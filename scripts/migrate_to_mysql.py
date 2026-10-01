"""Preserve the existing SQLite demo in a new managed MySQL database."""

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values, set_key
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url

from mysql_data import backup as mysql_backup
from mysql_runtime import PORT, ROOT, listening, start, stop

sys.path.insert(0, str(ROOT / "backend"))
from app import models  # noqa: E402,F401
from app.database import Base, engine_options  # noqa: E402
from app.migrate import upgrade_schema  # noqa: E402


def normalized(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def digest(rows):
    serialized = json.dumps(normalized(rows), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def copy_rows(source_engine, target_engine):
    """Require an empty destination; preserve IDs, JSON, Decimal and timestamps."""
    tables = Base.metadata.sorted_tables
    evidence = {}
    with source_engine.connect() as source, target_engine.begin() as target:
        for table in tables:
            if target.scalar(select(func.count()).select_from(table)):
                raise RuntimeError("Destination is not empty; no rows were overwritten.")
        for table in tables:
            original = [dict(row) for row in source.execute(select(table).order_by(table.c.id)).mappings()]
            for offset in range(0, len(original), 200):
                target.execute(table.insert(), original[offset:offset + 200])
            migrated = [dict(row) for row in target.execute(select(table).order_by(table.c.id)).mappings()]
            before_hash, after_hash = digest(original), digest(migrated)
            if before_hash != after_hash:
                raise RuntimeError(f"Migration comparison failed for {table.name}; inserts rolled back.")
            evidence[table.name] = {"rows": len(original), "source_sha256": before_hash,
                                    "target_sha256": after_hash, "identical": True}
    return evidence


def table_manifest(engine):
    with engine.connect() as connection:
        return {table.name: {"rows": connection.scalar(select(func.count()).select_from(table)),
                             "sha256": digest([dict(row) for row in connection.execute(
                                 select(table).order_by(table.c.id)).mappings()])}
                for table in Base.metadata.sorted_tables}


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--mysqld", type=Path,
                        default=Path(r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe"))
    args = parser.parse_args()
    current = dotenv_values(ROOT / ".env")
    url = make_url(current["DATABASE_URL"])
    if url.get_backend_name() != "sqlite":
        raise RuntimeError("Migration requires the current SQLite demo; destination is never overwritten.")
    source_path = Path(url.database).resolve()
    if current.get("APP_ENV") != "demo" or source_path != (ROOT / "data/campus_demo.db").resolve():
        raise RuntimeError("Only this project's SQLite demonstration file can be migrated.")
    if listening(8000):
        raise RuntimeError("Stop the application's backend before taking the migration snapshot.")
    if listening(PORT):
        raise RuntimeError("Managed MySQL port 13308 is occupied; migration refused.")
    tag = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    snapshots = ROOT / "work/backups" / f"sqlite-to-mysql-{tag}"
    snapshots.mkdir(parents=True)
    source_backup = snapshots / "campus_demo.db"
    with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(source_backup)) as target:
        source.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Source SQLite backup is not valid.")
        if target.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Source SQLite contains invalid references.")
    shutil.copy2(ROOT / ".env", snapshots / "original.env")
    pending = snapshots / "mysql.env"
    shutil.copy2(ROOT / ".env", pending)
    root_id = hashlib.sha256(str(ROOT).lower().encode("utf-8")).hexdigest()[:12]
    runtime_dir = args.runtime_dir or Path(os.environ["LOCALAPPDATA"]) / "QingheSol" / root_id / "mysql80"
    app_password = secrets.token_urlsafe(32)
    mysql_url = URL.create("mysql+pymysql", username="qinghe_app", password=app_password,
                           host="127.0.0.1", port=PORT, database="qinghe_sol", query={"charset": "utf8mb4"})
    for key, value in {"DATABASE_URL": mysql_url.render_as_string(hide_password=False),
                       "QINGHE_MYSQL_MANAGED": "1", "MYSQL_RUNTIME_DIR": str(runtime_dir.resolve()),
                       "MYSQLD_PATH": str(args.mysqld.resolve()),
                       "MYSQL_MANAGER_PASSWORD": secrets.token_urlsafe(32)}.items():
        set_key(pending, key, value, quote_mode="always")
    source_engine = create_engine(f"sqlite:///{source_backup.as_posix()}")
    target_engine = None
    switched = False
    started = False
    try:
        server = start(pending, allow_initialize=True)
        started = True
        target_engine = create_engine(mysql_url, **engine_options(mysql_url))
        Base.metadata.create_all(target_engine)
        evidence = copy_rows(source_engine, target_engine)
        revision = upgrade_schema(target_engine)
        final_manifest = table_manifest(target_engine)
        backup = mysql_backup(pending)
        report = {"at": datetime.now().isoformat(), "project": str(ROOT), "source": "sqlite",
                  "destination": "mysql", "database": "qinghe_sol", "port": PORT,
                  "server": server, "schema_revision": revision, "runtime_directory": str(runtime_dir.resolve()),
                  "source_backup": str(source_backup), "mysql_backup": backup, "tables": evidence,
                  "post_upgrade_tables": final_manifest,
                  "configuration_switched": True, "source_sqlite_preserved": True,
                  "existing_mysql_service_changed": False}
        report["comparison_stage"] = "Full rows compared before schema_version revision 0001 -> 0002"
        prepared_report = snapshots / "mysql-migration.json"
        prepared_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report_target = ROOT / "docs/acceptance/mysql-migration.json"
        report_target.parent.mkdir(parents=True, exist_ok=True)
        # Only verified data and a recoverable SQL backup permit the configuration switch.
        temporary_env = snapshots / "activated.env"
        shutil.copy2(pending, temporary_env)
        os.replace(temporary_env, ROOT / ".env")
        switched = True
        shutil.copy2(prepared_report, report_target)
        print(json.dumps({"status": "migrated", "database": "qinghe_sol", "port": PORT,
                          "tables": {name: item["rows"] for name, item in evidence.items()},
                          "schema_revision": revision, "source_preserved": True}, ensure_ascii=False, indent=2))
    finally:
        source_engine.dispose()
        if target_engine:
            target_engine.dispose()
        if not switched and started:
            stop(pending)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        raise SystemExit(f"MySQL migration: {message}. Original SQLite configuration/data retained unless activation succeeded.") from None
