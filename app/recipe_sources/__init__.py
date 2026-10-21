from functools import lru_cache

from app.config import get_settings
from app.recipe_sources.base import ExternalRecipe, RecipeSource, SourceError
from app.recipe_sources.chefkoch import ChefkochSource
from app.recipe_sources.mealdb import MealDBSource
from app.recipe_sources.spoonacular import SpoonacularSource

__all__ = ["ExternalRecipe", "RecipeSource", "SourceError", "get_sources", "get_source"]


@lru_cache
def get_sources() -> dict[str, RecipeSource]:
    """Alle aktivierten Rezeptquellen (Spoonacular nur mit API-Key)."""
    candidates = [ChefkochSource(), MealDBSource(), SpoonacularSource(get_settings().spoonacular_api_key)]
    return {s.key: s for s in candidates if s.enabled}


def get_source(key: str) -> RecipeSource | None:
    return get_sources().get(key)
