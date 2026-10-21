import httpx
import pytest

from app.recipe_sources import SourceError, get_sources
from app.recipe_sources.chefkoch import ChefkochSource
from app.recipe_sources.mealdb import MealDBSource
from app.recipe_sources.spoonacular import SpoonacularSource


def client_for(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_mealdb_parses_ingredients_and_measures():
    def handler(request):
        assert request.url.params["s"] == "pasta"
        return httpx.Response(
            200,
            json={
                "meals": [
                    {
                        "idMeal": "1",
                        "strMeal": "Pasta",
                        "strMealThumb": "https://img.example/pasta.jpg",
                        "strInstructions": "Kochen.\r\nEssen.",
                        "strSource": "javascript:alert(1)",
                        "strIngredient1": "Spaghetti",
                        "strMeasure1": "200g/7oz",
                        "strIngredient2": "Salt",
                        "strMeasure2": "Pinch",
                        "strIngredient3": "Egg",
                        "strMeasure3": "2",
                        "strIngredient4": "",
                        "strMeasure4": "",
                    }
                ]
            },
        )

    (recipe,) = MealDBSource(client_for(handler)).search("pasta")
    assert recipe.title == "Pasta"
    assert recipe.steps == "Kochen.\nEssen."
    assert recipe.source_url == "https://www.themealdb.com/meal/1"  # unsichere URL verworfen
    spaghetti, salt, egg = recipe.ingredients
    assert (spaghetti.amount, spaghetti.unit, spaghetti.name) == (200.0, "g", "Spaghetti (7oz)")
    assert (salt.amount, salt.name) == (None, "Salt (Pinch)")
    assert (egg.amount, egg.unit, egg.name) == (2.0, "", "Egg")


def test_chefkoch_search_and_detail():
    def handler(request):
        if request.url.path == "/v2/recipes":
            assert request.url.params["query"] == "Lasagne"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "recipe": {
                                "id": "123",
                                "title": "Lasagne",
                                "previewImageUrlTemplate": "https://img.chefkoch-cdn.de/x/<format>/l.jpg",
                                "siteUrl": "https://www.chefkoch.de/rezepte/123/Lasagne.html",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "123",
                "title": "Lasagne",
                "servings": 4,
                "instructions": "Schichten.",
                "siteUrl": "https://www.chefkoch.de/rezepte/123/Lasagne.html",
                "ingredientGroups": [
                    {
                        "header": "",
                        "ingredients": [
                            {"name": "Zwiebel(n)", "unit": "", "amount": 1.0, "usageInfo": "gewürfelt"},
                            {"name": "Salz", "unit": "", "amount": 0.0, "usageInfo": ""},
                        ],
                    }
                ],
            },
        )

    source = ChefkochSource(client_for(handler))
    (hit,) = source.search("Lasagne")
    assert hit.image_url == "https://img.chefkoch-cdn.de/x/crop-360x240/l.jpg"
    detail = source.get(hit.external_id)
    assert detail.servings == 4 and detail.steps == "Schichten."
    assert [(i.name, i.amount) for i in detail.ingredients] == [("Zwiebel(n) (gewürfelt)", 1.0), ("Salz", None)]


def test_source_errors_are_wrapped():
    source = ChefkochSource(client_for(lambda request: httpx.Response(500)))
    with pytest.raises(SourceError):
        source.search("x")
    with pytest.raises(SourceError):
        ChefkochSource(client_for(lambda request: httpx.Response(200, text="<html>")))._search("x")


def test_invalid_external_id_is_rejected_before_any_request():
    def handler(request):
        raise AssertionError("darf nicht aufgerufen werden")

    with pytest.raises(SourceError):
        ChefkochSource(client_for(handler)).get("../../etc/passwd")


def test_results_are_cached():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"meals": None})

    source = MealDBSource(client_for(handler))
    source.search("a")
    source.search("A")
    assert len(calls) == 1


def test_spoonacular_only_enabled_with_key():
    assert not SpoonacularSource(None).enabled
    assert SpoonacularSource("abc").enabled
    assert "spoonacular" not in get_sources()


def test_import_via_web_creates_independent_recipe(web, monkeypatch):
    from app.recipe_sources.base import ExternalIngredient, ExternalRecipe

    external = ExternalRecipe(
        source="mealdb",
        external_id="52795",
        title="Chicken Handi",
        source_url="https://www.themealdb.com/meal/52795",
        steps="Cook.",
        ingredients=[ExternalIngredient("Chicken", 1.0, "kg")],
    )
    monkeypatch.setattr(MealDBSource, "_get", lambda self, external_id: external)
    monkeypatch.setattr(MealDBSource, "_search", lambda self, query: [external])

    assert "Chicken Handi" in web.get("/search?source=mealdb&q=chicken").text
    preview = web.get("/search/preview?source=mealdb&id=52795")
    assert preview.status_code == 200 and "1 kg Chicken" in preview.text

    first = web.post("/search/import", {"source": "mealdb", "external_id": "52795", "day": "2026-10-21"})
    assert first.status_code == 303 and "plan_date=2026-10-21" in first.headers["location"]
    again = web.post("/search/import", {"source": "mealdb", "external_id": "52795"})
    assert again.headers["location"].split("?")[0] == first.headers["location"].split("?")[0]  # kein Duplikat
    assert "Chicken" in web.get(first.headers["location"].split("?")[0].removeprefix(web.base)).text


def test_unknown_source_is_404(web):
    assert web.get("/search/preview?source=evil&id=1").status_code == 404
    assert web.post("/search/import", {"source": "evil", "external_id": "1"}).status_code == 404


def test_search_survives_unreachable_source(web, monkeypatch):
    def boom(self, query):
        raise SourceError("Chefkoch ist gerade nicht erreichbar")

    monkeypatch.setattr(ChefkochSource, "_search", boom)
    page = web.get("/search?source=chefkoch&q=lasagne")
    assert page.status_code == 200 and "nicht erreichbar" in page.text
