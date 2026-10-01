import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, inspect, select, text

from app.database import Base
from app.migrate import REVISION, upgrade_schema
from app.models import ChatLog, Job, SystemSetting, User, WorkHour

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from migrate_to_mysql import copy_rows, table_manifest  # noqa: E402
from mysql_data import _reset_tables_and_seed, reset  # noqa: E402
from mysql_runtime import MARKER, ROOT, Runtime, check_owner, settings  # noqa: E402


def test_transfer_preserves_full_seed_and_microsecond_json_history(factory, tmp_path):
    target = factory.kw["bind"]
    source = create_engine(f"sqlite:///{(tmp_path / 'source.db').as_posix()}")
    Base.metadata.create_all(source)
    with target.connect() as original, source.begin() as snapshot:
        for table in Base.metadata.sorted_tables:
            rows = list(original.execute(select(table).order_by(table.c.id)).mappings())
            if rows:
                snapshot.execute(table.insert(), [dict(row) for row in rows])
    timestamp = datetime(2026, 9, 30, 17, 12, 45, 876543)
    with source.begin() as connection:
        student_id = connection.scalar(select(User.id).where(User.username == "student"))
        connection.execute(ChatLog.__table__.insert(), {"id": 40, "user_id": student_id,
                           "conversation_id": "migration-evidence", "question": "学校标准是什么？",
                           "response": {"kind": "policy", "text": "原文依据", "nested": [None, True, "中文"]},
                           "created_at": timestamp})
        connection.execute(Job.__table__.update().where(Job.id == 1).values(wage=Decimal("18.37")))
    try:
        Base.metadata.drop_all(target)
        Base.metadata.create_all(target)
        evidence = copy_rows(source, target)
        assert all(item["identical"] for item in evidence.values())
        assert table_manifest(source) == table_manifest(target)
        with target.connect() as connection:
            assert connection.scalar(select(ChatLog.created_at).where(ChatLog.id == 40)) == timestamp
            assert connection.scalar(select(Job.wage).where(Job.id == 1)) == Decimal("18.37")
        preserved = table_manifest(target)
        with pytest.raises(RuntimeError, match="not empty"):
            copy_rows(source, target)
        assert table_manifest(target) == preserved
    finally:
        source.dispose()


def test_revision_upgrade_is_idempotent_and_preserves_existing_records(factory):
    engine = factory.kw["bind"]
    with factory.begin() as db:
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version"))
        row.value = {**row.value, "revision": "0001", "evidence": {"kept": True}}
    before = table_manifest(engine)
    assert upgrade_schema(engine) == REVISION
    after = table_manifest(engine)
    assert {key: value for key, value in before.items() if key != "system_setting"} == {
        key: value for key, value in after.items() if key != "system_setting"}
    with factory() as db:
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version"))
        assert row.value["revision"] == REVISION
        assert row.value["evidence"] == {"kept": True}
    assert upgrade_schema(engine) == REVISION
    assert table_manifest(engine) == after


def test_unknown_revision_refuses_all_schema_changes(factory):
    engine = factory.kw["bind"]
    with factory.begin() as db:
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version"))
        row.value = {"revision": "future-9999"}
    Base.metadata.tables["chat_log"].drop(engine)
    with pytest.raises(RuntimeError, match="Unknown schema revision"):
        upgrade_schema(engine)
    assert not inspect(engine).has_table("chat_log")
    with factory() as db:
        assert db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version")).value == {
            "revision": "future-9999"}


def test_old_mysql_datetime_upgrade_keeps_records_and_accepts_microseconds(factory):
    engine = factory.kw["bind"]
    if engine.dialect.name != "mysql":
        pytest.skip("Requires the guarded MySQL verification instance")
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users MODIFY COLUMN created_at DATETIME NOT NULL"))
        connection.execute(text("ALTER TABLE application MODIFY COLUMN finished_at DATETIME NULL"))
    before = table_manifest(engine)
    upgrade_schema(engine)
    after = table_manifest(engine)
    assert before == after  # The fixture already has the current revision.
    for table in Base.metadata.sorted_tables:
        actual = {col["name"]: col["type"] for col in inspect(engine).get_columns(table.name)}
        for column in table.columns:
            if column.type.__class__.__name__ == "DateTime":
                assert actual[column.name].fsp == 6
    timestamp = datetime(2026, 10, 1, 10, 20, 30, 876543)
    with engine.begin() as connection:
        connection.execute(User.__table__.update().where(User.username == "student").values(created_at=timestamp))
    with factory() as db:
        assert db.scalar(select(User.created_at).where(User.username == "student")) == timestamp


def test_mysql_demo_reset_restores_seed_and_self_linked_hour_history(factory):
    engine = factory.kw["bind"]
    if engine.dialect.name != "mysql":
        pytest.skip("Requires the guarded MySQL verification instance")
    with factory.begin() as db:
        student_id = db.scalar(select(User.id).where(User.username == "student"))
        db.add(ChatLog(user_id=student_id, conversation_id="reset-only", question="temporary",
                       response={"kind": "policy"}, created_at=datetime(2026, 10, 1)))
        db.scalar(select(Job).where(Job.id == 1)).wage = Decimal("999.00")
    result = _reset_tables_and_seed(engine, include_bootstrap=False)
    assert result["status"] == "created"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(ChatLog)) == 0
        assert db.scalar(select(func.count()).select_from(WorkHour)) == 8
        assert db.scalar(select(func.count()).select_from(Job)) == 24
        assert db.scalar(select(Job.wage).where(Job.id == 1)) == Decimal("18.00")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT @@SESSION.foreign_key_checks")) == 1


def test_mysql_reset_rejects_without_confirmation_before_database_access(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("Database must not be accessed")
    monkeypatch.setattr("mysql_data.managed", refused)
    with pytest.raises(RuntimeError, match="confirmation"):
        reset(confirm="")


def test_managed_configuration_rejects_existing_mysql_service(tmp_path):
    config = tmp_path / "foreign.env"
    config.write_text("QINGHE_MYSQL_MANAGED=1\n"
                      "DATABASE_URL=mysql+pymysql://someone:unused@127.0.0.1:3306/existing\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="dedicated"):
        settings(config)
    config.write_text("APP_ENV=demo\nDATABASE_URL=sqlite:///unused.db\n", encoding="utf-8")
    assert settings(config) is None


def test_managed_owner_compares_canonical_data_paths(tmp_path):
    runtime = Runtime(tmp_path / "alias/../runtime", tmp_path / "mysqld.exe", "qinghe_sol", "unused", "unused", "unused")
    runtime.data.mkdir(parents=True)
    runtime.owner_file.write_text(json.dumps({"marker": MARKER, "project": str(ROOT),
                                             "data": str(runtime.data.resolve()), "port": runtime.port}),
                                  encoding="utf-8")
    assert check_owner(runtime)["marker"] == MARKER
