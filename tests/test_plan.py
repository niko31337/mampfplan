import re
from datetime import date

from sqlalchemy import select

from app.db import SessionLocal
from app.models import PlanEntry

DAY = "2026-10-21"  # Wednesday


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
    # nothing happens at the edge
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
    anon.client.cookies.delete("mp_member_1")
    anon.login("Ben")
    anon.post("/plan/note", {"day": DAY, "text": "von Ben"})
    page = anon.get("/history").text
    assert re.search(r"von Ben.*?Ben", page, re.S) and "von Anna" in page
    assert "von Anna" in anon.get(f"/plan?week={DAY}").text


# --- Today first, moving entries between days -------------------------------------------------


def _fix_today(monkeypatch, day: date):
    from app import web as web_module
    from app.routers import plan as plan_router

    monkeypatch.setattr(plan_router, "today", lambda: day)
    monkeypatch.setattr(web_module, "today", lambda: day)


def _day_order(page: str) -> list[str]:
    return re.findall(r'<section class="day[^"]*" data-day="([\d-]+)"', page)


def test_today_is_at_the_top_and_past_days_at_the_end(web, monkeypatch):
    _fix_today(monkeypatch, date(2026, 10, 21))  # Wednesday
    page = web.get("/plan").text
    assert _day_order(page) == [
        "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-19", "2026-10-20",
    ]  # fmt: skip
    assert page.index("Bereits vorbei") > page.index('data-day="2026-10-25"')
    assert page.index("Bereits vorbei") < page.index('data-day="2026-10-19"')


def test_other_weeks_keep_monday_first(web, monkeypatch):
    _fix_today(monkeypatch, date(2026, 10, 21))
    page = web.get("/plan?week=2026-10-26").text
    assert _day_order(page)[0] == "2026-10-26"
    assert "Bereits vorbei" not in page
    current = web.get("/plan?week=2026-10-19").text  # explicitly the current week: today first again
    assert _day_order(current)[0] == "2026-10-21"


def test_move_entry_to_a_day_that_already_has_a_recipe(web):
    pasta, salad = make_recipe(web, "Pasta"), make_recipe(web, "Salat")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": pasta})
    web.post("/plan/recipe", {"day": "2026-10-22", "recipe_id": salad})
    [entry] = entry_ids(DAY)

    response = web.post(f"/plan/entries/{entry}/day", {"day": "2026-10-22"}, htmx=True)
    assert response.status_code == 200
    assert entries(DAY) == []
    assert entries("2026-10-22") == [("recipe", salad, None), ("recipe", pasta, None)]  # added, nothing replaced

    # move the salad on to another day
    salad_entry = entry_ids("2026-10-22")[0]
    web.post(f"/plan/entries/{salad_entry}/day", {"day": "2026-10-23"})
    assert entries("2026-10-22") == [("recipe", pasta, None)]
    assert entries("2026-10-23") == [("recipe", salad, None)]

    web.post("/undo")
    assert entries("2026-10-22") == [("recipe", salad, None), ("recipe", pasta, None)]


def test_drop_entry_at_a_position(web):
    a, b, c = make_recipe(web, "A"), make_recipe(web, "B"), make_recipe(web, "C")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": a})
    web.post("/plan/recipe", {"day": "2026-10-22", "recipe_id": b})
    web.post("/plan/recipe", {"day": "2026-10-22", "recipe_id": c})
    moving = entry_ids(DAY)[0]
    first, second = entry_ids("2026-10-22")
    web.post(f"/plan/entries/{moving}/day", {"day": "2026-10-22", "ids": [first, moving, second]}, htmx=True)
    assert entries("2026-10-22") == [("recipe", b, None), ("recipe", a, None), ("recipe", c, None)]


def test_move_entry_of_other_household_is_refused(web):
    assert web.post("/plan/entries/99999/day", {"day": DAY}).status_code == 404


def test_week_offers_move_menu(web):
    pasta = make_recipe(web, "Pasta")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": pasta})
    page = web.get(f"/plan?week={DAY}").text
    [entry] = entry_ids(DAY)
    assert page.count(f"/plan/entries/{entry}/day") == 7  # 6 other weekdays + free date
    assert "entry-handle" in page
