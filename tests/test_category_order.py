"""Kategorie-Reihenfolge: dauerhaft wie im Supermarkt."""

import html
import re

from app.db import SessionLocal
from app.services.shopping import CATEGORY_ORDER, category_order
from tests.conftest import Browser
from tests.test_households import _space_id, create_household


def post_order(browser: Browser, **data):
    return browser.client.post(
        f"{browser.base}/shopping/categories/order",
        data=data,
        headers={"X-CSRF-Token": browser.client.cookies.get("mp_csrf"), "HX-Request": "true"},
    )


def full_order(browser: Browser) -> list[str]:
    with SessionLocal() as db:
        return category_order(db, _space_id(browser))


def visible_order(browser: Browser) -> list[str]:
    page = browser.get("/shopping").text
    return [html.unescape(c) for c in re.findall(r'<section class="shop-group" data-category="([^"]+)">', page)]


def test_category_moved_to_front_stays_in_front(web):
    """Getränke nach vorn – Obst bleibt direkt dahinter, auch wenn später andere Kategorien dazukommen."""
    web.post("/shopping/add", {"name": "Karotten"})  # Obst & Gemüse
    web.post("/shopping/add", {"name": "Cola"})  # Getränke
    assert visible_order(web) == ["Obst & Gemüse", "Getränke"]

    assert post_order(web, category="Getränke", before="Obst & Gemüse").status_code == 200
    assert visible_order(web) == ["Getränke", "Obst & Gemüse"]

    web.post("/shopping/add", {"name": "Brot"})  # Brot & Backwaren kommt dazu
    web.post("/shopping/add", {"name": "Chips"})  # Süßes & Snacks
    assert visible_order(web) == ["Getränke", "Obst & Gemüse", "Brot & Backwaren", "Süßes & Snacks"]
    assert full_order(web)[:3] == ["Getränke", "Obst & Gemüse", "Brot & Backwaren"]


def test_insert_after_last_visible_category(web):
    web.post("/shopping/add", {"name": "Karotten"})
    web.post("/shopping/add", {"name": "Cola"})
    post_order(web, category="Obst & Gemüse", after="Getränke")
    order = full_order(web)
    assert order.index("Obst & Gemüse") == order.index("Getränke") + 1
    assert order[0] == "Brot & Backwaren"  # übrige Reihenfolge unverändert


def test_full_store_order_from_the_store_order_page(web):
    wanted = ["Getränke", "Obst & Gemüse", "Süßes & Snacks"] + [
        c for c in CATEGORY_ORDER if c not in ("Getränke", "Obst & Gemüse", "Süßes & Snacks")
    ]
    assert post_order(web, categories=wanted).status_code == 200
    assert full_order(web) == wanted


def test_store_order_page_lists_all_categories_with_counts(web):
    web.post("/shopping/add", {"name": "Karotten"})
    web.post("/shopping/add", {"name": "Äpfel"})
    page = web.get("/shopping/categories")
    assert page.status_code == 200
    names = [html.unescape(c) for c in re.findall(r'<li class="list-item" data-category="([^"]+)">', page.text)]
    assert names == CATEGORY_ORDER  # auch leere Kategorien
    assert re.search(r"Obst &amp; Gemüse</span>\s*<span class=\"badge\"[^>]*>2</span>", page.text)
    assert 'id="category-order"' in page.text
    assert f"{web.base}/shopping/categories" in web.get("/shopping").text  # Link aus der Einkaufsliste


def test_moving_is_undoable(web):
    web.post("/shopping/add", {"name": "Karotten"})
    web.post("/shopping/add", {"name": "Cola"})
    before = full_order(web)
    post_order(web, category="Getränke", before="Obst & Gemüse")
    web.post("/undo")
    assert full_order(web) == before


def test_same_position_is_no_change(web):
    order = full_order(web)
    successor = order[order.index("Getränke") + 1]
    post_order(web, category="Getränke", before=successor)  # steht schon genau dort
    assert full_order(web) == order
    assert "Kategorien umsortiert" not in web.get("/history").text.split("<main>", 1)[1]


def test_unknown_category_is_rejected(web):
    assert post_order(web, category="Erfunden", before="Getränke").status_code == 400


def test_unknown_neighbour_changes_nothing(web):
    before = full_order(web)
    assert post_order(web, category="Getränke", before="Erfunden").status_code == 200
    assert full_order(web) == before


def test_order_is_per_household(web):
    post_order(web, category="Getränke", before="Obst & Gemüse")
    other = create_household(web).login("Mia")
    assert full_order(other) == CATEGORY_ORDER


def test_drag_script_keeps_list_while_dragging_and_does_not_replace_it_after_drop(anon):
    js = anon.client.get("/static/app.js").text
    post_order = js.split("function postOrder", 1)[1].split("\n}\n", 1)[0]
    assert "enqueue(" in post_order and "flushQueue()" in post_order  # der Reihe nach, nichts geht verloren
    assert "htmx.ajax" not in post_order
    assert "event.detail.shouldSwap = false" in js  # kein Austausch während des Ziehens
    assert "scrollSensitivity: 120" in js  # großer Randbereich fürs automatische Scrollen
    assert "values.before = next.dataset.category" in js
