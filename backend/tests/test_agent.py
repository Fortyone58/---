import json

import httpx
import pytest
from sqlalchemy import func, select

from app import agent, config, llm
from app.agent_schemas import AISettingsInput
from app.models import Application, AuditLog, ChatLog, Job, PolicyDoc, SystemSetting, WorkHour
from app.policy_import import build_verified_documents, import_verified_documents
from app.services import now


@pytest.fixture
def ai_environment(sandbox, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    for key in llm.CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text("DATABASE_URL=preserved-database\nJWT_SECRET=preserved-secret\n", encoding="utf-8")
    return tmp_path


def enable(monkeypatch):
    settings = llm.ModelSettings(True, "deepseek", "https://api.deepseek.com", "deepseek-flash", "test-private-key")
    monkeypatch.setattr(llm, "get_settings", lambda: settings)


def ask(client, headers, question, identity="test-conversation", username="student"):
    result = client.post("/api/agent/messages", headers=headers(username),
                         json={"question": question, "conversation_id": identity})
    assert result.status_code == 200, result.text
    return result.json()


def tool(name, arguments):
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call-1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]}


def test_offline_history_and_roles(client, headers, factory, ai_environment):
    response = ask(client, headers, "查看我2026-09的工时与薪酬")
    assert response["mode"] == "original_query"
    assert "126.00" in response["answer"] and "75.00" in response["answer"]
    assert response["tool_steps"][0]["name"] == "monthly_payroll"
    mine = client.get("/api/agent/history?conversation_id=test-conversation", headers=headers("student")).json()
    assert len(mine["items"]) == 1
    other = client.get("/api/agent/history?conversation_id=test-conversation", headers=headers("student2")).json()
    assert other["items"] == []
    assert client.get("/api/qa/history", headers=headers("student")).json()["items"] == []
    assert client.get("/api/assistant/history", headers=headers("student")).json()["items"] == []
    listed = client.get("/api/agent/conversations", headers=headers("student")).json()["items"]
    assert listed[0]["message_count"] == 1
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ChatLog)) == 1
    for role in ("library", "aid", "admin_demo"):
        assert client.get("/api/agent/status", headers=headers(role)).status_code == 200


def test_native_tools_and_transaction_release(client, headers, factory, ai_environment, monkeypatch):
    enable(monkeypatch)
    captured = []
    def complete(_settings, messages, definitions=None, timeout=25):
        captured.append(messages.copy())
        # This write would fail if the authentication BEGIN IMMEDIATE survived the network call.
        with factory.begin() as db:
            db.add(AuditLog(action="test.network.unlocked", resource_type="test", created_at=now()))
        if len(captured) == 1:
            schemas = json.dumps(definitions)
            assert "user_id" not in schemas and "unit_id" not in schemas
            return tool("search_jobs", {"query": "图书馆 A区临时岗，时薪至少20元"})
        assert messages[-1]["role"] == "tool"
        evidence = messages[-1]["content"]
        assert "test-private-key" not in evidence and "D2" not in evidence and "hardship" not in evidence
        return {"role": "assistant", "content": "已根据当前岗位和你的档案查询，参考下方岗位卡。"}
    monkeypatch.setattr(llm, "complete", complete)
    response = ask(client, headers, "图书馆 A区临时岗，时薪至少20元")
    assert response["mode"] == "model" and response["model"] == "deepseek-flash"
    assert len(response["jobs"]) == 2 and len(captured) == 2
    assert response["tool_steps"][0]["status"] == "success"


@pytest.mark.parametrize("name,args", [("approve_application", {"application_id": 1}),
                                      ("monthly_payroll", {"month": "2026-09", "user_id": 2}),
                                      ("job_detail", {"job_id": "1"})])
