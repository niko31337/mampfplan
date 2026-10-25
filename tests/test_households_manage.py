"""Profile umbenennen/löschen und Haushalte löschen."""

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import ActivityLog, CategoryOrder, Member, PlanEntry, Recipe, RecipeIngredient, ShoppingItem, Space
from tests.conftest import TOKEN, Browser
from tests.test_households import _space_id, create_household
from tests.test_plan import make_recipe

DAY = "2026-10-21"


def member_id(name: str, space_token: str = TOKEN) -> int:
    with SessionLocal() as db:
        return db.scalars(
            select(Member.id).join(Space, Space.id == Member.space_id).where(Member.name == name, Space.token == space_token)
        ).one()


def switch(browser: Browser, name: str) -> Browser:
    browser.client.cookies.delete(f"mp_member_{_space_id(browser)}")
    return browser.login(name)


# --- Profile umbenennen ----------------------------------------------------------------------


def test_owner_renames_profile_and_stored_names_follow(anon):
    anon.login("Ben")
    anon.post("/shopping/add", {"name": "Milch"})
    recipe_id = make_recipe(anon, "Bens Curry")
    with SessionLocal() as db:
        item_id = db.scalars(select(ShoppingItem.id)).one()
    anon.post(f"/shopping/{item_id}/toggle", {"state": "1"})

    switch(anon, "Anna")
    assert anon.post(f"/members/{member_id('Ben')}/rename", {"name": "Benni"}).status_code == 303

    who = anon.get("/who").text
    assert ">Benni</button>" in who and ">Ben</button>" not in who
    with SessionLocal() as db:
        item = db.get(ShoppingItem, item_id)
        assert (item.added_by, item.checked_by) == ("Benni", "Benni")
    assert "von Benni" in anon.get(f"/recipes/{recipe_id}").text
    history = anon.get("/history").text.split("<main>", 1)[1]
    assert "Benni ·" in history and "Ben ·" not in history


def test_renaming_never_touches_another_household(web):
    other = create_household(web, owner="Ben").login("Ben")  # gleicher Name, anderer Haushalt
    other.post("/shopping/add", {"name": "Fremde Milch"})
    assert web.post(f"/members/{member_id('Ben')}/rename", {"name": "Benni"}).status_code == 303
    with SessionLocal() as db:
        foreign = db.scalars(select(ShoppingItem).where(ShoppingItem.name == "Fremde Milch")).one()
        assert foreign.added_by == "Ben"
    assert ">Ben</button>" in other.get("/who").text


def test_rename_to_own_name_in_other_case_is_allowed(web):
    assert web.post(f"/members/{member_id('Ben')}/rename", {"name": "BEN"}).status_code == 303
    assert ">BEN</button>" in web.get("/who").text


@pytest.mark.parametrize(("name", "status"), [("Anna", 400), ("anna", 400), ("   ", 400), ("x" * 51, 400), ("", 422)])
def test_rename_is_validated(web, name, status):
    assert web.post(f"/members/{member_id('Ben')}/rename", {"name": name}).status_code == status
    assert ">Ben</button>" in web.get("/who").text


# --- Profile löschen -------------------------------------------------------------------------


def test_owner_deletes_profile_but_not_self(anon):
    anon.login("Ben")
    anon.post("/shopping/add", {"name": "Milch"})
    ben_id = member_id("Ben")
    switch(anon, "Anna")
    assert anon.post(f"/members/{member_id('Anna')}/delete").status_code == 400  # sich selbst nicht
    assert anon.post(f"/members/{ben_id}/delete").status_code == 303
    assert ">Ben</button>" not in anon.get("/who").text
    with SessionLocal() as db:
        assert db.get(Member, ben_id) is None
        assert db.scalars(select(ShoppingItem.added_by)).one() == "Ben"  # Eingetragenes bleibt
    assert "eingetragen von Ben" in anon.get("/shopping").text  # Kürzel bleibt sichtbar

    # Ein Gerät, das noch als Ben angemeldet war, muss neu wählen
    anon.client.cookies.set("mp_member_1", str(ben_id))
    assert anon.get("/plan").headers["location"].endswith("/who")


def test_only_owner_may_rename_or_delete(anon):
    anon.login("Ben")
    anna = member_id("Anna")
    assert anon.post(f"/members/{anna}/rename", {"name": "Hacker"}).status_code == 403
    assert anon.post(f"/members/{anna}/delete").status_code == 403
    assert ">Anna</button>" in anon.get("/who").text
    assert "/rename" not in anon.get("/more").text


def test_profiles_of_other_households_cannot_be_changed(web):
    other = create_household(web, owner="Mia")
    mia = member_id("Mia", other.base.rsplit("/", 1)[1])
    assert web.post(f"/members/{mia}/rename", {"name": "X"}).status_code == 404
    assert web.post(f"/members/{mia}/delete").status_code == 404
    with SessionLocal() as db:
        assert db.get(Member, mia).name == "Mia"


