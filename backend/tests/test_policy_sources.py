"""Reviewed official sources are imported into disposable databases only."""

import json
import shutil
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import schemas as s
from app.migrate import REVISION, upgrade_schema
from app.models import AuditLog, ChatLog, PolicyDoc, SystemSetting, User
from app.policy import retrieve_policy
from app.policy_import import DEFAULT_MANIFEST, build_verified_documents, import_verified_documents
from app.services import now

DOCUMENTS = build_verified_documents()


@pytest.fixture
def reviewed_db(factory):
    with factory.begin() as db:
        import_verified_documents(db, DOCUMENTS)
    return factory


def test_import_checks_all_official_archives_and_attaches_scope_metadata():
    assert len(DOCUMENTS) == 14
    sources = {doc["source_key"]: doc for doc in DOCUMENTS}
    assert sources["S08"]["sections"][20]["location"] == "第二十一条"
    assert "原则上每周不超过8小时" in sources["S08"]["sections"][20]["text"]
    assert sources["S03"]["expires_at"].isoformat() == "2025-10-31T14:00:00"
    assert sources["S05"]["expires_at"].isoformat() == "2025-04-30T23:59:59.999999"
    assert sources["S07"]["usage_scope"] == "archive_only"
    assert not sources["S07"]["current_answer_allowed"]
    assert sources["S13"]["usage_scope"] == "labor_reference"
    assert sources["S13"]["effective_from"].isoformat() == "2025-12-01"
    assert sources["S13"]["sections"][-1]["source_url"].endswith(".png")
    assert "【边界】" not in sources["S01"]["sections"][15]["text"]


def test_official_explanation_is_split_at_question_boundaries():
    document = next(doc for doc in DOCUMENTS if doc["source_key"] == "S09")
    sections = document["sections"]

    assert len(sections) == 7
    assert sections[0]["location"] == "答记者问：导语"
    assert sections[1]["location"].startswith("答记者问：一、")
    assert sections[-1]["location"].startswith("答记者问：六、")
    assert max(map(lambda section: len(section["text"]), sections)) < 2000
    original = (DEFAULT_MANIFEST.parent / "curated/S09.txt").read_text(encoding="utf-8")
    body = original.split("【原文】", 1)[1]
    assert " ".join(" ".join(section["text"] for section in sections).split()) == " ".join(body.split())


def test_import_reuses_national_original_id_and_preserves_private_history(factory):
    with factory.begin() as db:
        original_id = db.scalar(select(PolicyDoc.id))
        student_id = db.scalar(select(User.id).where(User.username == "student"))
        db.add(ChatLog(user_id=student_id, conversation_id="before-import", question="原有私密提问",
                       response={"kind": "policy", "citations": [{"source": {"id": original_id}}]},
                       created_at=now()))
        db.flush()
        history = db.scalar(select(ChatLog).where(ChatLog.conversation_id == "before-import"))
        snapshot = (history.id, history.user_id, history.question, history.response, history.created_at)
        before_audits = db.scalar(select(func.count()).select_from(AuditLog))
        result = import_verified_documents(db, DOCUMENTS)
        assert result["created"] == 13 and result["updated"] == 1
        assert db.scalar(select(PolicyDoc.id).where(PolicyDoc.source_key == "S08")) == original_id
        assert db.scalar(select(func.count()).select_from(PolicyDoc)) == 14
        assert (history.id, history.user_id, history.question, history.response, history.created_at) == snapshot
        assert db.scalar(select(func.count()).select_from(AuditLog)) == before_audits + 1
        repeat = import_verified_documents(db, DOCUMENTS)
        assert repeat["unchanged"] == 14 and repeat["created"] == repeat["updated"] == 0
        assert db.scalar(select(func.count()).select_from(AuditLog)) == before_audits + 1


def test_changed_archive_fails_before_import(tmp_path):
    research = tmp_path / "research"
    shutil.copytree(DEFAULT_MANIFEST.parent, research)
    manifest = json.loads((research / "verified_sources.json").read_text(encoding="utf-8"))
    path = research / manifest["sources"][0]["curated_text_relative_path"]
    path.write_bytes(path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="digest mismatch"):
        build_verified_documents(research / "verified_sources.json")


