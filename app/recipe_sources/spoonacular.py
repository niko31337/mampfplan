import re

import httpx

from app.recipe_sources.base import ExternalIngredient, ExternalRecipe, RecipeSource, SourceError, safe_https_url

BASE = "https://api.spoonacular.com"
_TAG = re.compile(r"<[^>]+>")


class SpoonacularSource(RecipeSource):
    key = "spoonacular"
    label = "Spoonacular"
    language = "en"

    def __init__(self, api_key: str | None, client: httpx.Client | None = None):
        super().__init__(client)
        self._api_key = api_key

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key or ""}

    def _search(self, query: str) -> list[ExternalRecipe]:
        data = self._json(
            f"{BASE}/recipes/complexSearch", params={"query": query, "number": 20}, headers=self._headers()
        )
        return [
            ExternalRecipe(
                source=self.key,
                external_id=str(item["id"]),
                title=item["title"][:200],
                image_url=safe_https_url(item.get("image")),
            )
            for item in data.get("results") or []
        ]

    def _get(self, external_id: str) -> ExternalRecipe:
        data = self._json(f"{BASE}/recipes/{external_id}/information", headers=self._headers())
        if not data.get("title"):
            raise SourceError("Rezept nicht gefunden")
        ingredients = [
            ExternalIngredient(
                name=(ing.get("name") or "").strip()[:100],
                amount=ing.get("amount") or None,
                unit=(ing.get("unit") or "").strip()[:20],
            )
            for ing in data.get("extendedIngredients") or []
            if (ing.get("name") or "").strip()
        ]
        steps = self._steps(data)
        return ExternalRecipe(
            source=self.key,
            external_id=str(data["id"]),
            title=data["title"][:200],
            image_url=safe_https_url(data.get("image")),
            source_url=safe_https_url(data.get("sourceUrl")),
            servings=data.get("servings") if isinstance(data.get("servings"), int) else None,
            steps=steps,
            ingredients=ingredients,
        )

    @staticmethod
    def _steps(data: dict) -> str:
        analyzed = data.get("analyzedInstructions") or []
        lines = [s["step"].strip() for block in analyzed for s in block.get("steps") or [] if s.get("step")]
        if lines:
            return "\n".join(lines)
        return _TAG.sub("", (data.get("instructions") or "").replace("</li>", "\n")).strip()
