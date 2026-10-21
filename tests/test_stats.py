from datetime import timedelta

from app.config import today
from tests.test_plan import entry_ids, make_recipe


def test_stats_count_past_and_marked_entries_only(web):
    pasta = make_recipe(web, "Pasta")
    salat = make_recipe(web, "Salat")
    past = (today() - timedelta(days=3)).isoformat()
    older = (today() - timedelta(days=10)).isoformat()
    future = (today() + timedelta(days=30)).isoformat()

    web.post("/plan/recipe", {"day": past, "recipe_id": pasta})
    web.post("/plan/recipe", {"day": older, "recipe_id": pasta})
    web.post("/plan/recipe", {"day": past, "recipe_id": salat})
    web.post("/plan/recipe", {"day": future, "recipe_id": salat})  # noch nicht gekocht

    page = web.get("/stats").text.split("Am häufigsten gekocht", 1)[1]
    assert page.index("Pasta") < page.index("Salat")  # Pasta häufiger
    assert "2×" in page and "1×" in page

    # Zukünftiges manuell als gekocht markieren
    (future_entry,) = entry_ids(future)
    web.post(f"/plan/entries/{future_entry}/cooked")
    assert "2×" in web.get("/stats").text  # Salat jetzt ebenfalls 2×


def test_notes_do_not_count(web):
    web.post("/plan/note", {"day": (today() - timedelta(days=1)).isoformat(), "text": "Restaurant"})
    page = web.get("/stats").text
    assert "Noch nichts gekocht" in page
