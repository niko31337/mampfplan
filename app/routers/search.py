from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import select

from app.models import Recipe
from app.recipe_sources import SourceError, get_source, get_sources
from app.services import history
from app.services import recipes as recipe_service
from app.web import Ctx, get_ctx, redirect, render

router = APIRouter(prefix="/s/{token}")

DEFAULT_SOURCE = "chefkoch"


def _source_or_404(key: str):
    source = get_source(key)
    if source is None:
        raise HTTPException(404, "Unbekannte Rezeptquelle")
    return source


@router.get("/search")
def search_page(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    q: Annotated[str, Query(max_length=100)] = "",
    source: Annotated[str, Query(max_length=20)] = DEFAULT_SOURCE,
    day: date | None = None,
):
    sources = get_sources()
    active = sources.get(source) or sources.get(DEFAULT_SOURCE) or next(iter(sources.values()))
    results, error = [], None
    if q.strip():
        try:
            results = active.search(q.strip())
        except SourceError as exc:
            error = str(exc)
    return render(ctx, "search.html", q=q, sources=sources, active=active, results=results, error=error, day=day)


@router.get("/search/preview")
def preview(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    source: Annotated[str, Query(max_length=20)],
    id: Annotated[str, Query(max_length=40)],
    day: date | None = None,
):
    src = _source_or_404(source)
    try:
        recipe = src.get(id)
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc
    return render(ctx, "search_preview.html", recipe=recipe, source=src, day=day)


@router.post("/search/import")
def import_recipe(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    source: Annotated[str, Form(max_length=20)],
    external_id: Annotated[str, Form(max_length=40)],
    day: Annotated[date | None, Form()] = None,
):
    src = _source_or_404(source)
    try:
        external = src.get(external_id)
    except SourceError as exc:
        raise HTTPException(502, str(exc)) from exc

    suffix = f"?plan_date={day.isoformat()}" if day else ""
    if external.source_url:
        existing = ctx.db.scalars(
            select(Recipe).where(
                Recipe.space_id == ctx.space.id,
                Recipe.source == external.source,
                Recipe.source_url == external.source_url,
                Recipe.deleted_at.is_(None),
            )
        ).first()
        if existing:
            return redirect(f"{ctx.base}/recipes/{existing.id}{suffix}")

    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"Rezept „{external.title[:60]}“ importiert") as act:
        recipe = recipe_service.create_recipe(
            act,
            ctx.db,
            ctx.space.id,
            ctx.member.name,
            external.title,
            external.servings,
            external.steps,
            [(i.amount, i.unit, i.name) for i in external.ingredients],
            image_url=external.image_url,
            source=external.source,
            source_url=external.source_url,
        )
    return redirect(f"{ctx.base}/recipes/{recipe.id}{suffix}")