def test_more_page_shows_edit_controls_for_owner(web):
    page = web.get("/more").text
    assert f"{web.base}/members/{member_id('Ben')}/rename" in page
    assert f"{web.base}/members/{member_id('Ben')}/delete" in page
    assert f"{web.base}/members/{member_id('Anna')}/delete" not in page  # eigenes Profil nicht löschbar


# --- Haushalte löschen -----------------------------------------------------------------------


def fill_household(browser: Browser) -> None:
    recipe_id = make_recipe(browser, "Ein Rezept", "200 g Mehl")
    browser.post("/plan/recipe", {"day": DAY, "recipe_id": recipe_id})
    browser.post("/plan/note", {"day": DAY, "text": "Notiz"})
    browser.post("/shopping/add", {"name": "Karotten"})
    browser.post("/shopping/add", {"name": "Milch"})
    browser.client.post(
        f"{browser.base}/shopping/categories/order",
        data={"categories": ["Milch, Käse & Eier", "Obst & Gemüse"]},
        headers={"X-CSRF-Token": browser.client.cookies.get("mp_csrf")},
    )
    browser.post("/members", {"name": "Max"})


def counts(space_id: int) -> dict[str, int]:
    with SessionLocal() as db:
        recipe_ids = select(Recipe.id).where(Recipe.space_id == space_id)

        def count(model, *where):
            return db.scalar(select(func.count(model.id)).where(*where))

        return {
            "recipes": count(Recipe, Recipe.space_id == space_id),
            "ingredients": count(RecipeIngredient, RecipeIngredient.recipe_id.in_(recipe_ids)),
            "plan": count(PlanEntry, PlanEntry.space_id == space_id),
            "items": count(ShoppingItem, ShoppingItem.space_id == space_id),
            "order": count(CategoryOrder, CategoryOrder.space_id == space_id),
            "log": count(ActivityLog, ActivityLog.space_id == space_id),
            "members": count(Member, Member.space_id == space_id),
            "space": count(Space, Space.id == space_id),
        }


def test_admin_deletes_household_with_all_its_data(web):
    fill_household(web)  # der eigene Haushalt hat ebenfalls Daten
    other = create_household(web, "Haushalt Schmidt", "Mia").login("Mia")  # Name kommt nicht im Formular-Platzhalter vor
    fill_household(other)
    other_id = _space_id(other)
    own_before = counts(_space_id(web))
    assert all(counts(other_id).values())

    assert web.post(f"/households/{other_id}/delete", {"confirm_name": "Haushalt Schmidt"}).status_code == 303
    assert set(counts(other_id).values()) == {0}
    assert other.get("/plan").status_code == 404  # Link ungültig
    assert counts(_space_id(web)) == own_before  # eigener Haushalt unberührt
    assert "Haushalt Schmidt" not in web.get("/households").text


@pytest.mark.parametrize("wrong", ["familie müller", "Familie", "Familie Müllerx", "   "])
def test_household_delete_requires_exact_name(web, wrong):
    other_id = _space_id(create_household(web, "Familie Müller", "Mia"))
    assert web.post(f"/households/{other_id}/delete", {"confirm_name": wrong}).status_code == 400
    assert counts(other_id)["space"] == 1


def test_household_delete_ignores_surrounding_spaces(web):
    other_id = _space_id(create_household(web, "Familie Müller", "Mia"))
    assert web.post(f"/households/{other_id}/delete", {"confirm_name": "  Familie Müller "}).status_code == 303
    assert counts(other_id)["space"] == 0


def test_household_delete_without_confirmation(web):
    other_id = _space_id(create_household(web, "Familie Müller", "Mia"))
    assert web.post(f"/households/{other_id}/delete").status_code == 400
    assert counts(other_id)["space"] == 1


def test_admin_household_cannot_be_deleted(web):
    assert web.post(f"/households/{_space_id(web)}/delete", {"confirm_name": "Familie"}).status_code == 400
    assert web.get("/plan").status_code == 200


def test_other_households_cannot_delete_households(web):
    first_id = _space_id(create_household(web, "Familie A", "Mia"))
    second = create_household(web, "Familie B", "Tom").login("Tom")
    assert second.post(f"/households/{first_id}/delete", {"confirm_name": "Familie A"}).status_code == 404
    assert counts(first_id)["space"] == 1


def test_delete_unknown_household(web):
    assert web.post("/households/999/delete", {"confirm_name": "x"}).status_code == 404


def test_households_page_shows_delete_only_for_other_households(web):
    other_id = _space_id(create_household(web, "Familie Müller", "Mia"))
    page = web.get("/households").text
    assert f"/households/{other_id}/delete" in page
    assert f"/households/{_space_id(web)}/delete" not in page