@pytest.mark.parametrize("identity,updates", [
    ("S03", {"default_current_answer_allowed": True}),
    ("S07", {"rag_usage": "general_policy", "default_current_answer_allowed": True}),
    ("S13", {"rag_usage": "general_policy"}),
    ("S01", {"source_url": "https://example.com/pretend-official"}),
    ("S01", {"raw_relative_path": "../outside-file.html"}),
])
def test_import_rejects_wrong_scope_or_nonofficial_or_outside_sources(tmp_path, identity, updates):
    # Retain archive paths via a local copy so validation reaches the selected
    # record; only this temporary manifest is mutated.
    research = tmp_path / "research"
    shutil.copytree(DEFAULT_MANIFEST.parent, research)
    manifest = json.loads((research / "verified_sources.json").read_text(encoding="utf-8"))
    source = next(item for item in manifest["sources"] if item["id"] == identity)
    source.update(updates)
    target = research / "verified_sources.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        build_verified_documents(target)


def test_readonly_evidence_lookup_does_not_create_chat_records(reviewed_db):
    with reviewed_db.begin() as db:
        before = db.scalar(select(func.count()).select_from(ChatLog))
        result = retrieve_policy(db, "武汉设计工程学院有没有勤工助学制度？")
        assert result["citations"][0]["source"]["source_key"] == "S01"
        assert result["citations"][0]["location"] == "第十六条"
        assert "学校建立勤工助学制度" in result["citations"][0]["text"]
        assert db.scalar(select(func.count()).select_from(ChatLog)) == before


def test_topic_lookup_returns_only_the_relevant_official_explanation_chunk(reviewed_db):
    with reviewed_db.begin() as db:
        result = retrieve_policy(db, "教育部对寒暑假每周勤工时间怎么规定？")
        explanation = next(hit for hit in result["citations"] if hit["source"]["source_key"] == "S09")

        assert explanation["location"].startswith("答记者问：四、")
        assert "寒暑假" in explanation["text"] and "每周不超过8小时" in explanation["text"]
        assert "薪金标准" not in explanation["text"]


def test_school_salary_is_unknown_and_never_becomes_12_or_24(reviewed_db):
    with reviewed_db.begin() as db:
        result = retrieve_policy(db, "学校勤工助学一小时多少钱？")
        assert not result["citations"]
        assert "尚未确认本校具体岗位时薪" in result["answer"]
        assert "不能" in result["answer"] and "24" in result["answer"]
        old = retrieve_policy(db, "武汉设计工程学院目前藏龙美术馆工资多少钱？")
        assert not old["citations"]
        assert "尚未确认" in old["answer"]


def test_current_recruitment_cannot_retrieve_expired_historical_notices(reviewed_db):
    with reviewed_db.begin() as db:
        current = retrieve_policy(db, "武汉设计工程学院现在学生助理怎么报名？")
        assert not current["citations"]
        assert "当前仍可报名" in current["answer"] and "历史招聘" in current["answer"]
        excluded = {doc["source_key"]: doc["reason"] for doc in current["retrieval"]["excluded_sources"]}
        assert "历史" in excluded["S03"]
        old = retrieve_policy(db, "2025年学生助理有什么要求，现在还能报名吗？")
        assert old["citations"]
        assert {hit["source"]["source_key"] for hit in old["citations"]} == {"S03", "S04"}
        assert all(hit["source"]["past_deadline"] for hit in old["citations"])
        assert "已截止" in old["answer"]
        assert "4-8小时" in old["citations"][0]["text"]


def test_school_current_hardship_levels_do_not_use_the_old_two_level_policy(reviewed_db):
    with reviewed_db.begin() as db:
        result = retrieve_policy(db, "武汉设计工程学院当前困难认定有几档，需要民政盖章吗？")
        assert not result["citations"]
        assert "2019年旧版不能" in result["answer"]
        historical = retrieve_policy(db, "2019年武汉设计工程学院困难等级怎么规定？")
        assert {hit["source"]["source_key"] for hit in historical["citations"]} == {"S07"}
        assert any("两个等级" in hit["text"] for hit in historical["citations"])
        assert "现行细则" in historical["answer"]