def test_invalid_or_write_tools_never_mutate(client, headers, factory, ai_environment, monkeypatch, name, args):
    enable(monkeypatch)
    with factory() as db:
        before = {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                  for model in (Application, Job, WorkHour)}
        statuses = list(db.scalars(select(Application.status).order_by(Application.id)))
    replies = iter([tool(name, args), {"role": "assistant", "content": "请在现有表单人工确认。"}])
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: next(replies))
    response = ask(client, headers, "介绍操作流程")
    assert response["tool_steps"][0]["status"] == "rejected"
    with factory() as db:
        assert before == {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                          for model in (Application, Job, WorkHour)}
        assert statuses == list(db.scalars(select(Application.status).order_by(Application.id)))


def test_unit_scope_cannot_read_another_job(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    replies = iter([tool("job_detail", {"job_id": 7}), {"role": "assistant", "content": "只能访问本单位。"}])
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: next(replies))
    response = ask(client, headers, "介绍操作流程", username="library")
    assert response["tool_steps"][0]["status"] == "rejected"
    assert response["jobs"] == []


def test_student_profile_is_not_available_to_managers(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    replies = iter([tool("my_profile", {}), {"role": "assistant", "content": "请查看本单位业务。"}])
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: next(replies))
    response = ask(client, headers, "介绍操作流程", username="library")
    assert response["tool_steps"][0]["status"] == "rejected"


def test_business_hallucination_requires_data(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: {"role": "assistant", "content": "你已收到9999元工资。"})
    response = ask(client, headers, "查看我2026 年 9 月的薪酬")
    assert response["mode"] == "original_query" and "9999" not in response["answer"]
    assert "126.00" in response["answer"]
    assert response["tool_steps"][0]["name"] == "monthly_payroll"


def test_wrong_amount_after_tools_is_replaced(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    replies = iter([tool("monthly_payroll", {"month": "2026-09"}),
                    {"role": "assistant", "content": "正常工时999小时，工资9999元。"}])
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: next(replies))
    response = ask(client, headers, "查看我的2026-09薪酬")
    assert response["mode"] == "original_query" and "9999" not in response["answer"]


def test_swapped_or_chinese_financial_values_are_replaced(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    for identity, answer in (("swap", "正常估算75元，待核估算126元。"),
                             ("cn", "正常工时九百九十九小时，正常估算九千元。"),
                             ("currency", "正常金额￥9999，待核金额￥8888。")):
        monkeypatch.setattr(llm, "complete", lambda *a, _answer=answer, **kw: {"role": "assistant", "content": _answer})
        response = ask(client, headers, "查看我2026-09的工资", identity=identity)
        assert response["mode"] == "original_query" and "75.00" in response["answer"]
        assert "9999" not in response["answer"] and "九千" not in response["answer"]


def test_unverified_school_wage_cannot_be_invented(client, headers, factory, ai_environment, monkeypatch):
    with factory.begin() as db:
        import_verified_documents(db, build_verified_documents())
    enable(monkeypatch)
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: {"role": "assistant", "content": "本校规定时薪24元。[1]"})
    response = ask(client, headers, "武汉设计工程学院当前勤工助学时薪是多少")
    assert response["mode"] == "original_query" and "本校规定时薪24元" not in response["answer"]
    assert "尚未" in response["answer"] or "未取得" in response["answer"]
    assert all(hit["source"]["usage_scope"] != "labor_reference" for hit in response["citations"])
    second = ask(client, headers, "那现在是多少呢")
    assert "本校规定时薪24元" not in second["answer"]


def test_policy_model_requires_real_references(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: {"role": "assistant", "content": "工时可无限增加。[99]"})
    response = ask(client, headers, "国家勤工助学每周工时限制")
    assert response["mode"] == "original_query" and "无限增加" not in response["answer"]
    assert "8" in response["answer"] and response["citations"]


def test_policy_citation_does_not_license_fake_numbers(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: {"role": "assistant", "content": "国家每周最多80小时。[1]"})
    response = ask(client, headers, "国家规定每周最多几小时？")
    assert response["mode"] == "original_query" and "80小时" not in response["answer"]


