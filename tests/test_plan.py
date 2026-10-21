import re
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import PlanEntry

DAY = "2026-10-21"  # Mittwoch


def make_recipe(web, title="Spaghetti", ingredients="500 g Spaghetti\n2 Eier\nSalz", steps="Kochen."):
    response = web.post("/recipes/new", {"title": title, "ingredients": ingredients, "steps": steps})
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[1])


def _day_entries(day: str) -> list[PlanEntry]:
    with SessionLocal() as db:
        return list(
            db.scalars(select(PlanEntry).where(PlanEntry.day == date.fromisoformat(day)).order_by(PlanEntry.position, PlanEntry.id))
        )


def entries(day=DAY):
    return [(e.kind, e.recipe_id, e.note) for e in _day_entries(day)]


def entry_ids(day=DAY):
    return [e.id for e in _day_entries(day)]


def test_multiple_entries_per_day_and_note(web):
    r1 = make_recipe(web, "Pasta")
    r2 = make_recipe(web, "Salat")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": r1})
    web.post("/plan/recipe", {"day": DAY, "recipe_id": r2})
    web.post("/plan/note", {"day": DAY, "text": "Danach Eis essen gehen"})
    assert entries() == [("recipe", r1, None), ("recipe", r2, None), ("note", None, "Danach Eis essen gehen")]


def test_reorder_entries(web):
    r1 = make_recipe(web, "Pasta")
    r2 = make_recipe(web, "Salat")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": r1})
    web.post("/plan/recipe", {"day": DAY, "recipe_id": r2})
    first, second = entry_ids()
    web.post(f"/plan/entries/{second}/move", {"direction": "up"})
    assert [e[1] for e in entries()] == [r2, r1]
    # am Rand passiert nichts
    web.post(f"/plan/entries/{second}/move", {"direction": "up"})
    assert [e[1] for e in entries()] == [r2, r1]


def test_undo_reorder_and_delete(web):
    r1 = make_recipe(web, "Pasta")
    r2 = make_recipe(web, "Salat")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": r1})
    web.post("/plan/recipe", {"day": DAY, "recipe_id": r2})
    first, second = entry_ids()

    web.post(f"/plan/entries/{second}/move", {"direction": "up"})
    web.post("/undo")
    assert [e[1] for e in entries()] == [r1, r2]

    web.post(f"/plan/entries/{first}/delete")
    assert [e[1] for e in entries()] == [r2]
    web.post("/undo")
    assert [e[1] for e in entries()] == [r1, r2]


def test_partial_response_contains_week_and_undo(web):
    r1 = make_recipe(web, "Pasta")
    response = web.post("/plan/recipe", {"day": DAY, "recipe_id": r1}, htmx=True)
    assert response.status_code == 200
    assert 'id="week"' in response.text
    assert 'id="undo-slot" hx-swap-oob="true"' in response.text
    assert "Pasta" in response.text


def test_plan_page_renders_week(web):
    r1 = make_recipe(web, "Pasta")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": r1})
    page = web.get(f"/plan?week={DAY}").text
    assert "Mittwoch, 21. Oktober" in page
    assert "KW 43" in page
    assert "Pasta" in page


def test_entry_of_other_space_is_not_reachable(web):
    assert web.post("/plan/entries/9999/delete").status_code == 404


def test_shop_entry_adds_all_ingredients(web):
    r1 = make_recipe(web, "Pasta")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": r1})
    (entry_id,) = entry_ids()
    web.post(f"/plan/entries/{entry_id}/shop")
    page = web.get("/shopping").text
    assert "Spaghetti" in page and "Eier" in page and "Salz" in page


def test_deleted_recipe_keeps_plan_entry_and_can_be_restored(web):
    r1 = make_recipe(web, "Pasta")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": r1})
    web.post(f"/recipes/{r1}/delete")
    assert web.get(f"/recipes/{r1}").status_code == 404
    assert "Pasta" in web.get(f"/plan?week={DAY}").text
    web.post("/undo")
    assert web.get(f"/recipes/{r1}").status_code == 200


def test_history_and_attribution(web):
    web.post("/plan/note", {"day": DAY, "text": "Pizza-Abend"})
    page = web.get("/history").text
    assert "Pizza-Abend" in page and "Anna" in page


def test_second_member_can_change_and_see_history(anon):
    anon.login("Anna")
    anon.post("/plan/note", {"day": DAY, "text": "von Anna"})
    anon.client.cookies.delete("mp_member")
    anon.login("Ben")
    anon.post("/plan/note", {"day": DAY, "text": "von Ben"})
    page = anon.get("/history").text
    assert re.search(r"von Ben.*?Ben", page, re.S) and "von Anna" in page
    assert "von Anna" in anon.get(f"/plan?week={DAY}").text
