from sqlalchemy import DateTime, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateColumn

from .database import Base, engine
from .models import PolicyDoc, SystemSetting
from .services import now

REVISION = "0003"


def _upgrade_policy_sources(target_engine):
    """Add only missing 0003 columns; safe to resume after MySQL DDL commits."""
    actual = {column["name"] for column in inspect(target_engine).get_columns("policy_doc")}
    columns = ("source_key", "usage_scope", "current_answer_allowed", "publication_date",
               "effective_from", "expires_at", "applicability", "source_metadata")
    with target_engine.begin() as connection:
        for name in columns:
            if name not in actual:
                column = PolicyDoc.__table__.c[name]
                definition = str(CreateColumn(column).compile(dialect=target_engine.dialect))
                connection.execute(text(f"ALTER TABLE policy_doc ADD COLUMN {definition}"))
        # A literal TEXT default is unsupported by MySQL. Backfill only the
        # new optional applicability field; do not alter original content.
        connection.execute(text("UPDATE policy_doc SET applicability = '' WHERE applicability IS NULL"))
    actual_indexes = {index["name"] for index in inspect(target_engine).get_indexes("policy_doc")}
    for index in PolicyDoc.__table__.indexes:
        if index.name not in actual_indexes:
            index.create(target_engine)


def upgrade_schema(target_engine=None):
    """0001 tables, 0002 microseconds, 0003 source provenance and usage bounds."""
    target_engine = target_engine or engine
    existing = inspect(target_engine).has_table("system_setting")
    factory = sessionmaker(bind=target_engine)
    if existing:
        with factory() as db:
            row = db.scalar(select(SystemSetting).where(SystemSetting.key == "schema_version"))
            if row and row.value.get("revision") not in {"0001", "0002", REVISION}:
                raise RuntimeError("Unknown schema revision; upgrade refused before any schema change")
    Base.metadata.create_all(target_engine)
    _upgrade_policy_sources(target_engine)
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
