import os

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

# This fixture stack imports app.main and its normal private configuration.
# Run test_agent_safety_isolated.py with --noconftest; it injects fake config first.
from app import rag
from app.database import Base, engine_options, get_db
from app.main import app
from app.seed import seed_database


@pytest.fixture(autouse=True)
def isolated_rag(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setenv("QINGHE_RAG_DIR", str(tmp_path / "rag"))
    monkeypatch.setenv("RAG_MODEL_CACHE", str(tmp_path / "rag/models"))
    yield
    rag.close_indexes()


@pytest.fixture
def sandbox(tmp_path):
    target = tmp_path / "isolated-test.db"
    mysql_url = os.getenv("QINGHE_TEST_MYSQL_URL")
    if mysql_url:
        parsed = make_url(mysql_url)
        if (parsed.drivername != "mysql+pymysql" or parsed.host != "127.0.0.1" or parsed.port != 13307 or
                not parsed.database or not parsed.database.startswith("qinghe_verify_") or
                os.getenv("QINGHE_MYSQL_TEST_CONFIRM") != "temporary-instance"):
            raise RuntimeError("MySQL tests require the guarded temporary instance; refused other database")
        engine = create_engine(mysql_url, **engine_options(mysql_url))
        Base.metadata.drop_all(engine)
    else:
        engine = create_engine(f"sqlite:///{target.as_posix()}", connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def configure(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    with factory.begin() as db:
        seed_database(db)

    def isolated_db(request: Request):
        with factory() as db:
            try:
                if engine.dialect.name == "sqlite" and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                    db.execute(text("BEGIN IMMEDIATE"))
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    app.dependency_overrides[get_db] = isolated_db
    client = TestClient(app)
    yield client, factory
    client.close()
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture
def client(sandbox):
    return sandbox[0]


@pytest.fixture
def factory(sandbox):
    return sandbox[1]


@pytest.fixture
def headers(client):
    cache = {}

    def login(username):
        if username not in cache:
            response = client.post("/api/auth/login", json={"username": username, "password": "Demo@2026"})
            assert response.status_code == 200, response.text
            cache[username] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        return cache[username]

    return login
