from datetime import date

import pytest

from app.routers import api
from tests.conftest import BASE
from tests.test_households import create_household
from tests.test_plan import entry_ids, make_recipe

TODAY = date(2026, 10, 21)  # Mittwoch


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(api, "today", lambda: TODAY)


def get_plan(browser, **params):
    response = browser.get("/api/plan", params=params)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    return response.json()


def test_plan_lists_entries_in_order_within_the_window(web):
    pasta = make_recipe(web, "Pasta")
    salat = make_recipe(web, "Salat")
    web.post("/plan/recipe", {"day": "2026-10-20", "recipe_id": pasta})  # gestern: nicht dabei
    web.post("/plan/recipe", {"day": "2026-10-21", "recipe_id": pasta})
    web.post("/plan/note", {"day": "2026-10-21", "text": "Reste essen"})
    web.post("/plan/recipe", {"day": "2026-10-21", "recipe_id": salat})
    web.post("/plan/note", {"day": "2026-10-23", "text": "Auswärts"})
    web.post("/plan/recipe", {"day": "2026-10-28", "recipe_id": salat})  # 8. Tag: nicht dabei

    # Reihenfolge im Plan ändern: Salat nach oben
    salat_entry = entry_ids("2026-10-21")[2]
    web.post(f"/plan/entries/{salat_entry}/move", {"direction": "up"})
    web.post(f"/plan/entries/{salat_entry}/move", {"direction": "up"})
    web.post(f"/plan/entries/{salat_entry}/cooked")

    data = get_plan(web)
    assert data["today"] == "2026-10-21"
    assert [d["date"] for d in data["days"]] == [f"2026-10-{n}" for n in range(21, 28)]
    assert data["days"][0]["weekday"] == "Mittwoch"
    assert data["days"][6]["weekday"] == "Dienstag"
    assert data["days"][0]["entries"] == [
        {"kind": "recipe", "title": "Salat", "cooked": True},
        {"kind": "recipe", "title": "Pasta", "cooked": False},
        {"kind": "note", "title": "Reste essen", "cooked": False},
    ]
    assert data["days"][1]["entries"] == []
    assert data["days"][2]["entries"] == [{"kind": "note", "title": "Auswärts", "cooked": False}]
    assert all(d["entries"] == [] for d in data["days"][3:])


def test_days_parameter_sets_the_window(web):
    web.post("/plan/note", {"day": "2026-11-03", "text": "Geburtstag"})  # 14. Tag
    assert len(get_plan(web, days=1)["days"]) == 1
    data = get_plan(web, days=14)
    assert len(data["days"]) == 14
    assert data["days"][-1] == {
        "date": "2026-11-03",
        "weekday": "Dienstag",
        "entries": [{"kind": "note", "title": "Geburtstag", "cooked": False}],
    }


def test_no_profile_needed(anon):
    response = anon.get("/api/plan")
    assert response.status_code == 200
    assert len(response.json()["days"]) == 7


def test_wrong_token_is_not_found(anon):
    response = anon.client.get("/s/falsches-token-0123456789abcdef/api/plan")
    assert response.status_code == 404


@pytest.mark.parametrize("days", [0, 15, -1])
def test_days_out_of_range_is_rejected(anon, days):
    assert anon.get("/api/plan", params={"days": days}).status_code == 422


def test_only_the_own_household_is_visible(web):
    web.post("/plan/note", {"day": "2026-10-21", "text": "Unser Essen"})
    other = create_household(web).login("Mia")
    other.post("/plan/note", {"day": "2026-10-21", "text": "Fremdes Essen"})

    assert get_plan(web)["days"][0]["entries"] == [{"kind": "note", "title": "Unser Essen", "cooked": False}]
    assert get_plan(other)["days"][0]["entries"] == [{"kind": "note", "title": "Fremdes Essen", "cooked": False}]
    assert other.base != BASE
