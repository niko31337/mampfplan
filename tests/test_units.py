"""Amerikanische Maße → metrisch (Rezept-Konverter und Online-Suche)."""

import pytest

from app import converter
from app.converter import fetch as fetching
from app.converter.fetch import Fetched
from app.recipe_sources.base import ExternalIngredient, ExternalRecipe, to_metric
from app.services.units import metric_ingredient, metric_line, metric_lines, metric_text


@pytest.mark.parametrize(
    "line, expected",
    [
        # Tassen: trockene Zutaten über die Dichte in Gramm, Flüssigkeiten in ml
        ("1 cup all-purpose flour", "120 g all-purpose flour"),
        ("1/2 cup sugar", "100 g sugar"),
        ("½ cup brown sugar", "110 g brown sugar"),
        ("1 cup powdered sugar", "120 g powdered sugar"),
        ("1 1/2 cups milk", "360 ml milk"),
        ("1 c. milk", "240 ml milk"),
        ("3 cups flour", "360 g flour"),
        ("1/4 cup sour cream", "60 g sour cream"),
        ("1 cup cooked rice", "160 g cooked rice"),
        ("2-3 cups chicken broth", "480–720 ml chicken broth"),
        ("1 cup chopped mystery thing", "240 ml chopped mystery thing"),  # unbekannt: Volumen
        # Gewicht
        ("8 oz cream cheese", "230 g cream cheese"),
        ("4 oz cheddar", "110 g cheddar"),
        ("2 lbs potatoes", "910 g potatoes"),
        ("3 pounds beef", "1,35 kg beef"),
        ("2 sticks butter", "230 g butter"),
        # Flüssigkeiten
        ("16 fl oz heavy cream", "470 ml heavy cream"),
        ("8 oz orange juice", "240 ml orange juice"),
        ("1 quart water", "950 ml water"),
        ("1 gallon water", "3,8 l water"),
        ("1 pint stock", "470 ml stock"),
        # Löffel bleiben Löffel
        ("2 tbsp soy sauce", "2 EL soy sauce"),
        ("2 Tbsp oil", "2 EL oil"),
        ("1 tsp salt", "1 TL salt"),
        ("1/2 teaspoon pepper", "0,5 TL pepper"),
        # Metrische Angabe in Klammern hat Vorrang
        ("1 lb (450 g) ground chicken or pork", "450 g ground chicken or pork"),
        ("1/3 cup (80ml) pineapple juice", "80 ml pineapple juice"),
        # Nur die Klammer umrechnen
        ("1 (14 oz) can diced tomatoes", "1 (400 g) can diced tomatoes"),
        ("1/2 cup (8 Tbsp; 113g) unsalted butter", "113 g unsalted butter"),
        ("1 and 1/2 cups (345g) mashed bananas", "345 g mashed bananas"),
        ("1 and 1/2 cups flour", "180 g flour"),
        # Mengen mitten in der Zeile
        ("optional: 3/4 cup (90g) pecans, or 1 cup (180g) chocolate chips", "optional: 90 g pecans, or 180 g chocolate chips"),
        ("optional: 1 cup chopped walnuts", "optional: 120 g chopped walnuts"),
    ],
)
def test_us_ingredient_lines(line, expected):
    assert metric_line(line) == expected


@pytest.mark.parametrize(
    "line",
    ["400 g Mehl", "2 EL Öl", "1 Tasse Duftreis", "6 garlic cloves", "18 to 24 wonton wrappers", "Salz nach Geschmack", "1 Cupcake-Form"],
)
def test_metric_and_other_lines_stay_unchanged(line):
    assert metric_line(line) == line


def test_conversions_are_listed():
    lines, changes = metric_lines(["1 cup flour", "2 Eier"])
    assert lines == ["120 g flour", "2 Eier"]
    assert [(c.before, c.after) for c in changes] == [("1 cup flour", "120 g flour")]


