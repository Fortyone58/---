"""Read-only comparison of the migrated live demo against its SQLite API baseline."""

import argparse
import json
from datetime import datetime

import httpx
from sqlalchemy import create_engine

from migrate_to_mysql import table_manifest
from mysql_data import managed
from mysql_runtime import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="after-migration")
    args = parser.parse_args()
    baseline = json.loads((ROOT / "work/before-mysql-api.json").read_text(encoding="utf-8"))
    migration = json.loads((ROOT / "docs/acceptance/mysql-migration.json").read_text(encoding="utf-8"))
    runtime, values = managed()
    from app.database import engine_options

    engine = create_engine(values["DATABASE_URL"], **engine_options(values["DATABASE_URL"]))
    try:
        manifest = table_manifest(engine)
        assert manifest == migration["post_upgrade_tables"], "Live rows changed since the verified migration"
    finally:
        engine.dispose()
    roles = []
    with httpx.Client(base_url="http://127.0.0.1:8000/api", timeout=10) as client:
        ping = client.get("/ping").json()
        assert ping["database"] == "mysql" and ping["project"] == "qinghe-sol"
        assert httpx.get("http://127.0.0.1:5173", timeout=10).status_code == 200
        for username, routes in baseline.items():
            login = client.post("/auth/login", json={"username": username, "password": "Demo@2026"})
            assert login.status_code == 200, f"Login failed for {username}"
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            for route, original in routes.items():
                response = client.get(route, headers=headers)
                assert response.status_code == 200, f"API failed for {username}: {route}"
                assert response.json() == original, f"API differs from SQLite for {username}: {route}"
            roles.append({"username": username, "routes_compared": len(routes), "all_identical": True})
    report = {"checked_at": datetime.now().isoformat(), "label": args.label, "ping": ping,
              "roles": roles, "total_api_comparisons": sum(item["routes_compared"] for item in roles),
              "table_hashes_identical": True, "tables": manifest, "mysql_port": runtime.port,
              "business_or_chat_writes": False, "demo_database_reset": False}
    target = ROOT / "docs/acceptance" / f"mysql-live-{args.label}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "identical", "roles": len(roles), "api_comparisons": report["total_api_comparisons"],
                      "table_count": len(manifest), "total_rows": sum(item["rows"] for item in manifest.values()),
                      "backend": ping["database"], "label": args.label}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) if isinstance(error, (RuntimeError, AssertionError)) else type(error).__name__
        raise SystemExit(f"Live MySQL verification: {message}") from None
