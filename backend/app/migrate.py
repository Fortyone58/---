from sqlalchemy import select

from .database import Base, SessionLocal, engine
from .models import SystemSetting
from .services import now

REVISION = "0001"


def upgrade_schema():
    """Initial migration. Future revisions must add explicit migration steps."""
    Base.metadata.create_all(engine)
    with SessionLocal.begin() as db:
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version"))
        if row and row.value.get("revision") != REVISION:
            raise RuntimeError("Unknown schema revision; do not run an implicit schema upgrade")
        if row is None:
            db.add(SystemSetting(key="schema_version", value={"revision": REVISION}, version=1, modified_at=now()))
    return REVISION


if __name__ == "__main__":
    print(f"Schema revision {upgrade_schema()}")
