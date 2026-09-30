from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import DATABASE_URL


class Base(DeclarativeBase):
    pass


sqlite = DATABASE_URL.startswith("sqlite")


def engine_options(url):
    backend = make_url(url).get_backend_name()
    options = {"pool_pre_ping": True}
    if backend == "sqlite":
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    elif backend == "mysql":
        # Auth reads must not pin a stale snapshot before a later business row lock.
        options["isolation_level"] = "READ COMMITTED"
    return options


engine = create_engine(DATABASE_URL, **engine_options(DATABASE_URL))
if sqlite:
    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    with SessionLocal() as db:
        try:
            if request.method in {"POST", "PUT", "PATCH", "DELETE"} and sqlite:
                db.execute(text("BEGIN IMMEDIATE"))
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
