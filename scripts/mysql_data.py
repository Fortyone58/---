"""Backup and reset only the guarded, managed Qinghe demonstration database."""

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from mysql_runtime import ROOT, connect, flags, settings


def managed(config_path=ROOT / ".env"):
    values = dotenv_values(config_path)
    runtime = settings(config_path)
    if runtime is None or values.get("APP_ENV") != "demo":
        raise RuntimeError("This command requires the managed local Qinghe demo, not an external database.")
    connection, _version = connect(runtime)
    connection.close()
    return runtime, values


def backup(config_path=ROOT / ".env"):
    runtime, _values = managed(config_path)
    dump = runtime.binary.with_name("mysqldump.exe")
    if not dump.is_file():
        raise RuntimeError("Official mysqldump.exe is required for a recoverable backup.")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = ROOT / "work/backups" / f"mysql-demo-{stamp}.sql"
    target.parent.mkdir(parents=True, exist_ok=True)
    private_options = runtime.directory / f"dump-{stamp}.cnf"
    private_options.write_text("[client]\nuser=qinghe_manager\n"
                               f"password={runtime.manager_password}\nhost=127.0.0.1\nport={runtime.port}\n",
                               encoding="utf-8")
    try:
        with target.open("wb") as output:
            result = subprocess.run([str(dump), f"--defaults-extra-file={private_options}",
                                     "--single-transaction", "--skip-lock-tables", "--no-tablespaces",
                                     "--set-gtid-purged=OFF", "--column-statistics=0", "--hex-blob",
                                     "--default-character-set=utf8mb4", "--databases", runtime.database],
                                    stdout=output, stderr=subprocess.PIPE, creationflags=flags(),
                                    timeout=120, check=False)
        if result.returncode or target.stat().st_size < 100:
            raise RuntimeError("MySQL backup failed; no reset was attempted.")
    finally:
        private_options.unlink(missing_ok=True)
    checksum = hashlib.sha256(target.read_bytes()).hexdigest()
    return {"path": str(target), "sha256": checksum, "bytes": target.stat().st_size}


def reset(config_path=ROOT / ".env", confirm=""):
    if confirm != "campus-demo":
        raise RuntimeError("Explicit demo reset confirmation is required.")
    _runtime, values = managed(config_path)
    saved = backup(config_path)
    sys.path.insert(0, str(ROOT / "backend"))
    from app.database import engine_options

    engine = create_engine(make_url(values["DATABASE_URL"]), **engine_options(values["DATABASE_URL"]))
    try:
        result = _reset_tables_and_seed(engine)
    finally:
        engine.dispose()
    record = {"at": datetime.now().isoformat(), "backend": "mysql", "database": "qinghe_sol",
              "backup": saved, "result": result}
    with (ROOT / "work/reset-log.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def _reset_tables_and_seed(engine, include_bootstrap=True):
    """Internal operation; the public reset authenticates and backs up first."""
    from app.database import Base
    from app.migrate import upgrade_schema
    from app.seed import seed_database
    from sqlalchemy.orm import sessionmaker

    if engine.dialect.name != "mysql":
        raise RuntimeError("This internal reset requires MySQL.")
    # Connection-local FK checks are restored even if deleting a self-linked tail fails.
    with engine.connect() as connection:
        connection.execute(text("SET SESSION FOREIGN_KEY_CHECKS=0"))
        connection.commit()
        try:
            with connection.begin():
                for table in reversed(Base.metadata.sorted_tables):
                    connection.execute(table.delete())
        finally:
            connection.execute(text("SET SESSION FOREIGN_KEY_CHECKS=1"))
            connection.commit()
        for table in Base.metadata.sorted_tables:
            connection.execute(text(f"ALTER TABLE `{table.name}` AUTO_INCREMENT=1"))
        connection.commit()
    upgrade_schema(engine)
    factory = sessionmaker(bind=engine)
    with factory.begin() as db:
        return seed_database(db, include_bootstrap=include_bootstrap, include_knowledge=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "reset"])
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        result = reset(confirm=args.confirm) if args.action == "reset" else backup()
        print(json.dumps(result, ensure_ascii=True))
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        raise SystemExit(f"Qinghe MySQL data: {message}") from None


if __name__ == "__main__":
    main()
