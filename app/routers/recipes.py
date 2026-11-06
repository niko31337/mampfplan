import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query
from sqlalchemy import select

from app import converter
from app.config import now, today
from app.models import Recipe
from app.recipe_sources.base import safe_https_url
from app.services import history, images
from app.services import recipes as recipe_service
from app.services import shopping as shopping_service
from app.services.ingredients import format_ingredient, parse_lines
from app.web import Ctx, get_ctx, redirect, render

log = logging.getLogger("mampfplan")
router = APIRouter(prefix="/s/{token}")


def _get_or_404(ctx: Ctx, recipe_id: int) -> Recipe:
    recipe = recipe_service.get_recipe(ctx.db, ctx.space.id, recipe_id)
    if recipe is None:
        raise HTTPException(404, "Rezept nicht gefunden")
    return recipe


@router.get("/recipes")
def recipe_list(ctx: Annotated[Ctx, Depends(get_ctx)], q: Annotated[str, Query(max_length=100)] = ""):
    return render(ctx, "recipes.html", q=q, recipes=recipe_service.search_recipes(ctx.db, ctx.space.id, q))


@router.get("/recipes/new")
def new_recipe_form(ctx: Annotated[Ctx, Depends(get_ctx)]):
    return render(ctx, "recipe_form.html", recipe=None, ingredient_text="", title="", servings=None, steps="")


@router.post("/recipes/new")
def create_recipe(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    title: Annotated[str, Form(max_length=200)],
    servings: Annotated[str, Form(max_length=5)] = "",
    ingredients: Annotated[str, Form(max_length=10000)] = "",
    steps: Annotated[str, Form(max_length=20000)] = "",
    source: Annotated[str, Form(max_length=20)] = "manual",
    source_url: Annotated[str, Form(max_length=500)] = "",
    image_url: Annotated[str, Form(max_length=500)] = "",
):
    if not title.strip():
        raise HTTPException(400, "Titel fehlt")
    # Aus dem Konverter: Quelle merken und als letzte Zeile der Zubereitung festhalten
    link = safe_https_url(source_url.strip())
    if source not in converter.CONVERTED_SOURCES or not link:
        source, link, image_url = "manual", None, ""
    steps = converter.with_source_line(steps, link)[:20000]
    action = "aus Link übernommen" if link else "angelegt"
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"Rezept „{title.strip()[:60]}“ {action}") as act:
        recipe = recipe_service.create_recipe(
            act,
            ctx.db,
            ctx.space.id,
            ctx.member.name,
            title,
            recipe_service.parse_servings(servings),
            steps,
            parse_lines(ingredients),
            image_url=image_url.strip() or None,
            source=source,
            source_url=link,
        )
    return redirect(f"{ctx.base}/recipes/{recipe.id}")


@router.get("/recipes/convert")
def convert_form(ctx: Annotated[Ctx, Depends(get_ctx)], url: Annotated[str, Query(max_length=2000)] = ""):
    return render(ctx, "recipe_convert.html", url=url, text="", error=None, needs_text=False, source="")


@router.post("/recipes/convert")
def convert_link(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    url: Annotated[str, Form(max_length=2000)],
    text: Annotated[str, Form(max_length=20000)] = "",
):
    """Link abrufen und als bearbeitbaren Vorschlag zeigen – gespeichert wird erst mit „Speichern“."""
    try:
        draft = converter.convert(url, text)
    except converter.FetchError as exc:
        return render(ctx, "recipe_convert.html", url=url, text=text, error=str(exc), needs_text=False, source="", status_code=422)
    if draft.needs_text:
        return render(
            ctx, "recipe_convert.html", url=draft.source_url, text=text, error=None, needs_text=True, source=draft.source
        )

    image_key = _store_image(draft.image_url)
    existing = ctx.db.scalars(
        select(Recipe).where(
            Recipe.space_id == ctx.space.id, Recipe.source_url == draft.source_url, Recipe.deleted_at.is_(None)
        )
    ).first()
    return render(
        ctx,
        "recipe_form.html",
        recipe=None,
        draft=draft,
        title=draft.title,
        servings=draft.servings,
        steps=draft.steps_text,
        ingredient_text=draft.ingredient_text,
        image_key=image_key,
        existing=existing,
    )


