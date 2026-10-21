import pytest
from sqlalchemy import create_engine, inspect, select, text

from app.db import SessionLocal, migrate
from app.models import PlanEntry, ShoppingItem
from app.services.shopping import clean_name, guess_category
from tests.test_plan import make_recipe


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Karotte(n)", "Karotte"),
        ("Zwiebel(n)", "Zwiebel"),
        ("Knoblauchzehe(n)", "Knoblauchzehe"),
        ("Stück(e)", "Stück"),
        ("Hackfleisch, gemischtes", "Hackfleisch, gemischtes"),
        ("Tomaten (gehackt)", "Tomaten (gehackt)"),
        ("  Eier  ", "Eier"),
    ],
)
def test_clean_name(raw, expected):
    assert clean_name(raw) == expected


@pytest.mark.parametrize(
    ("name", "category"),
    [
        ("Karotte(n)", "Obst & Gemüse"),
        ("Karotte", "Obst & Gemüse"),
        ("Karotten", "Obst & Gemüse"),
        ("Kartoffel(n)", "Obst & Gemüse"),
        ("Zwiebel(n)", "Obst & Gemüse"),
        ("Tomate", "Obst & Gemüse"),
        ("Apfel", "Obst & Gemüse"),  # Katalog: "Äpfel"
        ("Knoblauchzehe(n)", "Obst & Gemüse"),
        ("Ei", "Sonstiges"),  # zu kurz für einen sicheren Treffer
        ("Hackfleisch, gemischtes", "Fleisch, Fisch & Wurst"),
        ("Geburtstagskerzen", "Haushalt & Reinigung"),
        ("Tintenpatronen", "Sonstiges"),
    ],
)
def test_guess_category(web, name, category):
    with SessionLocal() as db:
        assert guess_category(db, name) == category


def open_names():
    with SessionLocal() as db:
        return [(i.category, i.name) for i in db.scalars(select(ShoppingItem).order_by(ShoppingItem.id))]


def test_recipe_ingredient_with_plural_marker_is_cleaned_and_categorised(web):
    recipe_id = make_recipe(web, "Gemüsepfanne", "3 Karotte(n)\n1 Zwiebel(n)")
    web.post("/plan/recipe", {"day": "2026-10-21", "recipe_id": recipe_id})
    with SessionLocal() as db:
        entry_id = db.scalars(select(PlanEntry.id)).first()
    web.post(f"/plan/entries/{entry_id}/shop")
    assert open_names() == [("Obst & Gemüse", "Karotte"), ("Obst & Gemüse", "Zwiebel")]


def test_plural_marker_variants_are_merged(web):
    web.post("/shopping/add", {"name": "2 Zwiebel(n)"})
    web.post("/shopping/add", {"name": "1 Zwiebel"})
    with SessionLocal() as db:
        (item,) = db.scalars(select(ShoppingItem)).all()
    assert (item.name, item.amount) == ("Zwiebel", 3.0)


def item_ids():
    with SessionLocal() as db:
        return {i.name: i.id for i in db.scalars(select(ShoppingItem))}


def listing(web) -> str:
    """Nur der Listenbereich (Undo-Button und Eingabefeld enthalten ebenfalls Namen)."""
    return web.get("/shopping").text.split('id="shopping-list"', 1)[1]


def order_of(page: str, *names: str) -> list[str]:
    return sorted(names, key=page.index)


def reorder(web, category, *names):
    ids = item_ids()
    data = {"category": category, "ids": [ids[n] for n in names]}
    return web.client.post(
        f"{web.base}/shopping/reorder", data=data, headers={"X-CSRF-Token": web.client.cookies.get("mp_csrf")}, follow_redirects=False
    )


def test_new_items_are_appended_in_order_added(web):
    for name in ("Karotten", "Äpfel", "Bananen"):
        web.post("/shopping/add", {"name": name})
    assert order_of(listing(web), "Karotten", "Äpfel", "Bananen") == ["Karotten", "Äpfel", "Bananen"]


def test_drag_and_drop_reorder_within_category_and_undo(web):
    for name in ("Karotten", "Äpfel", "Bananen"):
        web.post("/shopping/add", {"name": name})
    response = reorder(web, "Obst & Gemüse", "Bananen", "Karotten", "Äpfel")
    assert response.status_code in (200, 303)
    assert order_of(listing(web), "Karotten", "Äpfel", "Bananen") == ["Bananen", "Karotten", "Äpfel"]

    web.post("/undo")
    assert order_of(listing(web), "Karotten", "Äpfel", "Bananen") == ["Karotten", "Äpfel", "Bananen"]


def test_reorder_keeps_items_missing_from_the_request_and_ignores_unknown_ids(web):
    for name in ("Karotten", "Äpfel", "Bananen"):
        web.post("/shopping/add", {"name": name})
    ids = item_ids()
    web.client.post(
        f"{web.base}/shopping/reorder",
        data={"category": "Obst & Gemüse", "ids": [ids["Bananen"], 99999]},  # Karotten/Äpfel fehlen (z. B. neu ergänzt)
        headers={"X-CSRF-Token": web.client.cookies.get("mp_csrf")},
        follow_redirects=False,
    )
    assert order_of(listing(web), "Karotten", "Äpfel", "Bananen") == ["Bananen", "Karotten", "Äpfel"]


