"""Safety regression tests that never load the project's private config or data."""

import json
import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError


_POLICY_SALARY_QUESTIONS = (
    "固定岗多少钱？",
    "临时岗多少钱？",
    "固定岗位一个月多少钱？",
    "临时岗位一个小时给多少钱？",
    "固定岗能赚多少？",
    "临时岗每小时多少钱？",
    "固定岗位酬金标准是多少？",
    "临时岗位薪酬按什么标准计酬？",
    "固定/临时岗位报酬是多少？",
    "固定岗工资是多少？",
)

_WRITE_REQUESTS = (
    "帮我提交岗位3申请",
    "帮我报名岗位3",
    "撤销我的申请",
    "发布这个岗位",
    "关闭岗位招聘",
    "把岗位关掉",
    "下架这个岗位",
    "确认学生上岗",
    "安排某同学入职",
    "结束该岗位",
    "终止学生工作",
    "登记今天的工时",
    "更正工时记录",
    "核实工时",
    "给我做困难认定",
    "调整我的角色权限",
    "提升该学生权限",
    "变更用户身份",
    "把我改成管理员",
)

_MODEL_WRITE_CLAIMS = (
    "已为你提交了申请。",
    "已经替你报名岗位3。",
    "申请已撤销。",
    "岗位已发布。",
    "已经把岗位关掉了。",
    "已下架这个岗位。",
    "已确认学生上岗。",
    "已安排学生上岗。",
    "该学生已入职。",
    "已结束该岗位。",
    "已终止学生工作。",
    "工时已登记。",
    "已经更正工时记录。",
    "已核实工时。",
    "已完成困难认定。",
    "已调整该生角色权限。",
    "已将你设为管理员。",
)


def _fake_config(root):
    module = ModuleType("app.config")
    module.ROOT = root
    module.VERSION = "test"
    module.DATABASE_URL = f"sqlite:///{(root / 'isolated.db').as_posix()}"
    module.APP_ENV = "test"
    module.JWT_SECRET = "x" * 32
    module.TOKEN_HOURS = 12
    module.DEMO_PASSWORD = "Demo@2026"
    module.SKILLS = {"excel": "表格处理"}
    module.ROLE_NAMES = {"student": "学生", "unit": "用工单位", "aid": "资助中心", "admin": "系统管理员"}
    module.RULE_VERSION = "isolated-test"
    return module


def _business_snapshot(runtime, db):
    return {
        "counts": {model.__tablename__: db.scalar(select(func.count()).select_from(model))
                   for model in (runtime.Application, runtime.Job, runtime.WorkHour)},
        "applications": list(db.execute(select(
            runtime.Application.id, runtime.Application.status, runtime.Application.withdrawn_at,
        ).order_by(runtime.Application.id))),
        "jobs": list(db.execute(select(
            runtime.Job.id, runtime.Job.status, runtime.Job.close_reason, runtime.Job.published_at,
        ).order_by(runtime.Job.id))),
        "work_hours": list(db.execute(select(
            runtime.WorkHour.id, runtime.WorkHour.hours, runtime.WorkHour.previous_id,
            runtime.WorkHour.verified_by, runtime.WorkHour.verification_note,
        ).order_by(runtime.WorkHour.id))),
    }


@pytest.fixture(scope="module")
def isolated_runtime(tmp_path_factory):
    # The normal suite imports app.config in conftest. This module is purposely
    # run with --noconftest so no private project .env can be loaded first.
    if "app.config" in sys.modules:
        pytest.skip("run this isolation suite with --noconftest before app.config is imported")
    root = tmp_path_factory.mktemp("agent-safety")
    sys.modules["app.config"] = _fake_config(root)

    from app import agent, rag
    from app.database import Base, SessionLocal, engine, get_db
    from app.llm import ModelSettings
    from app.main import app
    from app.models import Application, Job, PolicyDoc, WorkHour
    from app.seed import seed_database
    from app.services import now

    yield SimpleNamespace(
        agent=agent,
        rag=rag,
        Base=Base,
        SessionLocal=SessionLocal,
        engine=engine,
        get_db=get_db,
        ModelSettings=ModelSettings,
        app=app,
        Application=Application,
        Job=Job,
        PolicyDoc=PolicyDoc,
        WorkHour=WorkHour,
        seed_database=seed_database,
        now=now,
        root=root,
    )
    app.dependency_overrides.clear()
    rag.close_indexes()
    engine.dispose()


