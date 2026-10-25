from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


# Spalten, die nach der ersten Version hinzugekommen sind: (Tabelle, Spalte, SQL-Definition)
ADDED_COLUMNS = [
    ("shopping_items", "position", "INTEGER NOT NULL DEFAULT 0"),
    ("shopping_items", "added_by", "VARCHAR(50)"),
    ("shopping_items", "checked_by", "VARCHAR(50)"),
    ("spaces", "is_admin", "BOOLEAN NOT NULL DEFAULT 0"),
    ("spaces", "created_at", "DATETIME"),
]


def migrate(bind: Engine) -> None:
    """Minimale Schema-Updates für bestehende Datenbanken (create_all ergänzt nur neue Tabellen)."""
    inspector = inspect(bind)
    for table, column, definition in ADDED_COLUMNS:
        if not inspector.has_table(table):
            continue  # neue Datenbank: create_all legt die Tabelle vollständig an
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            with bind.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
