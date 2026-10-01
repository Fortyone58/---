"""Manage only Qinghe's private local MySQL instance, never a Windows service."""

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pymysql
from dotenv import dotenv_values
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
MARKER = "qinghe-managed-mysql-v1"
PORT = 13308


@dataclass
class Runtime:
    directory: Path
    binary: Path
    database: str
    app_user: str
    app_password: str
    manager_password: str
    port: int = PORT

    @property
    def data(self):
        return self.directory / "data"

    @property
    def owner_file(self):
        return self.directory / "owner.json"


def settings(config_path=ROOT / ".env"):
    values = dotenv_values(config_path)
    if values.get("QINGHE_MYSQL_MANAGED") != "1":
        return None
    url = make_url(values["DATABASE_URL"])
    if (url.drivername != "mysql+pymysql" or url.host != "127.0.0.1" or url.port != PORT or
            url.database != "qinghe_sol" or url.username != "qinghe_app"):
        raise RuntimeError("Managed MySQL requires the dedicated local Qinghe URL.")
    directory = Path(values["MYSQL_RUNTIME_DIR"]).resolve()
    binary = Path(values["MYSQLD_PATH"]).resolve()
    if not str(directory).isascii() or directory == Path(directory.anchor):
        raise RuntimeError("MySQL runtime must use a dedicated ASCII directory.")
    if not binary.is_file() or binary.name.lower() != "mysqld.exe":
        raise RuntimeError("The installed official mysqld.exe is required.")
    for value in [url.password, values.get("MYSQL_MANAGER_PASSWORD")]:
        if not value or not re.fullmatch(r"[A-Za-z0-9_-]{24,}", value):
            raise RuntimeError("Managed MySQL requires generated private credentials.")
    return Runtime(directory, binary, url.database, url.username, url.password, values["MYSQL_MANAGER_PASSWORD"])


def listening(port=PORT):
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def check_owner(runtime):
    owner = json.loads(runtime.owner_file.read_text(encoding="utf-8"))
    if (owner.get("marker") != MARKER or Path(owner.get("project", "")).resolve() != ROOT or
            Path(owner.get("data", "")).resolve() != runtime.data.resolve() or owner.get("port") != runtime.port):
        raise RuntimeError("MySQL ownership does not match this project; action refused.")
    return owner


def mark_ready(runtime):
    owner = check_owner(runtime)
    if not owner.get("bootstrap_complete", True):
        owner["bootstrap_complete"] = True
        temporary = runtime.directory / "owner.pending.json"
        temporary.write_text(json.dumps(owner, indent=2), encoding="utf-8")
        temporary.replace(runtime.owner_file)


def connect(runtime):
    check_owner(runtime)
    connection = pymysql.connect(host="127.0.0.1", port=runtime.port, user="qinghe_manager",
                                 password=runtime.manager_password, autocommit=True,
                                 charset="utf8mb4", connect_timeout=2, read_timeout=10)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT @@datadir, @@port, VERSION()")
            data_dir, port, version = cursor.fetchone()
        if Path(data_dir).resolve() != runtime.data.resolve() or port != runtime.port:
            raise RuntimeError("Connected server is not this project's MySQL instance.")
        return connection, version
    except Exception:
        connection.close()
        raise


def flags():
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def server_arguments(runtime):
    return [str(runtime.binary), "--no-defaults", "--no-monitor",
            f"--basedir={runtime.binary.parent.parent}", f"--datadir={runtime.data}",
            f"--log-error={runtime.directory / 'mysql-error.log'}",
            f"--pid-file={runtime.directory / 'mysql.pid'}",
            "--innodb-buffer-pool-size=67108864", "--innodb-redo-log-capacity=33554432"]


def initialize(runtime):
    if runtime.directory.exists() and any(runtime.directory.iterdir()):
        raise RuntimeError("MySQL runtime directory is not empty; initialization refused.")
    if listening(runtime.port):
        raise RuntimeError("Port 13308 is occupied; the existing listener was not changed.")
    runtime.data.mkdir(parents=True)
    with (runtime.directory / "initialize.log").open("wb") as log:
        result = subprocess.run(server_arguments(runtime) + ["--initialize-insecure"],
                                stdout=log, stderr=log, creationflags=flags(), timeout=60, check=False)
    if result.returncode:
        raise RuntimeError("Private MySQL initialization failed; inspect initialize.log.")
    runtime.owner_file.write_text(json.dumps({"marker": MARKER, "project": str(ROOT),
                                             "data": str(runtime.data), "port": runtime.port,
                                             "bootstrap_complete": False}, indent=2),
                                  encoding="utf-8")


