from sqlalchemy import select

from app.db import SessionLocal
from app.models import CatalogItem, ShoppingItem
from tests.test_plan import make_recipe
from tests.test_shopping_categories import item_names


def items():
    with SessionLocal() as db:
        return [(i.name, i.amount, i.unit, i.checked, i.category) for i in db.scalars(select(ShoppingItem).order_by(ShoppingItem.id))]


def first_id():
    with SessionLocal() as db:
        return db.scalars(select(ShoppingItem.id).order_by(ShoppingItem.id)).first()


def test_catalog_is_seeded(web):
    with SessionLocal() as db:
        names = set(db.scalars(select(CatalogItem.name)))
    assert {"Milch", "Zahnpasta", "Spülmittel", "Toilettenpapier"} <= names


def test_add_parses_amount_and_guesses_category(web):
    web.post("/shopping/add", {"name": "2 l Milch"})
    assert items() == [("Milch", 2.0, "l", False, "Milch, Käse & Eier")]


def test_add_free_text_item(web):
    web.post("/shopping/add", {"name": "Tintenpatronen"})
    assert items() == [("Tintenpatronen", None, "", False, "Sonstiges")]


def test_category_is_guessed_from_partial_catalog_match(web):
    web.post("/shopping/add", {"name": "Geburtstagskerzen"})
    assert items()[0][4] == "Haushalt & Reinigung"


def test_same_item_is_merged(web):
    web.post("/shopping/add", {"name": "500 g Mehl"})
    web.post("/shopping/add", {"name": "250 g Mehl"})
    assert items() == [("Mehl", 750.0, "g", False, "Vorrat & Konserven")]
    web.post("/undo")
    assert items() == [("Mehl", 500.0, "g", False, "Vorrat & Konserven")]


def test_different_unit_is_not_merged(web):
    web.post("/shopping/add", {"name": "1 kg Mehl"})
    web.post("/shopping/add", {"name": "2 Packungen Mehl"})
    assert len(items()) == 2


def test_check_off_removes_from_open_list_and_undo(web):
    web.post("/shopping/add", {"name": "Milch"})
    web.post("/shopping/add", {"name": "Brot"})
    item_id = first_id()
    response = web.post(f"/shopping/{item_id}/toggle", htmx=True)
    assert response.status_code == 200
    page = response.text
    open_part, done_part = page.split("<h2>Gekauft</h2>", 1)
    # Artikelnamen gezielt prüfen: die (leere) Kategorie „Milch, Käse & Eier“ enthält ebenfalls „Milch“
    assert item_names(open_part) == ["Brot"]
    assert item_names(done_part) == ["Milch"]

    web.post("/undo")
    assert items()[0][3] is False


def test_clear_checked_and_undo(web):
    web.post("/shopping/add", {"name": "Milch"})
    web.post(f"/shopping/{first_id()}/toggle")
    web.post("/shopping/clear-checked")
    assert items() == []
    web.post("/undo")
    assert len(items()) == 1 and items()[0][3] is True


def test_recipe_ingredients_to_list_with_selection(web):
    recipe_id = make_recipe(web, "Pfannkuchen", "250 g Mehl\n3 Eier\n500 ml Milch")
    page = web.get(f"/recipes/{recipe_id}").text
    assert "Auswahl auf die Einkaufsliste" in page
    with SessionLocal() as db:
        from app.models import RecipeIngredient

        ids = {i.name: i.id for i in db.scalars(select(RecipeIngredient))}
    web.client.post(
        f"{web.base}/recipes/{recipe_id}/shop",
        data={"ingredient_ids": [ids["Mehl"], ids["Eier"]]},
        headers={"X-CSRF-Token": web.client.cookies.get("mp_csrf")},
        follow_redirects=False,
    )
    assert sorted(i[0] for i in items()) == ["Eier", "Mehl"]


def test_suggest_and_catalog_search(web):
    suggestions = web.get("/shopping/suggest?name=zahn").text
    assert "Zahnpasta" in suggestions and "Zahnbürste" in suggestions
    assert "Zahnpasta" in web.get("/shopping/catalog?q=zahnpasta").text


def test_suggest_escapes_like_wildcards(web):
    assert "Zahnpasta" not in web.get("/shopping/suggest?name=%25").text


def test_catalog_page_marks_items_on_list(web):
    web.post("/shopping/add", {"name": "Milch"})
    assert "✓ Milch" in web.get("/shopping/catalog").text
