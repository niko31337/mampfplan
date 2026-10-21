from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Recipe, RecipeIngredient
from app.recipe_sources.base import safe_https_url
from app.services.history import Action


def create_recipe(
    act: Action,
    db: Session,
    space_id: int,
    created_by: str,
    title: str,
    servings: int | None,
    steps: str,
    ingredients: list[tuple[float | None, str, str]],
    image_url: str | None = None,
    source: str = "manual",
    source_url: str | None = None,
) -> Recipe:
    recipe = Recipe(
        space_id=space_id,
        title=title.strip()[:200],
        servings=servings,
        image_url=safe_https_url(image_url),
        source=source,
        source_url=safe_https_url(source_url),
        steps=steps.strip()[:20000],
        created_by=created_by,
    )
    db.add(recipe)
    db.flush()
    act.created(recipe)
    _add_ingredients(act, db, recipe, ingredients)
    return recipe


def replace_ingredients(act: Action, db: Session, recipe: Recipe, ingredients: list[tuple[float | None, str, str]]) -> None:
    for old in list(recipe.ingredients):
        act.deleting(old)
    db.flush()
    _add_ingredients(act, db, recipe, ingredients)


def _add_ingredients(act: Action, db: Session, recipe: Recipe, ingredients: list[tuple[float | None, str, str]]) -> None:
    for position, (amount, unit, name) in enumerate(ingredients):
        ing = RecipeIngredient(recipe_id=recipe.id, position=position, name=name[:100], amount=amount, unit=unit[:20])
        db.add(ing)
        db.flush()
        act.created(ing)
    db.refresh(recipe, ["ingredients"])


def parse_servings(raw: str) -> int | None:
    raw = raw.strip()
    if raw.isdigit() and 0 < int(raw) <= 100:
        return int(raw)
    return None


def get_recipe(db: Session, space_id: int, recipe_id: int) -> Recipe | None:
    recipe = db.get(Recipe, recipe_id)
    if recipe is None or recipe.space_id != space_id or recipe.deleted_at is not None:
        return None
    return recipe


def search_recipes(db: Session, space_id: int, query: str = "") -> list[Recipe]:
    stmt = select(Recipe).where(Recipe.space_id == space_id, Recipe.deleted_at.is_(None)).order_by(Recipe.title)
    if query.strip():
        pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
        stmt = stmt.where(
            Recipe.title.ilike(pattern, escape="\\")
            | Recipe.id.in_(
                select(RecipeIngredient.recipe_id).where(RecipeIngredient.name.ilike(pattern, escape="\\"))
            )
        )
    return list(db.scalars(stmt))
