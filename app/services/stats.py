from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import KIND_RECIPE, PlanEntry, Recipe


@dataclass
class RecipeStat:
    recipe: Recipe
    count: int
    last: date


def _cooked_filter(space_id: int, today: date):
    """Als gekocht zählt, was geplant und nicht mehr in der Zukunft liegt, oder manuell markiert ist."""
    return (
        PlanEntry.space_id == space_id,
        PlanEntry.kind == KIND_RECIPE,
        or_(PlanEntry.day <= today, PlanEntry.cooked.is_(True)),
    )


def top_recipes(db: Session, space_id: int, today: date, limit: int = 10) -> list[RecipeStat]:
    rows = db.execute(
        select(Recipe, func.count(PlanEntry.id).label("n"), func.max(PlanEntry.day).label("last"))
        .join(PlanEntry, PlanEntry.recipe_id == Recipe.id)
        .where(*_cooked_filter(space_id, today))
        .group_by(Recipe.id)
        .order_by(func.count(PlanEntry.id).desc(), func.max(PlanEntry.day).desc())
        .limit(limit)
    ).all()
    return [RecipeStat(recipe, n, last) for recipe, n, last in rows]


def cooked_history(db: Session, space_id: int, today: date, limit: int = 30) -> list[PlanEntry]:
    return list(
        db.scalars(
            select(PlanEntry)
            .where(*_cooked_filter(space_id, today))
            .order_by(PlanEntry.day.desc(), PlanEntry.position.desc())
            .limit(limit)
        )
    )


def totals(db: Session, space_id: int, today: date) -> tuple[int, int]:
    """(Anzahl gekochter Mahlzeiten, Anzahl verschiedener Rezepte)"""
    total, distinct = db.execute(
        select(func.count(PlanEntry.id), func.count(func.distinct(PlanEntry.recipe_id))).where(
            *_cooked_filter(space_id, today)
        )
    ).one()
    return total, distinct
