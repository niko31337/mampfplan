"""Artikel in eine andere Kategorie verschieben (und die Korrektur je Haushalt merken)."""

import html
import re

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import CategoryOverride, ShoppingItem
from app.services.shopping import guess_category
from tests.conftest import Browser
from tests.test_households import _space_id, create_household

DAIRY = "Milch, Käse & Eier"


def item(name: str) -> ShoppingItem:
    with SessionLocal() as db:
        return db.scalars(select(ShoppingItem).where(ShoppingItem.name == name)).one()


def move(browser: Browser, name: str, category: str, *order: str):
    """Wie der Browser nach dem Ziehen: Ziel-Kategorie und Reihenfolge der Artikel dort."""
    ids = [item(n).id for n in order] if order else [item(name).id]
    return browser.client.post(
        f"{browser.base}/shopping/{item(name).id}/move",
        data={"category": category, "ids": ids},
        headers={"X-CSRF-Token": browser.client.cookies.get("mp_csrf"), "HX-Request": "true"},
    )


def listing(browser: Browser) -> str:
    return browser.get("/shopping").text.split('id="shopping-list"', 1)[1]


def section_names(page: str, category: str) -> list[str]:
    block = page.split(f'data-category="{html.escape(category)}">', 1)[1].split("</section>", 1)[0]
    return item_names(block)


def item_names(fragment: str) -> list[str]:
    return re.findall(r'<div class="shop-name">\s*([^<\n]+?)\s*(?:<small>|</div>)', fragment)


def test_item_moves_to_another_category_at_the_dropped_position(web):
    web.post("/shopping/add", {"name": "Tintenpatronen"})  # landet unter Sonstiges
    web.post("/shopping/add", {"name": "Milch"})
    web.post("/shopping/add", {"name": "Butter"})
    assert item("Tintenpatronen").category == "Sonstiges"

    response = move(web, "Tintenpatronen", DAIRY, "Milch", "Tintenpatronen", "Butter")
    assert response.status_code == 200
    assert item("Tintenpatronen").category == DAIRY
    assert section_names(listing(web), DAIRY) == ["Milch", "Tintenpatronen", "Butter"]


def test_move_into_empty_category(web):
    web.post("/shopping/add", {"name": "Tintenpatronen"})
    assert move(web, "Tintenpatronen", "Getränke").status_code == 200
    page = listing(web)
    assert section_names(page, "Getränke") == ["Tintenpatronen"]
    assert 'class="shop-group is-empty" data-category="Sonstiges"' in page  # leer, bleibt Ablagefläche


def test_correction_is_remembered_for_next_time(web):
    web.post("/shopping/add", {"name": "Tintenpatronen"})
    move(web, "Tintenpatronen", "Haushalt & Reinigung")
    web.post(f"/shopping/{item('Tintenpatronen').id}/delete")
    web.post("/shopping/add", {"name": "2 Tintenpatronen"})  # Menge, andere Schreibweise egal
    assert item("Tintenpatronen").category == "Haushalt & Reinigung"


def test_correction_beats_catalog_suggestion(web):
    web.post("/shopping/add", {"name": "Milch"})
    move(web, "Milch", "Getränke")
    web.post(f"/shopping/{item('Milch').id}/delete")
    web.post("/shopping/add", {"name": "Milch", "category": DAIRY})  # Tipp auf den Katalog-Chip
    assert item("Milch").category == "Getränke"


def test_correction_is_per_household(web):
    web.post("/shopping/add", {"name": "Tintenpatronen"})
    move(web, "Tintenpatronen", "Getränke")
    other = create_household(web).login("Mia")
    other.post("/shopping/add", {"name": "Tintenpatronen"})
    with SessionLocal() as db:
        foreign = db.scalars(
            select(ShoppingItem).where(ShoppingItem.name == "Tintenpatronen", ShoppingItem.space_id == _space_id(other))
        ).one()
    assert foreign.category == "Sonstiges"


def test_undo_restores_category_and_forgets_correction(web):
    web.post("/shopping/add", {"name": "Tintenpatronen"})
    move(web, "Tintenpatronen", "Getränke")
    web.post("/undo")
    assert item("Tintenpatronen").category == "Sonstiges"
    with SessionLocal() as db:
        assert db.scalars(select(CategoryOverride)).all() == []


def test_moving_back_updates_the_correction(web):
    web.post("/shopping/add", {"name": "Tintenpatronen"})
    move(web, "Tintenpatronen", "Getränke")
    move(web, "Tintenpatronen", "Haushalt & Reinigung")
    with SessionLocal() as db:
        (override,) = db.scalars(select(CategoryOverride)).all()
    assert override.category == "Haushalt & Reinigung"


def test_reorder_within_same_category_via_move_does_not_learn(web):
    web.post("/shopping/add", {"name": "Milch"})
    web.post("/shopping/add", {"name": "Butter"})
    move(web, "Butter", DAIRY, "Butter", "Milch")
    assert section_names(listing(web), DAIRY) == ["Butter", "Milch"]
    with SessionLocal() as db:
        assert db.scalars(select(CategoryOverride)).all() == []


@pytest.mark.parametrize("category", ["Erfunden", "", "Gekauft"])
def test_move_validates_category(web, category):
    web.post("/shopping/add", {"name": "Milch"})
    assert move(web, "Milch", category).status_code in (400, 422)
    assert item("Milch").category == DAIRY


def test_checked_items_cannot_be_moved(web):
    web.post("/shopping/add", {"name": "Milch"})
    web.post(f"/shopping/{item('Milch').id}/toggle", {"state": "1"})
    assert move(web, "Milch", "Getränke").status_code == 400


def test_items_of_other_households_cannot_be_moved(web):
    web.post("/shopping/add", {"name": "Milch"})
    other = create_household(web).login("Mia")
    response = other.client.post(
        f"{other.base}/shopping/{item('Milch').id}/move",
        data={"category": "Getränke"},
        headers={"X-CSRF-Token": other.client.cookies.get("mp_csrf")},
    )
    assert response.status_code == 404
    assert item("Milch").category == DAIRY


def test_history_names_the_move(web):
    web.post("/shopping/add", {"name": "Tintenpatronen"})
    move(web, "Tintenpatronen", "Getränke")
    assert "„Tintenpatronen“ nach „Getränke“ verschoben" in web.get("/history").text


def test_list_renders_all_categories_with_drop_targets(web):
    web.post("/shopping/add", {"name": "Milch"})
    page = listing(web)
    assert page.count('class="shop-items"') == 13  # alle Kategorien, leere als Ablagefläche
    assert page.count("is-empty") == 12
    assert f'data-move-url="{web.base}/shopping/__ID__/move"' in page


def test_deleting_a_household_removes_its_corrections(web):
    other = create_household(web, "Haushalt Schmidt", "Mia").login("Mia")
    other.post("/shopping/add", {"name": "Tintenpatronen"})
    move(other, "Tintenpatronen", "Getränke")
    assert web.post(f"/households/{_space_id(other)}/delete", {"confirm_name": "Haushalt Schmidt"}).status_code == 303
    with SessionLocal() as db:
        assert db.scalars(select(CategoryOverride)).all() == []


def test_cheese_is_recognised(web):
    with SessionLocal() as db:
        assert guess_category(db, "Maasdamer Käse") == DAIRY
        assert guess_category(db, "Käse am Stück") == DAIRY
        assert guess_category(db, "Briefumschläge") == "Sonstiges"


def test_offline_queue_covers_moving_between_categories(anon):
    js = anon.client.get("/static/app.js").text
    assert r"\d+\/move" in js and 'group: "shop-items"' in js