def start(config_path=ROOT / ".env", allow_initialize=False):
    runtime = settings(config_path)
    if runtime is None:
        return {"status": "not_managed"}
    fresh = not runtime.owner_file.exists()
    if fresh:
        if not allow_initialize:
            raise RuntimeError("Managed MySQL is not initialized. Restore its data or run the migration setup.")
        initialize(runtime)
    owner = check_owner(runtime)
    if listening(runtime.port):
        connection, version = connect(runtime)
        connection.close()
        mark_ready(runtime)
        return {"status": "already_running", "port": runtime.port, "version": version}

    bootstrap = runtime.directory / "bootstrap.sql"
    arguments = server_arguments(runtime) + [f"--port={runtime.port}", "--bind-address=127.0.0.1",
                                             "--mysqlx=0", "--skip-name-resolve", "--secure-file-priv=NULL",
                                             "--max-connections=40", "--character-set-server=utf8mb4",
                                             "--collation-server=utf8mb4_0900_as_cs"]
    if not owner.get("bootstrap_complete", True):
        bootstrap.write_text(
            f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{runtime.manager_password}';\n"
            f"CREATE DATABASE IF NOT EXISTS `{runtime.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs;\n"
            f"CREATE USER IF NOT EXISTS '{runtime.app_user}'@'127.0.0.1' IDENTIFIED BY '{runtime.app_password}';\n"
            f"ALTER USER '{runtime.app_user}'@'127.0.0.1' IDENTIFIED BY '{runtime.app_password}';\n"
            f"GRANT ALL PRIVILEGES ON `{runtime.database}`.* TO '{runtime.app_user}'@'127.0.0.1';\n"
            f"CREATE USER IF NOT EXISTS 'qinghe_manager'@'127.0.0.1' IDENTIFIED BY '{runtime.manager_password}';\n"
            f"ALTER USER 'qinghe_manager'@'127.0.0.1' IDENTIFIED BY '{runtime.manager_password}';\n"
            f"GRANT ALL PRIVILEGES ON `{runtime.database}`.* TO 'qinghe_manager'@'127.0.0.1';\n"
            "GRANT SHUTDOWN ON *.* TO 'qinghe_manager'@'127.0.0.1';\n", encoding="utf-8")
        arguments.append(f"--init-file={bootstrap}")
    with (runtime.directory / "server.log").open("ab") as log:
        server = subprocess.Popen(arguments, stdout=log, stderr=log, creationflags=flags())
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise RuntimeError("Private MySQL stopped during startup; inspect mysql-error.log.")
            try:
                connection, version = connect(runtime)
                connection.close()
                mark_ready(runtime)
                bootstrap.unlink(missing_ok=True)
                return {"status": "started", "port": runtime.port, "version": version}
            except pymysql.err.OperationalError:
                time.sleep(0.2)
        raise RuntimeError("Private MySQL did not become ready in 45 seconds.")
    except Exception:
        # Only the process created in this invocation can be terminated here.
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=10)
        raise
    finally:
        bootstrap.unlink(missing_ok=True)


def stop(config_path=ROOT / ".env"):
    runtime = settings(config_path)
    if runtime is None:
        return {"status": "not_managed"}
    check_owner(runtime)
    if not listening(runtime.port):
        return {"status": "already_stopped", "port": runtime.port}
    connection, version = connect(runtime)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHUTDOWN")
    finally:
        connection.close()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if not listening(runtime.port):
            return {"status": "stopped", "port": runtime.port, "version": version}
        time.sleep(0.2)
    raise RuntimeError("MySQL shutdown did not finish; no unrelated process was stopped.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    parser.add_argument("--config", type=Path, default=ROOT / ".env")
    args = parser.parse_args()
    try:
        if args.action == "start":
            result = start(args.config)
        elif args.action == "stop":
            result = stop(args.config)
        else:
            runtime = settings(args.config)
            if runtime and listening(runtime.port):
                connection, version = connect(runtime)
                connection.close()
                result = {"status": "running", "port": runtime.port, "version": version}
            else:
                result = {"status": "stopped" if runtime else "not_managed"}
        print(json.dumps(result))
    except Exception as error:
        # SQL/URL/password parameters must never enter terminal output.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        raise SystemExit(f"Qinghe MySQL: {message}") from None


if __name__ == "__main__":
    main()
