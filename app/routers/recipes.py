from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Query

from app.config import now, today
from app.models import Recipe
from app.services import history
from app.services import recipes as recipe_service
from app.services import shopping as shopping_service
from app.services.ingredients import format_ingredient, parse_lines
from app.web import Ctx, get_ctx, redirect, render

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
    return render(ctx, "recipe_form.html", recipe=None, ingredient_text="")


@router.post("/recipes/new")
def create_recipe(
    ctx: Annotated[Ctx, Depends(get_ctx)],
    title: Annotated[str, Form(max_length=200)],
    servings: Annotated[str, Form(max_length=5)] = "",
    ingredients: Annotated[str, Form(max_length=10000)] = "",
    steps: Annotated[str, Form(max_length=20000)] = "",
):
    if not title.strip():
        raise HTTPException(400, "Titel fehlt")
    with history.record(ctx.db, ctx.space.id, ctx.member.name, f"Rezept „{title.strip()[:60]}“ angelegt") as act:
        recipe = recipe_service.create_recipe(
            act,
            ctx.db,
            ctx.space.id,
            ctx.member.name,
            title,
            recipe_service.parse_servings(servings),
            steps,
            parse_lines(ingredients),
        )
    return redirect(f"{ctx.base}/recipes/{recipe.id}")


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
        recipe.steps = steps.strip()
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
