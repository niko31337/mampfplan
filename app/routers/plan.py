from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import today
from app.models import KIND_NOTE, KIND_RECIPE, PlanEntry
from app.services import history
from app.services import recipes as recipe_service
from app.services import shopping as shopping_service
from app.web import Ctx, get_ctx, redirect, render

router = APIRouter(prefix="/s/{token}")


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _week_entries(ctx: Ctx, start: date) -> dict[date, list[PlanEntry]]:
    entries = ctx.db.scalars(
        select(PlanEntry)
        .options(selectinload(PlanEntry.recipe))
        .where(PlanEntry.space_id == ctx.space.id, PlanEntry.day >= start, PlanEntry.day <= start + timedelta(days=6))
        .order_by(PlanEntry.day, PlanEntry.position, PlanEntry.id)
    ).all()
    by_day: dict[date, list[PlanEntry]] = {start + timedelta(days=i): [] for i in range(7)}
    for entry in entries:
        by_day[entry.day].append(entry)
    return by_day


def _week_context(ctx: Ctx, start: date) -> dict:
    return {
        "start": start,
        "end": start + timedelta(days=6),
        "week_number": start.isocalendar().week,
        "prev_week": start - timedelta(days=7),
        "next_week": start + timedelta(days=7),
        "days": _week_entries(ctx, start),
    }


def _respond_week(ctx: Ctx, day: date, toast: str | None = None):
    """Nach einer Änderung: nur die Wochenansicht (HTMX) oder Redirect (normales Formular)."""
    start = monday_of(day)
    if ctx.is_partial:
        return render(ctx, "partials/week.html", toast=toast, **_week_context(ctx, start))
    return redirect(f"{ctx.base}/plan?week={start.isoformat()}")


def _get_entry(ctx: Ctx, entry_id: int) -> PlanEntry:
    entry = ctx.db.get(PlanEntry, entry_id)
    if entry is None or entry.space_id != ctx.space.id:
        raise HTTPException(404, "Eintrag nicht gefunden")
    return entry


def _next_position(ctx: Ctx, day: date) -> int:
    last = ctx.db.scalars(
        select(PlanEntry.position)
        .where(PlanEntry.space_id == ctx.space.id, PlanEntry.day == day)
        .order_by(PlanEntry.position.desc())
        .limit(1)
    ).first()
    return 0 if last is None else last + 1


def _label(entry: PlanEntry) -> str:
    return entry.recipe.title if entry.kind == KIND_RECIPE and entry.recipe else (entry.note or "Notiz")


@router.get("/plan")
def plan_page(ctx: Annotated[Ctx, Depends(get_ctx)], week: date | None = None):
    start = monday_of(week or today())
    return render(ctx, "plan.html", **_week_context(ctx, start))


@router.post("/plan/note")
def add_note(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    day: Annotated[date, Form()],
    text: Annotated[str, Form(max_length=300)],
):
    text = text.strip()
    if text:
        with history.record(ctx.db, ctx.space.id, ctx.member.name, f"Notiz „{text[:60]}“ für {day:%d.%m.} hinzugefügt") as act:
            entry = PlanEntry(
                space_id=ctx.space.id, day=day, position=_next_position(ctx, day), kind=KIND_NOTE, note=text
            )
            ctx.db.add(entry)
            ctx.db.flush()
            act.created(entry)
    return _respond_week(ctx, day, "Notiz hinzugefügt" if text else None)


@router.get("/plan/pick")
def pick_recipe(ctx: Annotated[Ctx, Depends(get_ctx)], day: date, q: Annotated[str, Query(max_length=100)] = ""):
    return render(ctx, "plan_pick.html", day=day, q=q, recipes=recipe_service.search_recipes(ctx.db, ctx.space.id, q))


@router.post("/plan/recipe")
def add_recipe_to_plan(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    day: Annotated[date, Form()],
    recipe_id: Annotated[int, Form()],
):
    recipe = recipe_service.get_recipe(ctx.db, ctx.space.id, recipe_id)
    if recipe is None:
        raise HTTPException(404, "Rezept nicht gefunden")
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"„{recipe.title}“ für {day:%d.%m.} eingeplant") as act:
        entry = PlanEntry(
            space_id=ctx.space.id, day=day, position=_next_position(ctx, day), kind=KIND_RECIPE, recipe_id=recipe.id
        )
        ctx.db.add(entry)
        ctx.db.flush()
        act.created(entry)
    return _respond_week(ctx, day, f"„{recipe.title}“ eingeplant")


@router.post("/plan/entries/{entry_id}/delete")
def delete_entry(ctx: Annotated[Ctx, Depends(get_ctx)], entry_id: int):
    entry = _get_entry(ctx, entry_id)
    day, label = entry.day, _label(entry)
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"„{label[:60]}“ vom {day:%d.%m.} entfernt") as act:
        act.deleting(entry)
    return _respond_week(ctx, day, f"„{label[:40]}“ entfernt")


@router.post("/plan/entries/{entry_id}/move")
def move_entry(ctx: Annotated[Ctx, Depends(get_ctx)], entry_id: int, direction: Annotated[str, Form(pattern="^(up|down)$")]):
    entry = _get_entry(ctx, entry_id)
    siblings = list(
        ctx.db.scalars(
            select(PlanEntry)
            .where(PlanEntry.space_id == ctx.space.id, PlanEntry.day == entry.day)
            .order_by(PlanEntry.position, PlanEntry.id)
        )
    )
    index = siblings.index(entry)
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(siblings):
        siblings[index], siblings[target] = siblings[target], siblings[index]
        with history.record(ctx.db, ctx.space.id, ctx.member.name, f"„{_label(entry)[:60]}“ verschoben") as act:
            for position, sibling in enumerate(siblings):
                if sibling.position != position:
                    act.updating(sibling)
                    sibling.position = position
    return _respond_week(ctx, entry.day)


@router.post("/plan/entries/{entry_id}/cooked")
def toggle_cooked(ctx: Annotated[Ctx, Depends(get_ctx)], entry_id: int):
    entry = _get_entry(ctx, entry_id)
    state = "als gekocht markiert" if not entry.cooked else "nicht mehr als gekocht markiert"
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"„{_label(entry)[:60]}“ {state}") as act:
        act.updating(entry)
        entry.cooked = not entry.cooked
    return _respond_week(ctx, entry.day)


@router.post("/plan/entries/{entry_id}/shop")
def shop_entry(ctx: Annotated[Ctx, Depends(get_ctx)], entry_id: int):
    """Alle Zutaten des eingeplanten Rezepts auf die Einkaufsliste setzen."""
    entry = _get_entry(ctx, entry_id)
    if entry.kind != KIND_RECIPE or entry.recipe is None:
        raise HTTPException(400, "Nur Rezepte haben Zutaten")
    recipe = entry.recipe
    with history.record(
        ctx.db, ctx.space.id, ctx.member.name, f"Zutaten von „{recipe.title[:60]}“ auf die Einkaufsliste gesetzt"
    ) as act:
        count = shopping_service.add_recipe_ingredients(ctx.db, act, ctx.space.id, recipe.id, recipe.ingredients)
    return _respond_week(ctx, entry.day, f"{count} Zutaten auf der Einkaufsliste")
