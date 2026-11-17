"""Read-only JSON API, e.g. for Home Assistant.

Access only via the token in the path (like the shared link); no profile is needed because nothing
is changed here.
"""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import today
from app.db import get_db
from app.i18n import WEEKDAYS
from app.models import KIND_RECIPE, PlanEntry, Space
from app.web import get_space

router = APIRouter(prefix="/s/{token}/api")


def _title(entry: PlanEntry) -> str:
    if entry.kind == KIND_RECIPE:
        return entry.recipe.title if entry.recipe else ""
    return entry.note or ""


@router.get("/plan")
def plan(
    space: Annotated[Space, Depends(get_space)],
    db: Annotated[Session, Depends(get_db)],
    days: Annotated[int, Query(ge=1, le=14)] = 7,
    lang: Annotated[str, Query(pattern="^(de|en|fr|es|it)$")] = "de",
):
    start = today()
    end = start + timedelta(days=days - 1)
    entries = db.scalars(
        select(PlanEntry)
        .options(selectinload(PlanEntry.recipe))
        .where(PlanEntry.space_id == space.id, PlanEntry.day >= start, PlanEntry.day <= end)
        .order_by(PlanEntry.day, PlanEntry.position, PlanEntry.id)
    ).all()

    by_day = {start + timedelta(days=i): [] for i in range(days)}
    for entry in entries:
        by_day[entry.day].append({"kind": entry.kind, "title": _title(entry), "cooked": bool(entry.cooked)})

    return {
        "today": start.isoformat(),
        "days": [
            {"date": day.isoformat(), "weekday": WEEKDAYS[lang][day.weekday()], "entries": items}
            for day, items in by_day.items()
        ],
    }
