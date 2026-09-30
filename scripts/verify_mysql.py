"""Verify the application against a disposable, local MySQL 8 instance.

Uses an installed official mysqld binary. Never connects to the user's MySQL service,
edits .env, or prints credentials. Keep generated artifacts under ignored work/.
"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pymysql
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
PORT = 13307


def main():
    # PowerShell may otherwise use GBK while pytest emits UTF-8 diagnostics.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mysqld", default=r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe")
    parser.add_argument("--junit-output", default="../docs/acceptance/mysql-tests-v02.xml")
    parser.add_argument("--work-dir", default=str(ROOT / "work/mysql-verify"),
                        help="Temporary server files; native Windows MySQL requires an ASCII path")
    parser.add_argument("targets", nargs="*", help="Optional pytest paths / flags")
    args = parser.parse_args()
    binary = Path(args.mysqld).resolve()
    if not binary.is_file() or binary.name.lower() not in {"mysqld", "mysqld.exe"}:
        raise SystemExit("An installed mysqld binary is required; no server is downloaded.")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit("Temporary test port 13307 is occupied. No existing service was stopped.")

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
    workspace = Path(args.work_dir).resolve()
    if os.name == "nt" and not str(workspace).isascii():
        raise SystemExit("Native Windows MySQL requires an ASCII server path. Supply --work-dir for test artifacts.")
    run_dir = workspace / run_id
    data = run_dir / "data"
    data.mkdir(parents=True)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    common = [str(binary), "--no-defaults", f"--basedir={binary.parent.parent}", f"--datadir={data}",
              f"--log-error={run_dir / 'mysql-error.log'}",
              "--innodb-redo-log-capacity=16777216", "--innodb-buffer-pool-size=33554432"]
    if os.name == "nt":
        common.append("--no-monitor")  # Keep one owned server process, rather than a detached watchdog child.
    with (run_dir / "initialize.log").open("wb") as log:
        initialized = subprocess.run(common + ["--initialize-insecure"], stdout=log, stderr=log,
                                     creationflags=flags, timeout=60, check=False)
    if initialized.returncode:
        raise SystemExit(f"Temporary MySQL initialization failed. Log: {run_dir / 'initialize.log'}")

    database = "qinghe_verify_" + secrets.token_hex(6)
    user_password, root_password = secrets.token_urlsafe(28), secrets.token_urlsafe(28)
    bootstrap = run_dir / "bootstrap.sql"
    bootstrap.write_text(
        f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{root_password}';\n"
        f"CREATE USER 'verify_admin'@'127.0.0.1' IDENTIFIED BY '{root_password}';\n"
        "GRANT ALL PRIVILEGES ON *.* TO 'verify_admin'@'127.0.0.1' WITH GRANT OPTION;\n",
        encoding="utf-8",
    )
    connection = None
    code = 1
    with (run_dir / "server.log").open("wb") as log:
        server = subprocess.Popen(common + [f"--port={PORT}", "--bind-address=127.0.0.1", "--mysqlx=0",
                                            f"--init-file={bootstrap}",
                                            "--skip-name-resolve", "--secure-file-priv=NULL", "--max-connections=30"],
                                  stdout=log, stderr=log, creationflags=flags)
        try:
            deadline = time.monotonic() + 45
            last_error_code = None
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise RuntimeError(f"Temporary MySQL stopped; inspect {run_dir / 'server.log'}")
                try:
                    connection = pymysql.connect(host="127.0.0.1", port=PORT, user="verify_admin", password=root_password,
                                                 autocommit=True, connect_timeout=2, read_timeout=10)
                    break
                except pymysql.err.OperationalError as error:
                    last_error_code = error.args[0]
                    time.sleep(0.25)
            if connection is None:
                raise RuntimeError(f"Temporary MySQL connection failed in 45 seconds (code {last_error_code})")
            bootstrap.unlink(missing_ok=True)
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
                cursor.execute("CREATE USER 'campus_verify'@'127.0.0.1' IDENTIFIED BY %s", (user_password,))
                cursor.execute(f"GRANT ALL PRIVILEGES ON `{database}`.* TO 'campus_verify'@'127.0.0.1'")
                cursor.execute("SELECT VERSION(), @@transaction_isolation")
                version, default_isolation = cursor.fetchone()
            url = URL.create("mysql+pymysql", username="campus_verify", password=user_password,
                             host="127.0.0.1", port=PORT, database=database, query={"charset": "utf8mb4"})
            environment = os.environ.copy()
            environment["PYTHONUTF8"] = "1"
            environment["PYTHONIOENCODING"] = "utf-8"
            environment["QINGHE_TEST_MYSQL_URL"] = url.render_as_string(hide_password=False)
            environment["QINGHE_MYSQL_TEST_CONFIRM"] = "temporary-instance"
            sys.path.insert(0, str(ROOT / "backend"))
            from app.database import engine_options
            check_engine = create_engine(url, **engine_options(url))
            with check_engine.connect() as check_connection:
                actual_isolation = check_connection.get_isolation_level()
            check_engine.dispose()
            print(f"Temporary MySQL {version}; server default {default_isolation}; "
                  f"application isolation {actual_isolation}; running isolated tests.", flush=True)
            command = [sys.executable, "-m", "pytest", "-q", f"--junitxml={args.junit_output}"] + args.targets
            tested = subprocess.run(command, cwd=ROOT / "backend", env=environment,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                    encoding="utf-8", errors="replace", creationflags=flags, check=False)
            output = tested.stdout.replace(root_password, "[redacted]").replace(user_password, "[redacted]")
            (run_dir / "pytest.log").write_text(output, encoding="utf-8")
            code = tested.returncode
            (run_dir / "result.json").write_text(json.dumps({"version": version, "port": PORT,
                                                          "server_default_isolation": default_isolation,
                                                          "application_isolation": actual_isolation,
                                                          "pytest_exit_code": code}, indent=2), encoding="utf-8")
            print(output, flush=True)
        finally:
            bootstrap.unlink(missing_ok=True)
            if connection is not None:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SHUTDOWN")
                except pymysql.err.Error:
                    pass
                connection.close()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.terminate()  # Only the child process started above, never a Windows service.
                server.wait(timeout=10)
            print("Temporary MySQL stopped; demonstration database and existing MySQL service unchanged.", flush=True)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