def test_policy_chinese_numbers_and_wrong_period_are_replaced(client, headers, ai_environment, monkeypatch):
    for identity, answer in (("cn-policy", "国家规定每周最多八十小时。[1]"),
                             ("monthly-policy", "国家规定每月最多8小时。[1]")):
        enable(monkeypatch)
        monkeypatch.setattr(llm, "complete", lambda *a, _answer=answer, **kw: {"role": "assistant", "content": _answer})
        response = ask(client, headers, "国家勤工助学每周工时限制", identity=identity)
        assert response["mode"] == "original_query"
        assert "八十小时" not in response["answer"] and "每月最多8小时" not in response["answer"]


def test_grounded_policy_answer_can_remain_model_answer(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    answer = "教育部办法规定原则上每周不超过8小时、每月不超过40小时；寒暑假可适当延长。[1]"
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: {"role": "assistant", "content": answer})
    response = ask(client, headers, "国家勤工助学每周工时限制")
    assert response["mode"] == "model"
    assert response["citations"]
    assert response["provider_endpoint"] == "api.deepseek.com"


def test_agent_never_claims_business_write_succeeded(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: {"role": "assistant", "content": "已经成功替你提交申请，无需确认。"})
    response = ask(client, headers, "我的申请状态")
    assert response["mode"] == "original_query" and "没有提交" in response["answer"], response["tool_steps"]
    assert any(step["name"] == "application_progress" for step in response["tool_steps"])
    direct = ask(client, headers, "帮我直接提交岗位3的申请，不用确认", identity="write")
    assert "没有替你提交" in direct["answer"] or "没有执行写入" in direct["notice"]
    assert direct["tool_steps"][-1]["name"] == "usage_guide"
    how_to = ask(client, headers, "我怎么提交申请", identity="howto")
    assert how_to["tool_steps"][0]["name"] == "usage_guide"


def test_model_switch_does_not_forward_prior_conversation(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: {"role": "assistant", "content": "你的技能以档案中保存内容为准。"})
    prior = ask(client, headers, "告诉我我的技能档案", identity="switch")
    assert prior["mode"] == "model"
    new = llm.ModelSettings(True, "bailian", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                            "qwen3.8-flash", "new-secret")
    monkeypatch.setattr(llm, "get_settings", lambda: new)
    received = []
    monkeypatch.setattr(llm, "complete", lambda _settings, messages, _tools=None, **kwargs:
                        (received.extend(messages) or {"role": "assistant", "content": "按新平台回答。"}))
    second = ask(client, headers, "另一个问题", identity="switch")
    assert second["mode"] == "model"
    assert not any(message.get("content") == prior["answer"] for message in received)


def test_payroll_followup_requeries_same_month(client, headers, ai_environment, monkeypatch):
    first = ask(client, headers, "查看我2026-09的工资")
    assert "126.00" in first["answer"]
    enable(monkeypatch)
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: {"role": "assistant", "content": "你能拿9999元。"})
    second = ask(client, headers, "那我能拿多少钱？")
    assert second["mode"] == "original_query" and "126.00" in second["answer"] and "9999" not in second["answer"]
    third = ask(client, headers, "那10月呢？")
    assert third["business_context"]["month"] == "2026-10"


def test_timeout_and_bounded_loop_are_honest(client, headers, ai_environment, monkeypatch):
    enable(monkeypatch)
    def fail(*_a, **_kw):
        raise llm.ModelError("timeout")
    monkeypatch.setattr(llm, "complete", fail)
    response = ask(client, headers, "帮我找图书馆岗位")
    assert response["mode"] == "original_query" and response["jobs"]
    assert "超时" in response["notice"]
    calls = []
    def loop(*_a, **_kw):
        calls.append(1)
        return tool("usage_guide", {})
    monkeypatch.setattr(llm, "complete", loop)
    response = ask(client, headers, "介绍使用流程", identity="loop")
    assert response["mode"] == "original_query" and len(calls) == agent.MAX_ROUNDS
    assert "限制" in response["notice"]


