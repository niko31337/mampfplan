"""Recipe view: change the servings and scale the ingredients (and what goes on the shopping list)."""

import html

import pytest
from sqlalchemy import select

from app.converter import tidy_ingredient
from app.db import SessionLocal
from app.models import RecipeIngredient, ShoppingItem
from app.services.ingredients import scale_amount


def make_recipe(web, servings: str = "4", ingredients: str = "400 g Mehl\n3 Eier\n1 EL Öl\nSalz") -> int:
    response = web.post(
        "/recipes/new", {"title": "Pfannkuchen", "ingredients": ingredients, "steps": "Backen.", "servings": servings}
    )
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


def ingredient_ids(recipe_id: int) -> list[int]:
    with SessionLocal() as db:
        return list(db.scalars(select(RecipeIngredient.id).where(RecipeIngredient.recipe_id == recipe_id)))


def shopping_items() -> dict[str, tuple[float | None, str]]:
    with SessionLocal() as db:
        return {i.name: (i.amount, i.unit) for i in db.scalars(select(ShoppingItem))}


def page_text(response) -> str:
    return html.unescape(response.text)


# --- Scaling --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "amount, unit, factor, expected",
    [
        (400, "g", 0.5, 200),  # the example from the wish list
        (450, "g", 0.5, 225),
        (250, "g", 0.75, 190),  # steps of 5 from 100 g
        (3, "", 0.5, 1.5),  # pieces: halves
        (1, "EL", 1 / 3, 0.25),  # spoons: quarters
        (1, "l", 1 / 3, 0.33),  # litres: two decimals
        (200, "ml", 1.5, 300),
        (5, "g", 0.1, 0.5),  # never rounds down to nothing
        (None, "", 0.5, None),  # „Salz“ without an amount stays without
        (400, "g", 1, 400),
    ],
)
def test_scale_amount(amount, unit, factor, expected):
    assert scale_amount(amount, unit, factor) == expected


# --- Recipe view ----------------------------------------------------------------------------------


def test_recipe_view_shows_the_recipes_servings_by_default(web):
    recipe = make_recipe(web)
    page = page_text(web.get(f"/recipes/{recipe}"))
    assert "4 Portionen" in page and "400 g Mehl" in page
    assert 'name="servings" value="4"' in page
    assert "Umgerechnet" not in page


def test_fewer_servings_scale_the_ingredients(web):
    recipe = make_recipe(web)
    page = page_text(web.get(f"/recipes/{recipe}?servings=2"))
    assert "2 Portionen" in page
    assert "200 g Mehl" in page and "1,5 Eier" in page and "0,5 EL Öl" in page and "Salz" in page
    assert "Umgerechnet vom Rezept für 4 Portionen." in page
    assert 'name="servings" value="2"' in page


def test_stepper_links_keep_other_parameters(web):
    recipe = make_recipe(web)
    page = page_text(web.get(f"/recipes/{recipe}?plan_date=2026-11-20&servings=3"))
    assert "plan_date=2026-11-20&servings=2" in page and "plan_date=2026-11-20&servings=4" in page
    assert 'hx-select="#ingredients"' in page and 'id="ingredients"' in page


def test_one_serving_has_no_minus_link(web):
    recipe = make_recipe(web)
    page = page_text(web.get(f"/recipes/{recipe}?servings=1"))
    assert "servings=0" not in page and 'aria-disabled="true">−' in page


def test_recipe_without_servings_counts_as_four(web):
    recipe = make_recipe(web, servings="")
    page = page_text(web.get(f"/recipes/{recipe}"))
    assert "4 Portionen" in page and "Ohne Portionsangabe gilt ein Rezept als für 4 Portionen." in page
    assert "100 g Mehl" in page_text(web.get(f"/recipes/{recipe}?servings=1"))


def test_recipe_for_two_doubled(web):
    recipe = make_recipe(web, servings="2", ingredients="150 g Spaghetti")
    assert "300 g Spaghetti" in page_text(web.get(f"/recipes/{recipe}?servings=4"))


@pytest.mark.parametrize("servings", ["0", "51", "abc"])
def test_invalid_servings_are_rejected(web, servings):
    recipe = make_recipe(web)
    assert web.get(f"/recipes/{recipe}?servings={servings}").status_code == 422


def test_servings_are_translated(web):
    recipe = make_recipe(web)
    web.client.cookies.set("mp_lang", "it")
    page = page_text(web.get(f"/recipes/{recipe}?servings=2"))
    assert "2 porzioni" in page and "Ricalcolato dalla ricetta per 4 porzioni." in page and "Meno porzioni" in page


# --- Shopping list ------------------------------------------------------------------------------


def test_shopping_list_gets_the_scaled_amounts(web):
    recipe = make_recipe(web)
    web.post(f"/recipes/{recipe}/shop", {"ingredient_ids": ingredient_ids(recipe), "servings": "2"})
    items = shopping_items()
    assert items["Mehl"] == (200, "g") and items["Eier"] == (1.5, "") and items["Salz"] == (None, "")


def test_shopping_list_without_servings_uses_the_recipe(web):
    recipe = make_recipe(web)
    web.post(f"/recipes/{recipe}/shop", {"ingredient_ids": ingredient_ids(recipe)})
    assert shopping_items()["Mehl"] == (400, "g")


def test_shopping_list_rejects_invalid_servings(web):
    recipe = make_recipe(web)
    assert web.post(f"/recipes/{recipe}/shop", {"ingredient_ids": ingredient_ids(recipe), "servings": "0"}).status_code == 422
    assert shopping_items() == {}


# --- Converter: amount after the name -------------------------------------------------------------


@pytest.mark.parametrize(
    "line, expected",
    [
        ("Spaghetti 320 g", "320 g Spaghetti"),
        ("Tuorli (di uova medie) 6", "6 Tuorli (di uova medie)"),
        ("Pecorino Romano DOP 50 g", "50 g Pecorino Romano DOP"),
        ("Farina 00 300 g", "300 g Farina 00"),
        ("Aglio 2 spicchi", "2 spicchi Aglio"),
        ("Zucker, 100g", "100 g Zucker"),
        ("Milch 1/2 l", "1/2 l Milch"),
        ("Salz 1 Prise", "1 Prise Salz"),
        # unchanged: the number belongs to the name, no amount, or not a known unit
        ("Mehl Type 405", "Mehl Type 405"),
        ("Farina tipo 00", "Farina tipo 00"),
        ("Pepe nero q.b.", "Pepe nero q.b."),
        ("Vitamin B12", "Vitamin B12"),
        ("Hähnchen 2 Brüste", "Hähnchen 2 Brüste"),
        ("200 g Mehl", "200 g Mehl"),
    ],
)
def test_converter_moves_trailing_amounts_to_the_front(line, expected):
    assert tidy_ingredient(line) == expected