def _store_image(url: str | None) -> str | None:
    """Rezeptbild abgesichert laden und im Bild-Cache ablegen; Rückgabe: Adresse für das Rezept."""
    key = converter.image_key(url)
    if not key:
        return None
    try:
        cache = images.get_image_cache()
        if cache.lookup(key):
            return key
        data = converter.fetch_image(url)
        return key if data and cache.store(key, data) else None
    except Exception:  # noqa: BLE001 – ein fehlendes Bild darf den Konverter nie verhindern
        log.exception("Bild konnte nicht übernommen werden")
        return None


@router.get("/recipes/{recipe_id}")
def recipe_detail(ctx: Annotated[Ctx, Depends(get_ctx)], recipe_id: int, plan_date: date | None = None):
    recipe = _get_or_404(ctx, recipe_id)
    return render(ctx, "recipe_detail.html", recipe=recipe, plan_date=plan_date or today())


@router.get("/recipes/{recipe_id}/edit")
def edit_recipe_form(ctx: Annotated[Ctx, Depends(get_ctx)], recipe_id: int):
    recipe = _get_or_404(ctx, recipe_id)
    text = "\n".join(format_ingredient(i.amount, i.unit, i.name) for i in recipe.ingredients)
    return render(ctx, "recipe_form.html", recipe=recipe, ingredient_text=text)


@router.post("/recipes/{recipe_id}/edit")
def update_recipe(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    recipe_id: int,
    title: Annotated[str, Form(max_length=200)],
    servings: Annotated[str, Form(max_length=5)] = "",
    ingredients: Annotated[str, Form(max_length=10000)] = "",
    steps: Annotated[str, Form(max_length=20000)] = "",
):
    recipe = _get_or_404(ctx, recipe_id)
    if not title.strip():
        raise HTTPException(400, "Titel fehlt")
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"Rezept „{title.strip()[:60]}“ bearbeitet") as act:
        act.updating(recipe)
        recipe.title = title.strip()[:200]
        recipe.servings = recipe_service.parse_servings(servings)
        if recipe.source in converter.CONVERTED_SOURCES:  # die Quelle bleibt in der Zubereitung stehen
            steps = converter.with_source_line(steps, recipe.source_url)
        recipe.steps = steps.strip()[:20000]
        recipe_service.replace_ingredients(act, ctx.db, recipe, parse_lines(ingredients))
    return redirect(f"{ctx.base}/recipes/{recipe.id}")


@router.post("/recipes/{recipe_id}/delete")
def delete_recipe(ctx: Annotated[Ctx, Depends(get_ctx)], recipe_id: int):
    recipe = _get_or_404(ctx, recipe_id)
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"Rezept „{recipe.title[:60]}“ gelöscht") as act:
        act.updating(recipe)
        recipe.deleted_at = now()
    return redirect(f"{ctx.base}/recipes")


@router.post("/recipes/{recipe_id}/shop")
def add_to_shopping_list(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    recipe_id: int,
    ingredient_ids: Annotated[list[int] | None, Form()] = None,
):
    recipe = _get_or_404(ctx, recipe_id)
    wanted = set(ingredient_ids or [])
    chosen = [i for i in recipe.ingredients if i.id in wanted]
    if chosen:
        with history.record(
            ctx.db, ctx.space.id, ctx.member.name, f"{len(chosen)} Zutaten von „{recipe.title[:60]}“ auf die Einkaufsliste gesetzt"
        ) as act:
            shopping_service.add_recipe_ingredients(ctx.db, act, ctx.space.id, recipe.id, chosen, added_by=ctx.member.name)
    return redirect(f"{ctx.base}/shopping")
