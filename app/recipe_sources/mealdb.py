from app.recipe_sources.base import ExternalIngredient, ExternalRecipe, RecipeSource, SourceError, safe_https_url
from app.services.ingredients import parse_measure

BASE = "https://www.themealdb.com/api/json/v1/1"


class MealDBSource(RecipeSource):
    key = "mealdb"
    label = "TheMealDB"
    language = "en"

    def _search(self, query: str) -> list[ExternalRecipe]:
        data = self._json(f"{BASE}/search.php", params={"s": query})
        return [self._to_recipe(meal) for meal in (data.get("meals") or [])[:20]]

    def _get(self, external_id: str) -> ExternalRecipe:
        data = self._json(f"{BASE}/lookup.php", params={"i": external_id})
        meals = data.get("meals") or []
        if not meals:
            raise SourceError("Rezept nicht gefunden")
        return self._to_recipe(meals[0])

    @staticmethod
    def _to_recipe(meal: dict) -> ExternalRecipe:
        ingredients = []
        for i in range(1, 21):
            name = (meal.get(f"strIngredient{i}") or "").strip()
            if not name:
                continue
            measure = (meal.get(f"strMeasure{i}") or "").strip()
            amount, unit, extra = parse_measure(measure) if measure else (None, "", "")
            extra = extra.lstrip(" /,;")
            if amount is None and measure:  # z. B. "Pinch", "to taste"
                extra = measure
            if extra:
                name = f"{name} ({extra})"
            ingredients.append(ExternalIngredient(name=name[:100], amount=amount, unit=unit[:20]))
        return ExternalRecipe(
            source="mealdb",
            external_id=str(meal["idMeal"]),
            title=(meal.get("strMeal") or "Ohne Titel")[:200],
            image_url=safe_https_url(meal.get("strMealThumb")),
            source_url=safe_https_url(meal.get("strSource")) or f"https://www.themealdb.com/meal/{meal['idMeal']}",
            steps=(meal.get("strInstructions") or "").replace("\r\n", "\n").strip(),
            ingredients=ingredients,
        )
