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


def migrate(bind: Engine) -> None:
    """Minimale Schema-Updates für bestehende Datenbanken (create_all ergänzt nur neue Tabellen)."""
    inspector = inspect(bind)
    if inspector.has_table("shopping_items"):
        columns = {c["name"] for c in inspector.get_columns("shopping_items")}
        if "position" not in columns:
            with bind.begin() as conn:
                conn.execute(text("ALTER TABLE shopping_items ADD COLUMN position INTEGER NOT NULL DEFAULT 0"))


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
