"""Änderungsverlauf und Rückgängig-Funktion.

Jede Benutzeraktion läuft über `record(...)`. Dabei werden die *Umkehr-Operationen*
(löschen / wieder einfügen / alte Werte zurückschreiben) als JSON im `ActivityLog`
gespeichert. `undo_last` führt sie aus. Dieselbe Tabelle dient als Verlaufsanzeige.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.config import now
from app.models import ActivityLog, CategoryOrder, PlanEntry, Recipe, RecipeIngredient, ShoppingItem

# Nur diese Tabellen dürfen über Undo-Operationen verändert werden.
UNDOABLE_MODELS = {
    "plan_entries": PlanEntry,
    "shopping_items": ShoppingItem,
    "category_order": CategoryOrder,
    "recipes": Recipe,
    "recipe_ingredients": RecipeIngredient,
}
MAX_LOG_ROWS = 500


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _deserialize(column, value: Any) -> Any:
    if value is None:
        return None
    python_type = column.type.python_type
    if python_type is datetime:
        return datetime.fromisoformat(value)
    if python_type is date:
        return date.fromisoformat(value)
    return value


def snapshot(obj) -> dict[str, Any]:
    mapper = inspect(obj).mapper
    return {c.key: _serialize(getattr(obj, c.key)) for c in mapper.column_attrs}


class Action:
    """Sammelt die Umkehr-Operationen einer Benutzeraktion."""

    def __init__(self, db: Session):
        self.db = db
        self.ops: list[dict] = []

    # Reihenfolge: neue Umkehr-Operationen vorne einfügen, damit sie rückwärts laufen.
    def created(self, obj) -> None:
        """Nach `db.flush()` aufrufen, wenn `obj` neu angelegt wurde."""
        self.ops.insert(0, {"op": "delete", "table": obj.__tablename__, "id": obj.id})

    def updating(self, obj) -> None:
        """VOR dem Ändern von `obj` aufrufen."""
        values = snapshot(obj)
        self.ops.insert(0, {"op": "update", "table": obj.__tablename__, "id": values.pop("id"), "values": values})

    def deleting(self, obj) -> None:
        """Merkt sich `obj` und löscht es."""
        self.ops.insert(0, {"op": "insert", "table": obj.__tablename__, "row": snapshot(obj)})
        self.db.delete(obj)


@contextmanager
def record(db: Session, space_id: int, member_name: str, description: str) -> Iterator[Action]:
    action = Action(db)
    try:
        yield action
        db.flush()
        if not action.ops:  # nichts geändert (z. B. gleiche Reihenfolge erneut gesendet): nicht protokollieren
            db.commit()
            return
        db.add(
            ActivityLog(
                space_id=space_id,
                member_name=member_name,
                description=description[:300],
                undo_ops=json.dumps(action.ops),
            )
        )
        db.commit()
        _prune(db, space_id)
    except Exception:
        db.rollback()
        raise


def _prune(db: Session, space_id: int) -> None:
    ids = db.scalars(
        select(ActivityLog.id).where(ActivityLog.space_id == space_id).order_by(ActivityLog.id.desc()).offset(MAX_LOG_ROWS)
    ).all()
    if ids:
        for row in db.scalars(select(ActivityLog).where(ActivityLog.id.in_(ids))):
            db.delete(row)
        db.commit()


def last_undoable(db: Session, space_id: int) -> ActivityLog | None:
    return db.scalars(
        select(ActivityLog)
        .where(ActivityLog.space_id == space_id, ActivityLog.undone_at.is_(None))
        .order_by(ActivityLog.id.desc())
        .limit(1)
    ).first()


def recent(db: Session, space_id: int, limit: int = 100) -> list[ActivityLog]:
    return list(
        db.scalars(
            select(ActivityLog).where(ActivityLog.space_id == space_id).order_by(ActivityLog.id.desc()).limit(limit)
        )
    )


def undo_last(db: Session, space_id: int) -> ActivityLog | None:
    entry = last_undoable(db, space_id)
    if entry is None:
        return None
    try:
        for op in json.loads(entry.undo_ops):
            _apply(db, space_id, op)
        entry.undone_at = now()
        db.commit()
    except Exception:
        db.rollback()
        raise
    return entry


def _apply(db: Session, space_id: int, op: dict) -> None:
    model = UNDOABLE_MODELS[op["table"]]  # KeyError bei unbekannter Tabelle -> Abbruch
    columns = {c.key: c for c in inspect(model).mapper.column_attrs}

    def restore(values: dict) -> dict:
        return {k: _deserialize(columns[k].columns[0], v) for k, v in values.items() if k in columns}

    kind = op["op"]
    if kind == "delete":
        obj = db.get(model, op["id"])
        if obj is not None:
            db.delete(obj)
            db.flush()
    elif kind == "insert":
        row = restore(op["row"])
        if "space_id" in row and row["space_id"] != space_id:
            raise ValueError("Fremder Space")
        db.add(model(**row))
        db.flush()
    elif kind == "update":
        obj = db.get(model, op["id"])
        if obj is not None:
            if hasattr(obj, "space_id") and obj.space_id != space_id:
                raise ValueError("Fremder Space")
            for key, value in restore(op["values"]).items():
                setattr(obj, key, value)
            db.flush()
    else:
        raise ValueError(f"Unbekannte Operation: {kind}")