def test_policy_questions_prefetch_verified_sources_not_simulated_jobs(isolated_runtime):
    agent = isolated_runtime.agent
    for question in (
        *_POLICY_SALARY_QUESTIONS,
        "固定岗工资怎么算？",
        "固定岗位工资怎么算？",
        "2025年学生助理有什么要求，现在还能报名吗？",
        "国家勤工助学每周工时限制",
        "那现在是多少呢？",
    ):
        assert agent._policy_intent(question, None)
        assert agent._business_requirement(question, None) == (None, None)

    assert not agent._policy_intent("帮我找固定岗位，时薪至少20元", None)
    assert agent._business_requirement("我有哪些可申请岗位", None)[0] == "search_jobs"
    assert not agent._policy_intent("图书馆 A区临时岗，时薪至少20元", None)
    assert agent._business_requirement("图书馆 A区临时岗，时薪至少20元", None)[0] == "search_jobs"
    assert agent._business_requirement("帮我找固定岗位，时薪至少20元", None)[0] == "search_jobs"
    assert not agent._write_intent("我申请的岗位有哪些要求？")
    assert not agent._write_intent("2025年学生助理有什么要求，现在还能报名吗？")


@pytest.mark.parametrize("question", _WRITE_REQUESTS)
def test_write_operation_requests_are_recognized(isolated_runtime, question):
    assert isolated_runtime.agent._write_intent(question)


