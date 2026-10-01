"""Preview reviewed official-source import; --apply requires stopped demo API.

Run from backend: uv run python ../scripts/import_verified_sources.py [--apply]
"""

import argparse
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config  # noqa: E402
from app.database import SessionLocal, engine  # noqa: E402
from app.migrate import upgrade_schema  # noqa: E402
from app.policy_import import DEFAULT_MANIFEST, build_verified_documents, import_verified_documents  # noqa: E402
from app.database import Base  # noqa: E402
from migrate_to_mysql import digest  # noqa: E402
from mysql_data import backup, managed  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402


def protected_manifest(target_engine):
    # Read the old schema before 0003 without selecting not-yet-added policy
    # columns. Only policy/audit/schema metadata are expected to change.
    with target_engine.connect() as connection:
        result = {}
        for table in Base.metadata.sorted_tables:
            if table.name in {"policy_doc", "audit_log"}:
                continue
            query = select(table).order_by(table.c.id)
            count = select(func.count()).select_from(table)
            if table.name == "system_setting":
                query = query.where(table.c.key != "schema_version")
                count = count.where(table.c.key != "schema_version")
            result[table.name] = {"rows": connection.scalar(count), "sha256": digest([
                dict(row) for row in connection.execute(query).mappings()])}
        return result


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--apply", action="store_true", help="Back up and import into the managed demo MySQL")
    args = parser.parse_args()
    documents = build_verified_documents(args.manifest)
    if not args.apply:
        print(json.dumps({"mode": "preview", "documents": len(documents), "sources": [
            {"source_key": doc["source_key"], "title": doc["title"], "usage_scope": doc["usage_scope"],
             "current_answer_allowed": doc["current_answer_allowed"], "section_count": len(doc["sections"])}
            for doc in documents]}, ensure_ascii=False, indent=2))
        return
    if config.APP_ENV != "demo" or engine.dialect.name != "mysql":
        raise RuntimeError("Import CLI requires the managed local demo MySQL database")
    _runtime, values = managed()
    if engine.url != make_url(values["DATABASE_URL"]):
        raise RuntimeError("Process environment differs from the managed project database; import refused")
    with socket.socket() as connection:
        connection.settimeout(0.5)
        if connection.connect_ex(("127.0.0.1", 8000)) == 0:
            raise RuntimeError("Stop the demo API before upgrading and importing knowledge")
    saved = backup()  # Authenticates the exact project-owned MySQL instance.
    before = protected_manifest(engine)
    revision = upgrade_schema(engine)
    with SessionLocal.begin() as db:
        summary = import_verified_documents(db, documents)
    after = protected_manifest(engine)
    protected = list(before)
    unchanged = all(before[name] == after[name] for name in protected)
    report = {"mode": "applied", "schema_revision": revision, "backup": saved,
              "protected_tables_unchanged": unchanged, "protected_tables": {
                  name: {"before": before[name], "after": after[name]} for name in protected}, **summary}
    target = ROOT / "docs/acceptance/knowledge-import.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not unchanged:
        raise RuntimeError("Protected-table verification failed; inspect the preserved backup and report")
    print(json.dumps({"documents": summary["documents"], "created": summary["created"],
                      "updated": summary["updated"], "unchanged": summary["unchanged"],
                      "protected_tables_unchanged": True, "report": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SQL driver error strings may contain secrets; only deliberate guard
        # descriptions and exception classes are suitable for terminal output.
        print(str(error) if isinstance(error, (RuntimeError, ValueError)) else type(error).__name__)
        sys.exit(1)
