import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

from app import seed


def test_reset_closes_backup_connections_before_removing_windows_database(tmp_path, monkeypatch):
    target = tmp_path / "data/campus_demo.db"
    target.parent.mkdir()
    with closing(sqlite3.connect(target)) as connection:
        connection.execute("CREATE TABLE preserved (value TEXT NOT NULL)")
        connection.execute("INSERT INTO preserved VALUES ('test-only data')")
        connection.commit()

    monkeypatch.setattr(seed.config, "ROOT", tmp_path)
    monkeypatch.setattr(seed.config, "DATABASE_URL", f"sqlite:///{target.as_posix()}")
    monkeypatch.setattr(seed.config, "APP_ENV", "demo")
    # This test never opens, disposes, or resets the application's demonstration database.
    monkeypatch.setattr(seed, "engine", SimpleNamespace(dispose=lambda: None))

    seed.reset_demo("campus-demo")

    assert not target.exists()  # Windows refuses this unlink if the backup source is still open.
    backups = list((tmp_path / "work/backups").glob("*.db"))
    assert len(backups) == 1
    with closing(sqlite3.connect(backups[0])) as connection:
        assert connection.execute("SELECT value FROM preserved").fetchone()[0] == "test-only data"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    log = json.loads((tmp_path / "work/reset-log.jsonl").read_text(encoding="utf-8"))
    assert log["target"] == str(target.resolve())