def test_instructions():
    text = metric_text(
        "Preheat oven to 350°F. Bake at 350F/175C for 30 mins in a 9x13-inch pan. "
        "Cut into 1-inch pieces. Add 1/2 cup of water and 2 tbsp oil. Grill at 425 degrees F."
    )
    assert text == (
        "Preheat oven to 175 °C. Bake at 175 °C for 30 mins in a 23 x 33 cm pan. "
        "Cut into 2,5 cm pieces. Add 120 ml of water and 2 EL oil. Grill at 220 °C."
    )


def test_instructions_without_us_units_stay_unchanged():
    text = "Bei 180 °C 25 Min. backen. 2 EL Öl und 1/2 Tasse Wasser dazugeben."
    assert metric_text(text) == text


@pytest.mark.parametrize(
    "amount, unit, name, expected",
    [
        (1.0, "cup", "Flour", (120.0, "g", "Flour")),
        (2.0, "tbs", "Olive Oil", (2.0, "EL", "Olive Oil")),
        (1.0, "lb", "Beef", (450.0, "g", "Beef")),
        (0.5, "cups", "milk", (120.0, "ml", "milk")),
        (200.0, "g", "Mehl", (200.0, "g", "Mehl")),
        (None, "", "Salz", (None, "", "Salz")),
        (2.0, "large", "eggs", (2.0, "large", "eggs")),
    ],
)
def test_search_ingredients(amount, unit, name, expected):
    assert metric_ingredient(amount, unit, name) == expected


def test_search_recipes_are_converted():
    recipe = to_metric(
        ExternalRecipe(
            source="mealdb",
            external_id="1",
            title="Pancakes",
            steps="Heat the pan to 375°F.",
            ingredients=[ExternalIngredient("Flour", 1.0, "cup"), ExternalIngredient("Eggs", 2.0, "")],
        )
    )
    assert [(i.amount, i.unit, i.name) for i in recipe.ingredients] == [(120.0, "g", "Flour"), (2.0, "", "Eggs")]
    assert recipe.steps == "Heat the pan to 190 °C."


def test_converter_draft_is_metric(monkeypatch):
    caption = """Banana Bread 🍌

Ingredients:
- 2 cups flour
- 1/2 cup melted butter
- 1 cup milk
- 2 eggs

Instructions:
1. Preheat the oven to 350°F.
2. Bake in a 9x5-inch loaf pan for 60 minutes."""
    page = f'<html><head><meta property="og:title" content="Bäckerei auf Instagram: &quot;{caption}&quot;"></head></html>'
    monkeypatch.setattr(fetching, "fetch", lambda url, **kw: Fetched(url, page.encode(), "text/html", "utf-8"))
    draft = converter.convert("https://www.instagram.com/p/bread/")
    assert draft.ingredient_text == "240 g flour\n110 g melted butter\n240 ml milk\n2 eggs"
    assert len(draft.conversions) == 3 and draft.steps_converted
    assert "1. Preheat the oven to 175 °C." in draft.steps_text
    assert "23 x 13 cm loaf pan" in draft.steps_text


def test_german_draft_is_untouched(monkeypatch):
    draft = converter.convert("https://www.instagram.com/p/x/", "Kuchen\n\nZutaten:\n- 200 g Mehl\n\nZubereitung:\n1. Bei 180 °C backen.")
    assert draft.conversions == [] and not draft.steps_converted
    assert draft.ingredient_text == "200 g Mehl"


def test_preview_lists_the_conversions(web, monkeypatch):
    response = web.post(
        "/recipes/convert",
        {"url": "https://www.instagram.com/p/bread/", "text": "Bread\n\nIngredients:\n- 2 cups flour\n\nInstructions:\n1. Bake at 350°F."},
    )
    page = response.text
    assert "Amerikanische Maße in g/ml umgerechnet (1 Zutaten)" in page
    assert "2 cups flour → <strong>240 g flour</strong>" in page
    assert "Bake at 175 °C." in page