def test_labor_minimum_wage_has_its_own_scope_and_attachment_citation(reviewed_db):
    with reviewed_db.begin() as db:
        ordinary = retrieve_policy(db, "临时岗位工资标准是多少？")
        assert ordinary["citations"][0]["source"]["source_key"] == "S08"
        assert all(hit["source"]["usage_scope"] != "labor_reference" for hit in ordinary["citations"])
        labor = retrieve_policy(db, "湖北省武汉最低工资标准及适用区域是什么？")
        assert any(hit["source"]["source_key"] == "S13" and "武汉市区" in hit["text"]
                   for hit in labor["citations"])
        assert "不是已确认的本校" in labor["answer"]
        attachment = next(hit for hit in labor["citations"] if hit["quote_source_url"].endswith(".png"))
        assert "24元" in attachment["text"]


def test_verified_contact_and_national_proof_cancellation_are_available(reviewed_db):
    with reviewed_db.begin() as db:
        contact = retrieve_policy(db, "我应该找哪个部门咨询？")
        assert contact["citations"][0]["source"]["source_key"] == "S02"
        assert "经济资助中心027-81733022" in contact["citations"][0]["text"]
        proof = retrieve_policy(db, "国家取消民政证明的规定是什么？")
        assert any(hit["source"]["source_key"] == "S11" and "书面承诺" in hit["text"]
                   for hit in proof["citations"])
        assert all(hit["source"]["source_key"] != "S07" for hit in proof["citations"])


def test_historical_followup_retains_scope_but_now_returns_to_current_evidence(reviewed_db):
    with reviewed_db.begin() as db:
        question = "2025年学生助理怎么报名？"
        first = retrieve_policy(db, question)
        assert first["citations"] and first["retrieval"]["archive_requested"]
        followup = retrieve_policy(db, "这个具体怎么规定？", previous=question)
        assert followup["retrieval"]["used_context"] and followup["retrieval"]["archive_requested"]
        assert {hit["source"]["source_key"] for hit in followup["citations"]} <= {"S03", "S04"}
        current = retrieve_policy(db, "那现在学生助理怎么报名？", previous=question)
        assert not current["citations"] and not current["retrieval"]["archive_requested"]


def test_future_effective_source_is_excluded_even_if_authentic_and_explicitly_requested(reviewed_db):
    with reviewed_db.begin() as db:
        doc = db.scalar(select(PolicyDoc).where(PolicyDoc.source_key == "S01"))
        doc.effective_from = now().date() + timedelta(days=1)
        result = retrieve_policy(db, "S01第16条")
        assert not result["citations"]
        assert any(item["source_key"] == "S01" and "执行日期" in item["reason"]
                   for item in result["retrieval"]["excluded_sources"])


def test_manual_archive_import_validation_cannot_mark_history_current():
    fields = {"title": "历史测试资料", "publisher": "测试机构", "source_url": "https://example.com/policy",
              "version": "2025", "verified_at": now(), "verification_note": "仅用于参数校验测试",
              "sections": [{"location": "正文", "text": "隔离测试原文，不作为正式政策"}],
              "usage_scope": "archive_only", "current_answer_allowed": True}
    with pytest.raises(ValueError, match="不能标记"):
        s.PolicyInput(**fields)


