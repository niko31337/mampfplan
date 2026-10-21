"""Chefkoch.de über die inoffizielle, undokumentierte JSON-API (api.chefkoch.de/v2).

Es gibt keine offizielle API: Die Quelle kann sich jederzeit ändern oder wegfallen.
Fehler bleiben deshalb auf diese Quelle beschränkt (SourceError).
"""

from app.recipe_sources.base import ExternalIngredient, ExternalRecipe, RecipeSource, SourceError, safe_https_url

BASE = "https://api.chefkoch.de/v2"
IMAGE_FORMAT = "crop-360x240"


def _image(template: str | None) -> str | None:
    return safe_https_url(template.replace("<format>", IMAGE_FORMAT)) if template else None


class ChefkochSource(RecipeSource):
    key = "chefkoch"
    label = "Chefkoch"
    language = "de"

    def _search(self, query: str) -> list[ExternalRecipe]:
        data = self._json(f"{BASE}/recipes", params={"query": query, "limit": 20, "offset": 0})
        results = []
        for item in data.get("results") or []:
            recipe = item.get("recipe") or {}
            if not recipe.get("id") or not recipe.get("title"):
                continue
            results.append(
                ExternalRecipe(
                    source=self.key,
                    external_id=str(recipe["id"]),
                    title=recipe["title"][:200],
                    image_url=_image(recipe.get("previewImageUrlTemplate")),
                    source_url=safe_https_url(recipe.get("siteUrl")),
                )
            )
        return results

    def _get(self, external_id: str) -> ExternalRecipe:
        data = self._json(f"{BASE}/recipes/{external_id}")
        if not data.get("title"):
            raise SourceError("Rezept nicht gefunden")
        ingredients = []
        for group in data.get("ingredientGroups") or []:
            for ing in group.get("ingredients") or []:
                name = (ing.get("name") or "").strip()
                if not name:
                    continue
                usage = (ing.get("usageInfo") or "").strip(" ,;")
                if usage:
                    name = f"{name} ({usage})"
                amount = ing.get("amount")
                ingredients.append(
                    ExternalIngredient(
                        name=name[:100],
                        amount=float(amount) if isinstance(amount, (int, float)) and amount else None,
                        unit=(ing.get("unit") or "").strip()[:20],
                    )
                )
        steps = (data.get("instructions") or "").replace("\r\n", "\n").strip()
        return ExternalRecipe(
            source=self.key,
            external_id=str(data["id"]),
            title=data["title"][:200],
            image_url=_image(data.get("previewImageUrlTemplate")),
            source_url=safe_https_url(data.get("siteUrl")),
            servings=data.get("servings") if isinstance(data.get("servings"), int) else None,
            steps=steps,
            ingredients=ingredients,
        )