def test_reorder_is_idempotent(web):
    for name in ("Karotten", "Äpfel"):
        web.post("/shopping/add", {"name": name})
    reorder(web, "Obst & Gemüse", "Äpfel", "Karotten")
    reorder(web, "Obst & Gemüse", "Äpfel", "Karotten")  # z. B. erneutes Senden nach einer Offline-Phase
    assert order_of(listing(web), "Karotten", "Äpfel") == ["Äpfel", "Karotten"]
    web.post("/undo")  # nur eine Änderung wurde protokolliert, ein Undo genügt
    assert order_of(listing(web), "Karotten", "Äpfel") == ["Karotten", "Äpfel"]


def test_reorder_validates_category(web):
    response = web.client.post(
        f"{web.base}/shopping/reorder",
        data={"category": "Erfunden"},
        headers={"X-CSRF-Token": web.client.cookies.get("mp_csrf")},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_categories_can_be_reordered_and_undone(web):
    web.post("/shopping/add", {"name": "Karotten"})  # Obst & Gemüse
    web.post("/shopping/add", {"name": "Milch"})  # Milch, Käse & Eier
    web.post("/shopping/add", {"name": "Zahnpasta"})  # Hygiene & Drogerie
    assert order_of(listing(web), "Karotten", "Milch", "Zahnpasta") == ["Karotten", "Milch", "Zahnpasta"]

    web.client.post(
        f"{web.base}/shopping/categories/order",
        data={"categories": ["Hygiene & Drogerie", "Obst & Gemüse", "Milch, Käse & Eier"]},
        headers={"X-CSRF-Token": web.client.cookies.get("mp_csrf")},
        follow_redirects=False,
    )
    assert order_of(listing(web), "Karotten", "Milch", "Zahnpasta") == ["Zahnpasta", "Karotten", "Milch"]

    # Die Reihenfolge ist dauerhaft – auch für später hinzukommende Artikel
    web.post("/shopping/add", {"name": "Brot"})
    assert order_of(listing(web), "Karotten", "Zahnpasta", "Brot")[0] == "Zahnpasta"

    web.post("/undo")  # Brot
    web.post("/undo")  # Kategorien
    assert order_of(listing(web), "Karotten", "Milch", "Zahnpasta") == ["Karotten", "Milch", "Zahnpasta"]


def test_toggle_with_state_is_idempotent(web):
    web.post("/shopping/add", {"name": "Milch"})
    item_id = item_ids()["Milch"]
    for _ in range(3):  # z. B. erneutes Senden nach einer Offline-Phase
        assert web.post(f"/shopping/{item_id}/toggle", {"state": "1"}, htmx=True).status_code == 200
    with SessionLocal() as db:
        assert db.get(ShoppingItem, item_id).checked is True
    history = web.get("/history").text.split("<main>", 1)[1]
    assert history.count("abgehakt") == 1  # nur die erste Änderung wurde protokolliert

    web.post(f"/shopping/{item_id}/toggle", {"state": "0"})
    with SessionLocal() as db:
        assert db.get(ShoppingItem, item_id).checked is False


def test_toggle_without_state_still_toggles(web):
    web.post("/shopping/add", {"name": "Milch"})
    item_id = item_ids()["Milch"]
    web.post(f"/shopping/{item_id}/toggle")
    with SessionLocal() as db:
        assert db.get(ShoppingItem, item_id).checked is True


def test_toggle_rejects_invalid_state(web):
    web.post("/shopping/add", {"name": "Milch"})
    assert web.post(f"/shopping/{item_ids()['Milch']}/toggle", {"state": "2"}).status_code == 422


def test_list_fragment_endpoint(web):
    web.post("/shopping/add", {"name": "Milch"})
    fragment = web.get("/shopping/list").text
    assert fragment.lstrip().startswith('<div id="shopping-list"') and "Milch" in fragment and "<html" not in fragment


def test_list_markup_has_drag_handles_and_ids_for_the_client(web):
    web.post("/shopping/add", {"name": "Milch"})
    page = web.get("/shopping").text
    assert 'class="drag-handle item-handle"' in page and 'class="drag-handle cat-handle"' in page
    assert 'data-reorder-url="' in page and 'data-order-url="' in page and "data-id=" in page
    assert "Sortieren" not in page  # kein Sortiermodus mehr
    assert 'data-base="/s/' in page and 'id="sync-banner"' in page


def test_checked_items_are_not_draggable(web):
    web.post("/shopping/add", {"name": "Milch"})
    web.post(f"/shopping/{item_ids()['Milch']}/toggle", {"state": "1"})
    page = web.get("/shopping").text.split('class="shop-group shop-done"', 1)[1]
    assert "item-handle" not in page


def test_migration_adds_position_column_to_old_database():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE shopping_items (id INTEGER PRIMARY KEY, space_id INTEGER, name VARCHAR(100))"))
        conn.execute(text("INSERT INTO shopping_items (id, space_id, name) VALUES (1, 1, 'Milch')"))
    migrate(engine)
    migrate(engine)  # idempotent
    assert "position" in {c["name"] for c in inspect(engine).get_columns("shopping_items")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT position FROM shopping_items")).scalar() == 0


def test_recipe_ingredients_are_not_preselected(web):
    recipe_id = make_recipe(web, "Pfannkuchen", "250 g Mehl\n3 Eier")
    page = web.get(f"/recipes/{recipe_id}").text
    assert 'name="ingredient_ids"' in page
    assert "checked" not in page.split('id="ingredient-list"', 1)[1].split("</ul>", 1)[0]
    assert "data-check-all" in page


def test_recipes_page_has_one_form_with_two_search_buttons(web):
    page = web.get("/recipes").text
    assert page.count('type="search"') == 1
    assert f'formaction="{web.base}/recipes"' in page and f'formaction="{web.base}/search"' in page
    assert page.count("formaction=") == 2