def test_0003_upgrade_of_actual_old_policy_table_preserves_every_original_field(tmp_path):
    target = create_engine(f"sqlite:///{(tmp_path / 'old-schema.db').as_posix()}")
    original = {"id": 23, "title": "迁移前原文", "publisher": "隔离测试发布者",
                "source_url": "https://example.com/original", "version": "test-original", "verified": 1,
                "verified_at": "2026-09-30 13:24:15.876543", "verification_note": "原核验备注不改变",
                "imported_at": "2026-09-30 13:25:16.123456",
                "sections": json.dumps([{"location": "第二十一条", "text": "原有正文"}], ensure_ascii=False),
                "is_school_policy": 0}
    try:
        with target.begin() as connection:
            connection.execute(text("""CREATE TABLE policy_doc (
                id INTEGER NOT NULL PRIMARY KEY, title VARCHAR(200) NOT NULL, publisher VARCHAR(100) NOT NULL,
                source_url VARCHAR(500) NOT NULL, version VARCHAR(100) NOT NULL, verified BOOLEAN NOT NULL,
                verified_at DATETIME, verification_note TEXT NOT NULL, imported_at DATETIME NOT NULL,
                sections JSON NOT NULL, is_school_policy BOOLEAN NOT NULL)"""))
            columns = ", ".join(original)
            values = ", ".join(f":{name}" for name in original)
            connection.execute(text(f"INSERT INTO policy_doc ({columns}) VALUES ({values})"), original)
        assert upgrade_schema(target) == "0003" == REVISION
        with target.connect() as connection:
            after = dict(connection.execute(text("SELECT * FROM policy_doc WHERE id = 23")).mappings().one())
            assert {name: after[name] for name in original} == original
            assert after["usage_scope"] == "general_policy" and after["current_answer_allowed"] == 1
            assert after["source_key"] is None and after["source_metadata"] is None
        assert upgrade_schema(target) == REVISION
        indexes = inspect(target).get_indexes("policy_doc")
        assert any(index["unique"] and index["column_names"] == ["source_key"] for index in indexes)
        with sessionmaker(bind=target)() as db:
            assert db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version")).value == {
                "revision": "0003"}
    finally:
        target.dispose()


def test_0003_upgrade_of_actual_mysql_old_table_is_repeatable(factory):
    target = factory.kw["bind"]
    if target.dialect.name != "mysql":
        pytest.skip("Requires the guarded temporary MySQL verification instance")
    with factory.begin() as db:
        revision = db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version"))
        revision.value = {**revision.value, "revision": "0002"}
    PolicyDoc.__table__.drop(target)
    with target.begin() as connection:
        connection.execute(text("""CREATE TABLE policy_doc (
            id INTEGER NOT NULL PRIMARY KEY AUTO_INCREMENT, title VARCHAR(200) NOT NULL,
            publisher VARCHAR(100) NOT NULL, source_url VARCHAR(500) NOT NULL, version VARCHAR(100) NOT NULL,
            verified BOOLEAN NOT NULL, verified_at DATETIME(6), verification_note TEXT NOT NULL,
            imported_at DATETIME(6) NOT NULL, sections JSON NOT NULL, is_school_policy BOOLEAN NOT NULL
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs"""))
        connection.execute(text("INSERT INTO policy_doc (id,title,publisher,source_url,version,verified,"
                                "verified_at,verification_note,imported_at,sections,is_school_policy) VALUES "
                                "(42,'原有文档','隔离测试','https://example.com/original','fixture',1,"
                                "'2026-09-30 13:24:15.876543','原有核验备注','2026-09-30 13:25:16.123456',"
                                "'[{\"location\":\"第二十一条\",\"text\":\"原有正文\"}]',0)"))
    assert upgrade_schema(target) == REVISION
    with factory() as db:
        original = db.get(PolicyDoc, 42)
        assert original.title == "原有文档" and original.verified_at.microsecond == 876543
        assert original.sections == [{"location": "第二十一条", "text": "原有正文"}]
        assert original.source_key is None and original.current_answer_allowed
    assert upgrade_schema(target) == REVISION
    with factory.begin() as db:
        original = db.get(PolicyDoc, 42)
        original.source_key = "fixture"
        duplicate = PolicyDoc(title="重复测试", publisher="隔离测试", source_url="https://example.com/other",
                              version="fixture", verified=True, verified_at=now(), imported_at=now(),
                              verification_note="重复编号必须由唯一索引拒绝", sections=[], source_key="fixture")
        db.add(duplicate)
        with pytest.raises(IntegrityError, match="Duplicate entry"):
            db.flush()
        db.rollback()