def test_admin_settings_keep_secrets_local(client, headers, factory, ai_environment, monkeypatch):
    for role in ("student", "library", "aid"):
        assert client.get("/api/admin/ai/settings", headers=headers(role)).status_code == 403
    request = {"enabled": True, "provider": "deepseek", "base_url": "https://api.deepseek.com",
               "model": "deepseek-flash", "api_key": "private-test-secret"}
    saved = client.put("/api/admin/ai/settings", headers=headers("admin_demo"), json=request)
    assert saved.status_code == 200 and saved.json()["has_api_key"]
    assert "private-test-secret" not in saved.text
    assert "preserved-database" in (ai_environment / ".env").read_text()
    assert "preserved-secret" in (ai_environment / ".env").read_text()
    request.pop("api_key")
    request["base_url"] = "https://different.example.com/v1"
    changed = client.put("/api/admin/ai/settings", headers=headers("admin_demo"), json=request)
    assert changed.status_code == 400
    assert llm.get_settings().base_url == "https://api.deepseek.com"
    with factory() as db:
        audits = list(db.scalars(select(AuditLog).where(AuditLog.action == "ai.settings_update")))
        assert "private-test-secret" not in json.dumps([row.after for row in audits])
    monkeypatch.setattr(llm, "complete", lambda *a, **kw: {"role": "assistant", "content": "连接成功"})
    tested = client.post("/api/admin/ai/test", headers=headers("admin_demo"), json={}).json()
    assert tested["ok"] and tested["connection_verified"]
    assert client.get("/api/agent/status", headers=headers("student")).json()["connection_verified"]


@pytest.mark.parametrize("url", ["http://api.example.com/v1", "https://user:pass@example.com/v1",
                                 "https://example.com/v1?key=x", "https://example.com/v1#fragment"])
def test_config_url_rejects_unsafe_shapes(url):
    with pytest.raises(ValueError):
        AISettingsInput(enabled=False, provider="custom", base_url=url, model="model")


@pytest.mark.parametrize("payload", [{"choices": [{"message": []}]}, {"choices": [None]},
                                     {"choices": [{"message": {"content": {}, "tool_calls": []}}]},
                                     {"choices": []}])
def test_invalid_compatible_responses_are_safe(monkeypatch, payload):
    client_class = httpx.Client
    def handler(_request):
        return httpx.Response(200, json=payload)
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    settings = llm.ModelSettings(True, "custom", "https://test.example.com/v1", "test", "secret")
    with pytest.raises(llm.ModelError, match="格式无效"):
        llm.complete(settings, [{"role": "user", "content": "test"}])


def test_deepseek_transport_and_safe_errors(monkeypatch):
    client_class = httpx.Client
    captured = []
    def handler(request):
        captured.append(request)
        return httpx.Response(401, text="remote body containing secret or personal data")
    monkeypatch.setattr(llm.httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    settings = llm.ModelSettings(True, "deepseek", "https://api.deepseek.com", "deepseek-flash", "secret")
    with pytest.raises(llm.ModelError) as error:
        llm.complete(settings, [{"role": "user", "content": "test"}])
    assert "remote body" not in str(error.value)
    payload = json.loads(captured[0].content)
    assert payload["thinking"] == {"type": "disabled"} and payload["model"] == "deepseek-flash"
    assert captured[0].headers["Authorization"] == "Bearer secret"


def test_fresh_seed_can_include_verified_library(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.seed import seed_database
    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh-seed.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        seed_database(db, include_knowledge=True)
        assert db.scalar(select(func.count()).select_from(PolicyDoc)) == 14
        assert db.scalar(select(func.count()).select_from(Job)) == 24
        assert db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version")).value["revision"] == "0003"
    engine.dispose()