def test_policy_questions_use_verified_sources_not_simulated_jobs(isolated_runtime, monkeypatch):
    runtime = isolated_runtime
    monkeypatch.setenv("RAG_ENABLED", "false")
    runtime.Base.metadata.create_all(runtime.engine)
    with runtime.SessionLocal.begin() as db:
        runtime.seed_database(db)
        db.add_all([
            runtime.PolicyDoc(
                title="固定岗位酬金说明", publisher="Fixture", source_url="https://example.invalid/fixed",
                version="2024", verified=True, is_school_policy=True,
                verified_at=runtime.now(), imported_at=runtime.now(),
                verification_note="isolated", source_key="FIXED-2024",
                sections=[{"location": "第一条", "text": "固定岗位的酬金应按学校核验规则说明。"}],
            ),
            runtime.PolicyDoc(
                title="临时岗位酬金说明", publisher="Fixture", source_url="https://example.invalid/temporary",
                version="2024", verified=True, is_school_policy=True,
                verified_at=runtime.now(), imported_at=runtime.now(),
                verification_note="isolated", source_key="TEMP-2024",
                sections=[{"location": "第二条", "text": "临时岗位的酬金应按学校核验规则说明。"}],
            ),
            runtime.PolicyDoc(
                title="2025年学生助理招聘公告", publisher="Fixture", source_url="https://example.invalid/history",
                version="2025", verified=True, verified_at=runtime.now(), imported_at=runtime.now(),
                verification_note="isolated", source_key="STUDENT-2025", usage_scope="archive_only",
                current_answer_allowed=False, expires_at=datetime(2025, 12, 31),
                sections=[{"location": "报名条件", "text": "学生助理招聘申请报名条件以该年度公告为准。"}],
            ),
        ])

    old_settings = runtime.agent.llm.get_settings
    runtime.agent.llm.get_settings = lambda: runtime.ModelSettings(False, "custom", "", "", "")
    client = TestClient(runtime.app)
    try:
        login = client.post("/api/auth/login", json={"username": "student", "password": "Demo@2026"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        salary_responses = [client.post("/api/agent/messages", headers=headers, json={
            "question": question, "conversation_id": f"salary-policy-{index}",
        }) for index, question in enumerate(_POLICY_SALARY_QUESTIONS)]
        history = client.post("/api/agent/messages", headers=headers, json={
            "question": "2025年学生助理有什么要求，现在还能报名吗？", "conversation_id": "history-policy",
        })
    finally:
        client.close()
        runtime.agent.llm.get_settings = old_settings

    for response in (*salary_responses, history):
        assert response.status_code == 200
        body = response.json()
        assert body["jobs"] == []
        assert body["tool_steps"][0]["name"] == "search_policies"
        assert all(step["name"] != "search_jobs" for step in body["tool_steps"])
    for response in salary_responses:
        body = response.json()
        assert body["citations"]
        assert "本校具体时薪/酬金未取得可靠数值" in body["answer"]
        assert body["answer_state"] == "policy_boundary"
    assert "固定岗位的酬金" in salary_responses[0].json()["answer"]
    assert "临时岗位的酬金" in salary_responses[1].json()["answer"]
    assert history.json()["citations"]
    assert "历史存档" in history.json()["answer"]
    assert history.json()["answer_state"] == "policy_boundary"


def test_policy_turn_rejects_model_attempt_to_query_simulated_jobs(isolated_runtime, monkeypatch):
    runtime = isolated_runtime
    monkeypatch.setenv("RAG_ENABLED", "false")
    runtime.Base.metadata.create_all(runtime.engine)
    with runtime.SessionLocal.begin() as db:
        runtime.seed_database(db)
        db.add(runtime.PolicyDoc(
            title="固定岗位酬金说明", publisher="Fixture", source_url="https://example.invalid/fixed",
            version="2024", verified=True, verified_at=runtime.now(), imported_at=runtime.now(),
            verification_note="isolated", source_key="FIXED-MODEL",
            sections=[{"location": "第一条", "text": "固定岗位的酬金应按学校核验规则说明。"}],
        ))
    candidate = runtime.ModelSettings(True, "ollama", "http://127.0.0.1:11434/v1", "fixture", "")
    verified = runtime.ModelSettings(True, "ollama", "http://127.0.0.1:11434/v1", "fixture", "",
                                     candidate.fingerprint)
    old_settings, old_complete = runtime.agent.llm.get_settings, runtime.agent.llm.complete
    runtime.agent.llm.get_settings = lambda: verified
    runtime.agent.llm.complete = lambda *_args, **_kwargs: {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "job-call", "type": "function",
            "function": {"name": "search_jobs", "arguments": '{"query":"固定岗位"}'},
        }],
    }
    client = TestClient(runtime.app)
    try:
        login = client.post("/api/auth/login", json={"username": "student", "password": "Demo@2026"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.post("/api/agent/messages", headers=headers,
                               json={"question": "固定岗多少钱？", "conversation_id": "policy-tool-only"})
    finally:
        client.close()
        runtime.agent.llm.get_settings, runtime.agent.llm.complete = old_settings, old_complete
    assert response.status_code == 200
    body = response.json()
    assert body["jobs"] == []
    assert all(step["name"] != "search_jobs" or step["status"] == "rejected" for step in body["tool_steps"])
    assert body["tool_steps"][-1]["status"] == "rejected"
    assert body["citations"] and "固定岗位的酬金" in body["answer"]


def test_context_requires_exact_verified_provider_identity(isolated_runtime):
    agent = isolated_runtime.agent
    identity = "deepseek|https://api.example.com/v1|model-a"
    offline = SimpleNamespace(response={"provider_identity": None, "business_context": {"month": "2026-09"}})
    fallback = SimpleNamespace(response={"provider_identity": None, "retrieval": {"mode": "keyword"}})
    old_unverified = SimpleNamespace(response={"provider_identity": identity})
    prior = SimpleNamespace(response={"provider_identity": identity, "provider_context_verified": True})

    assert agent._reusable_context(offline, identity) is None
    assert agent._reusable_context(fallback, identity) is None
    assert agent._reusable_context(old_unverified, identity) is None
    assert agent._reusable_context(prior, "deepseek|https://api.example.com/v1|model-b") is None
    assert agent._reusable_context(prior, identity) is prior
    assert agent._business_requirement("那我能拿多少钱？", None) == ("monthly_payroll", None)

    first = SimpleNamespace(response={"provider_identity": identity, "provider_context_verified": True})
    switched = SimpleNamespace(response={"provider_identity": "bailian|https://api.example.com/v1|model-b",
                                         "provider_context_verified": True})
    returned = SimpleNamespace(response={"provider_identity": identity, "provider_context_verified": True})
    assert agent._reusable_history([first, switched], identity) == []
    assert agent._reusable_history([first, switched, returned], identity) == [returned]


def test_free_form_qualitative_policy_claim_falls_back_to_source_text(isolated_runtime):
    agent = isolated_runtime.agent
    evidence = {
        "answer": "找到 1 段已核验原文，请结合条款上下文阅读。",
        "citations": [{
            "reference": "[1]",
            "text": "上岗前应当接受必要培训。",
            "location": "第二十一条",
            "source": {"title": "已核验资料"},
        }],
        "boundaries": [],
    }
    fabricated = "教育部要求单位购买商业保险。[1]"
    assert not agent._policy_numbers_supported(fabricated, evidence)
    answer = agent._policy_source_answer(evidence)
    assert "商业保险" not in answer
    assert "上岗前应当接受必要培训" in answer and "[1]" in answer


@pytest.mark.parametrize("question", _WRITE_REQUESTS)
def test_write_requests_use_one_centralized_refusal(isolated_runtime, question):
    assert isolated_runtime.agent._write_intent(question)


@pytest.mark.parametrize("answer", _MODEL_WRITE_CLAIMS)
def test_write_completion_claims_are_detected(isolated_runtime, answer):
    assert isolated_runtime.agent._write_claimed(answer)


@pytest.mark.parametrize("answer", ["该岗位已经关闭，不能申请。", "已结束的岗位不能报名。"])
def test_closed_job_status_claims_require_read_only_evidence(isolated_runtime, answer):
    agent = isolated_runtime.agent
    assert agent._write_claimed(answer)
    assert not agent._write_claimed(answer, [{"jobs": [{"status": "closed", "close_reason": "manual"}]}])
    assert agent._write_claimed(answer, [{"jobs": [{"status": "published"}]}])
    assert agent._write_claimed(answer, [{"jobs": [{"status": "closed"}, {"status": "published"}]}])


def test_write_requests_do_not_change_business_tables(isolated_runtime):
    runtime = isolated_runtime
    runtime.Base.metadata.create_all(runtime.engine)
    with runtime.SessionLocal.begin() as db:
        runtime.seed_database(db)
        before = _business_snapshot(runtime, db)

    old_settings = runtime.agent.llm.get_settings
    runtime.agent.llm.get_settings = lambda: runtime.ModelSettings(False, "custom", "", "", "")
    client = TestClient(runtime.app)
    try:
        login = client.post("/api/auth/login", json={"username": "student", "password": "Demo@2026"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        for number, question in enumerate(_WRITE_REQUESTS):
            response = client.post("/api/agent/messages", headers=headers,
                                   json={"question": question, "conversation_id": f"write-{number}"})
            assert response.status_code == 200
            assert "本次未执行写入，请到对应页面人工确认" in response.json()["answer"]
        with runtime.SessionLocal() as db:
            after = _business_snapshot(runtime, db)
        assert after == before
    finally:
        client.close()
        runtime.agent.llm.get_settings = old_settings


def test_offline_payroll_context_is_not_forwarded_to_a_verified_model(isolated_runtime):
    runtime = isolated_runtime
    runtime.Base.metadata.create_all(runtime.engine)
    with runtime.SessionLocal.begin() as db:
        runtime.seed_database(db)

    disabled = runtime.ModelSettings(False, "ollama", "http://127.0.0.1:11434/v1", "fixture", "")
    candidate = runtime.ModelSettings(True, "ollama", "http://127.0.0.1:11434/v1", "fixture", "")
    verified = runtime.ModelSettings(True, "ollama", "http://127.0.0.1:11434/v1", "fixture", "",
                                     candidate.fingerprint)
    old_settings, old_complete = runtime.agent.llm.get_settings, runtime.agent.llm.complete
    captured = []
    runtime.agent.llm.get_settings = lambda: disabled
    client = TestClient(runtime.app)
    try:
        login = client.post("/api/auth/login", json={"username": "student", "password": "Demo@2026"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        first = client.post("/api/agent/messages", headers=headers, json={
            "question": "查看我2026-09的工时与薪酬", "conversation_id": "offline-to-model",
        })
        assert first.status_code == 200
        assert first.json()["provider_identity"] is None

        runtime.agent.llm.get_settings = lambda: verified

        def complete(_settings, messages, *_args, **_kwargs):
            captured.extend(messages)
            return {"role": "assistant", "content": "请说明要查询的月份。"}

        runtime.agent.llm.complete = complete
        second = client.post("/api/agent/messages", headers=headers, json={
            "question": "那我能拿多少钱？", "conversation_id": "offline-to-model",
        })
        assert second.status_code == 200
        assert not second.json()["tool_steps"]
        assert "请说明要查哪个月份" in second.json()["answer"]
        assert not any(message.get("role") == "tool" for message in captured)
        assert not any("126.00" in str(message.get("content", "")) for message in captured)
    finally:
        client.close()
        runtime.agent.llm.get_settings, runtime.agent.llm.complete = old_settings, old_complete


def test_model_write_claims_are_replaced_without_business_change(isolated_runtime):
    runtime = isolated_runtime
    runtime.Base.metadata.create_all(runtime.engine)
    with runtime.SessionLocal.begin() as db:
        runtime.seed_database(db)
        before = _business_snapshot(runtime, db)

    candidate = runtime.ModelSettings(True, "ollama", "http://127.0.0.1:11434/v1", "fixture", "")
    verified = runtime.ModelSettings(True, "ollama", "http://127.0.0.1:11434/v1", "fixture", "",
                                     candidate.fingerprint)
    old_settings, old_complete = runtime.agent.llm.get_settings, runtime.agent.llm.complete
    runtime.agent.llm.get_settings = lambda: verified
    claims = iter(_MODEL_WRITE_CLAIMS)
    runtime.agent.llm.complete = lambda *_args, **_kwargs: {
        "role": "assistant", "content": next(claims),
    }
    client = TestClient(runtime.app)
    try:
        login = client.post("/api/auth/login", json={"username": "student", "password": "Demo@2026"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        for number, claim in enumerate(_MODEL_WRITE_CLAIMS):
            response = client.post("/api/agent/messages", headers=headers,
                                   json={"question": "我的申请状态", "conversation_id": f"model-write-{number}"})
            assert response.status_code == 200
            assert "本次未执行写入，请到对应页面人工确认" in response.json()["answer"]
            assert claim not in response.json()["answer"]
        with runtime.SessionLocal() as db:
            after = _business_snapshot(runtime, db)
        assert after == before
    finally:
        client.close()
        runtime.agent.llm.get_settings, runtime.agent.llm.complete = old_settings, old_complete


@pytest.mark.parametrize("answer", ["该岗位已经关闭，不能申请。", "已结束的岗位不能报名。"])
def test_model_may_describe_closed_job_when_read_only_tool_confirms_it(isolated_runtime, answer):
    runtime = isolated_runtime
    runtime.Base.metadata.create_all(runtime.engine)
    with runtime.SessionLocal.begin() as db:
        runtime.seed_database(db)
        closed_job_id = db.scalar(select(runtime.Job.id).where(runtime.Job.status == "closed").limit(1))
        assert closed_job_id is not None
        before = _business_snapshot(runtime, db)

    candidate = runtime.ModelSettings(True, "ollama", "http://127.0.0.1:11434/v1", "fixture", "")
    verified = runtime.ModelSettings(True, "ollama", "http://127.0.0.1:11434/v1", "fixture", "",
                                     candidate.fingerprint)
    old_settings, old_complete = runtime.agent.llm.get_settings, runtime.agent.llm.complete
    runtime.agent.llm.get_settings = lambda: verified
    model_calls = 0

    def complete(*_args, **_kwargs):
        nonlocal model_calls
        model_calls += 1
        if model_calls % 2:
            return {"role": "assistant", "content": None, "tool_calls": [{
                "id": "closed-job", "type": "function", "function": {
                    "name": "job_detail", "arguments": json.dumps({"job_id": closed_job_id}),
                },
            }]}
        return {"role": "assistant", "content": answer}

    runtime.agent.llm.complete = complete
    client = TestClient(runtime.app)
    try:
        login = client.post("/api/auth/login", json={"username": "student", "password": "Demo@2026"})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        response = client.post("/api/agent/messages", headers=headers,
                               json={"question": "查看岗位详情", "conversation_id": f"closed-status-{model_calls}"})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == answer
        assert body["mode"] == "model"
        assert any(step["name"] == "job_detail" and step["status"] == "success" for step in body["tool_steps"])
        with runtime.SessionLocal() as db:
            after = _business_snapshot(runtime, db)
        assert after == before
    finally:
        client.close()
        runtime.agent.llm.get_settings, runtime.agent.llm.complete = old_settings, old_complete


def test_deterministic_application_status_is_not_treated_as_a_model_write_claim(isolated_runtime):
    runtime = isolated_runtime
    runtime.Base.metadata.create_all(runtime.engine)
    with runtime.SessionLocal.begin() as db:
        runtime.seed_database(db)
    old_settings = runtime.agent.llm.get_settings
    runtime.agent.llm.get_settings = lambda: runtime.ModelSettings(False, "custom", "", "", "")
    client = TestClient(runtime.app)
    try:
        login = client.post("/api/auth/login", json={"username": "student", "password": "Demo@2026"})
        response = client.post("/api/agent/messages",
                               headers={"Authorization": f"Bearer {login.json()['access_token']}"},
                               json={"question": "我的申请状态", "conversation_id": "deterministic-status"})
        assert response.status_code == 200
        assert "本次未执行写入，请到对应页面人工确认" not in response.json()["answer"]
        assert response.json()["tool_steps"][0]["name"] == "application_progress"
    finally:
        client.close()
        runtime.agent.llm.get_settings = old_settings


def test_qdrant_point_count_mismatch_is_unavailable_and_degrades_to_keyword(isolated_runtime, monkeypatch, tmp_path):
    rag = isolated_runtime.rag
    settings = rag.Options(True, "fixture", tmp_path / "rag", tmp_path / "rag/models", 0.65)
    settings.model_cache.mkdir(parents=True)
    (settings.model_cache / "fixture.onnx").touch()
    instance = rag.LocalIndex(settings)
    instance.vectors_path.mkdir(parents=True)

    class Client:
        def collection_exists(self, _collection):
            return True

        def get_collection(self, _collection):
            return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=3))))

        def count(self, collection_name=None, exact=True):
            assert collection_name == "policy_fixture" and exact is True
            return SimpleNamespace(count=2)

        def close(self):
            pass

    metadata = {"collection": "policy_fixture", "dimension": 3, "chunks": 3}
    instance.client = Client()
    monkeypatch.setattr(instance, "manifest", lambda: metadata)
    status = instance.status()
    assert status["state"] == "unavailable"
    assert status["ready"] is False
    assert status["rebuild_required"] is True
    assert status["reason"] == "collection_count_mismatch"

    class BrokenIndex:
        def search(self, *_args, **_kwargs):
            raise rag.RagUnavailable("collection_count_mismatch")

    document = SimpleNamespace(id=1, title="Policy", sections=[{"location": "L1", "text": "verified source"}])
    monkeypatch.setattr(rag, "options", lambda: settings)
    monkeypatch.setattr(rag, "index", lambda: BrokenIndex())
    _hits, retrieval = rag.hybrid_search([document], {1}, "source", lambda _chunk: 1, limit=1)
    assert retrieval["mode"] == "keyword_fallback"
    assert retrieval["degraded_reason"] == "collection_count_mismatch"


def test_qdrant_count_read_and_query_failures_mark_index_unavailable(isolated_runtime, monkeypatch, tmp_path):
    rag = isolated_runtime.rag
    settings = rag.Options(True, "fixture", tmp_path / "rag", tmp_path / "rag/models", 0.65)
    settings.model_cache.mkdir(parents=True)
    (settings.model_cache / "fixture.onnx").touch()
    instance = rag.LocalIndex(settings)
    instance.vectors_path.mkdir(parents=True)
    metadata = {"collection": "policy_fixture", "dimension": 3, "chunks": 3}

    class ReadBrokenClient:
        def collection_exists(self, _collection):
            return True

        def get_collection(self, _collection):
            return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=3))))

        def count(self, *_args, **_kwargs):
            raise RuntimeError("fixture count failure")

        def close(self):
            pass

    instance.client = ReadBrokenClient()
    monkeypatch.setattr(instance, "manifest", lambda: metadata)
    read_status = instance.status()
    assert read_status["state"] == "unavailable"
    assert read_status["ready"] is False
    assert read_status["rebuild_required"] is True
    assert read_status["reason"] == "collection_unavailable"

    class QueryBrokenClient:
        def collection_exists(self, _collection):
            return True

        def get_collection(self, _collection):
            return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=SimpleNamespace(size=3))))

        def count(self, *_args, **_kwargs):
            return SimpleNamespace(count=3)

        def query_points(self, **_kwargs):
            raise RuntimeError("fixture query failure")

        def close(self):
            pass

    class Model:
        def query_embed(self, _query):
            yield SimpleNamespace(tolist=lambda: [1.0, 0.0, 0.0])

    instance.client = QueryBrokenClient()
    instance.last_error = None
    monkeypatch.setattr(instance, "refresh", lambda _chunks: metadata)
    monkeypatch.setattr(instance, "load_model", lambda: Model())
    with pytest.raises(rag.RagUnavailable, match="search_unavailable"):
        instance.search([], {"fixture-point"}, "query")
    query_status = instance.status()
    assert query_status["state"] == "unavailable"
    assert query_status["ready"] is False
    assert query_status["rebuild_required"] is True
    assert query_status["reason"] == "search_unavailable"


