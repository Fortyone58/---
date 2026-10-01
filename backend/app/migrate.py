from sqlalchemy import DateTime, inspect, select, text
from sqlalchemy.orm import sessionmaker

from .database import Base, engine
from .models import SystemSetting
from .services import now

REVISION = "0002"


def upgrade_schema(target_engine=None):
    """0001 creates empty tables; 0002 explicitly preserves MySQL microseconds."""
    target_engine = target_engine or engine
    existing = inspect(target_engine).has_table("system_setting")
    factory = sessionmaker(bind=target_engine)
    if existing:
        with factory() as db:
            row = db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version"))
            if row and row.value.get("revision") not in {"0001", REVISION}:
                raise RuntimeError("Unknown schema revision; upgrade refused before any schema change")
    Base.metadata.create_all(target_engine)
    if target_engine.dialect.name == "mysql":
        inspector = inspect(target_engine)
        with target_engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                actual = {column["name"]: column["type"] for column in inspector.get_columns(table.name)}
                for column in table.columns:
                    if isinstance(column.type, DateTime) and getattr(actual[column.name], "fsp", None) != 6:
                        nullable = "NULL" if column.nullable else "NOT NULL"
                        connection.execute(text(f"ALTER TABLE `{table.name}` MODIFY COLUMN `{column.name}` "
                                                f"DATETIME(6) {nullable}"))
    with factory.begin() as db:
        row = db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version"))
        if row is None:
            db.add(SystemSetting(key="schema_version", value={"revision": REVISION}, version=1, modified_at=now()))
        elif row.value.get("revision") != REVISION:
            row.value = {**row.value, "revision": REVISION}
    return REVISION


if __name__ == "__main__":
    print(f"Schema revision {upgrade_schema()}")