def test_database_operational_error_returns_safe_503(isolated_runtime):
    runtime = isolated_runtime

    def unavailable_db():
        raise OperationalError("SELECT password FROM users", {}, RuntimeError("mysql://secret@example.invalid"))

    client = TestClient(runtime.app, raise_server_exceptions=False)
    runtime.app.dependency_overrides[runtime.get_db] = unavailable_db
    try:
        response = client.post("/api/auth/login", json={"username": "student", "password": "Demo@2026"})
    finally:
        runtime.app.dependency_overrides.pop(runtime.get_db, None)
        client.close()
    assert response.status_code == 503
    assert response.json() == {"error": "unavailable", "message": "数据库暂不可用，请稍后重试。"}
    assert "SELECT" not in response.text and "secret" not in response.text


def test_database_integrity_error_remains_a_409_conflict(isolated_runtime):
    runtime = isolated_runtime

    def conflicting_db():
        raise IntegrityError("INSERT INTO application", {}, RuntimeError("duplicate key"))

    client = TestClient(runtime.app, raise_server_exceptions=False)
    runtime.app.dependency_overrides[runtime.get_db] = conflicting_db
    try:
        response = client.post("/api/auth/login", json={"username": "student", "password": "Demo@2026"})
    finally:
        runtime.app.dependency_overrides.pop(runtime.get_db, None)
        client.close()
    assert response.status_code == 409
    assert response.json() == {"error": "conflict", "message": "记录已存在或已被更新，请刷新后重试"}
