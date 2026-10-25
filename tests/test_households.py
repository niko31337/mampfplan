import re
from datetime import date

import pytest
from sqlalchemy import create_engine, inspect, select, text

from app.config import Settings
from app.db import SessionLocal, migrate
from app.models import Member, PlanEntry, ShoppingItem, Space
from app.services import households
from app.services.seed import ensure_space
from tests.conftest import TOKEN, Browser
from tests.test_plan import make_recipe

DAY = "2026-10-21"


def create_household(web: Browser, name="Familie Müller", owner="Mia") -> Browser:
    """Legt über die Verwaltung einen Haushalt an und gibt einen Browser in diesem Haushalt zurück."""
    response = web.post("/households", {"name": name, "owner_name": owner})
    assert response.status_code == 303
    created = int(re.search(r"created=(\d+)", response.headers["location"])[1])
    with SessionLocal() as db:
        token = db.get(Space, created).token
    return web.household(token)


# --- Verwaltungs-Haushalt --------------------------------------------------------------------


def test_first_household_is_the_admin_household(web):
    with SessionLocal() as db:
        (space,) = db.scalars(select(Space)).all()
        assert space.is_admin and space.token == TOKEN
    assert "Haushalte verwalten" in web.get("/more").text
    assert web.get("/households").status_code == 200


def test_admin_creates_household_with_own_link_and_first_profile(web):
    other = create_household(web, "Familie Müller", "Mia")
    page = web.get("/households").text
    assert "Familie Müller" in page and "Mia" in page and other.base in page
    assert other.base != web.base and len(other.base.rsplit("/", 1)[1]) >= 40  # eigener, langer Zufalls-Link
    who = other.get("/who").text
    assert ">Mia</button>" in who and ">Anna</button>" not in who


def test_new_household_is_not_an_admin(web):
    other = create_household(web).login("Mia")
    assert other.get("/households").status_code == 404
    assert other.post("/households", {"name": "X", "owner_name": "Y"}).status_code == 404
    assert "Haushalte verwalten" not in other.get("/more").text


@pytest.mark.parametrize(
    ("name", "owner", "status"),
    # leer = Pflichtfeld fehlt (422), nur Leerzeichen/zu lang = verständliche Meldung (400), viel zu lang = 422
    [("", "Mia", 422), ("Familie", "", 422), ("   ", "Mia", 400), ("x" * 101, "Mia", 400), ("x" * 300, "Mia", 422)],
)
def test_household_creation_validates_input(web, name, owner, status):
    assert web.post("/households", {"name": name, "owner_name": owner}).status_code == status
    with SessionLocal() as db:
        assert db.scalar(select(Space.id).where(Space.is_admin.is_(False))) is None


def test_admin_can_renew_another_households_link(web):
    other = create_household(web).login("Mia")
    with SessionLocal() as db:
        target = db.scalars(select(Space).where(Space.is_admin.is_(False))).one()
    assert web.post(f"/households/{target.id}/rotate").status_code == 303
    assert other.get("/plan").status_code == 404  # alter Link ungültig
    with SessionLocal() as db:
        new_token = db.get(Space, target.id).token
    assert web.household(new_token).get("/who").status_code == 200


# --- Datentrennung zwischen Haushalten -------------------------------------------------------


def test_households_do_not_see_each_others_data(web):
    recipe_id = make_recipe(web, "Omas Geheimrezept")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": recipe_id})
    web.post("/plan/note", {"day": DAY, "text": "Geheime Notiz"})
    web.post("/shopping/add", {"name": "Geheimzutat"})

    other = create_household(web).login("Mia")
    assert "Omas Geheimrezept" not in other.get("/recipes").text
    assert "Geheime Notiz" not in other.get(f"/plan?week={DAY}").text
    assert "Geheimzutat" not in other.get("/shopping").text
    assert "Geheim" not in other.get("/history").text
    assert "Omas" not in other.get("/stats").text


def test_foreign_ids_are_not_reachable_from_another_household(web):
    recipe_id = make_recipe(web, "Pasta")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": recipe_id})
    web.post("/shopping/add", {"name": "Milch"})
    with SessionLocal() as db:
        entry_id = db.scalars(select(PlanEntry.id)).one()
        item_id = db.scalars(select(ShoppingItem.id)).one()

    other = create_household(web).login("Mia")
    assert other.get(f"/recipes/{recipe_id}").status_code == 404
    assert other.get(f"/recipes/{recipe_id}/edit").status_code == 404
    assert other.post(f"/recipes/{recipe_id}/delete").status_code == 404
    assert other.post(f"/recipes/{recipe_id}/shop", {"ingredient_ids": 1}).status_code == 404
    assert other.post("/plan/recipe", {"day": DAY, "recipe_id": recipe_id}).status_code == 404
    assert other.post(f"/plan/entries/{entry_id}/delete").status_code == 404
    assert other.post(f"/plan/entries/{entry_id}/shop").status_code == 404
    assert other.post(f"/shopping/{item_id}/toggle", {"state": "1"}).status_code == 404
    assert other.post(f"/shopping/{item_id}/delete").status_code == 404
    with SessionLocal() as db:  # nichts verändert
        assert db.get(ShoppingItem, item_id).checked is False
        assert db.get(PlanEntry, entry_id) is not None


def test_undo_only_affects_the_own_household(web):
    web.post("/shopping/add", {"name": "Milch"})
    other = create_household(web).login("Mia")
    other.post("/undo")  # hat selbst nichts zum Rückgängigmachen
    assert "Milch" in web.get("/shopping").text


def test_reorder_in_one_household_ignores_foreign_ids(web):
    web.post("/shopping/add", {"name": "Karotten"})
    with SessionLocal() as db:
        foreign_id = db.scalars(select(ShoppingItem.id)).one()
        before = db.get(ShoppingItem, foreign_id).position
    other = create_household(web).login("Mia")
    other.post("/shopping/add", {"name": "Äpfel"})
    other.client.post(
        f"{other.base}/shopping/reorder",
        data={"category": "Obst & Gemüse", "ids": [foreign_id]},
        headers={"X-CSRF-Token": other.client.cookies.get("mp_csrf")},
    )
    with SessionLocal() as db:
        assert db.get(ShoppingItem, foreign_id).position == before


def test_profile_of_one_household_is_not_valid_in_another(web):
    other = create_household(web)
    with SessionLocal() as db:
        anna = db.scalars(select(Member).where(Member.name == "Anna")).one()
        other_space = db.scalars(select(Space).where(Space.is_admin.is_(False))).one()
    # Gefälschtes Cookie: Annas Profil-ID im fremden Haushalt
    other.client.cookies.set(f"mp_member_{other_space.id}", str(anna.id))
    assert other.get("/plan").headers["location"].endswith("/who")


def test_one_device_can_use_two_households_at_once(web):
    other = create_household(web).login("Mia")
    # Ohne erneute Profilwahl abwechselnd in beiden Haushalten
    assert web.get("/plan").status_code == 200
    assert other.get("/plan").status_code == 200
    assert "Anna" in web.get("/more").text and "Mia" in other.get("/more").text


# --- Profile ---------------------------------------------------------------------------------


def test_first_profile_can_add_profiles(web):
    assert web.post("/members", {"name": "Clara"}).status_code == 303
    assert ">Clara</button>" in web.get("/who").text
    assert "Clara" in web.get("/more").text


def test_other_profiles_cannot_add_profiles(anon):
    anon.login("Ben")  # zweites Profil
    assert anon.post("/members", {"name": "Clara"}).status_code == 403
    page = anon.get("/more").text
    assert 'action="' + anon.base + '/members"' not in page and "Neue Profile kann Anna anlegen" in page


def test_owner_of_a_new_household_manages_its_profiles(web):
    other = create_household(web, owner="Mia").login("Mia")
    assert other.post("/members", {"name": "Max"}).status_code == 303
    other.client.cookies.delete(f"mp_member_{_space_id(other)}")
    other.login("Max")
    assert other.post("/members", {"name": "Lea"}).status_code == 403


@pytest.mark.parametrize(("name", "status"), [("Anna", 400), ("anna", 400), ("", 422), ("   ", 400), ("x" * 51, 400)])
def test_profile_names_are_validated(web, name, status):
    assert web.post("/members", {"name": name}).status_code == status


def test_profile_limit(web, monkeypatch):
    monkeypatch.setattr(households, "MAX_MEMBERS_PER_HOUSEHOLD", 3)
    assert web.post("/members", {"name": "Clara"}).status_code == 303
    assert web.post("/members", {"name": "Dora"}).status_code == 400


def test_owner_can_renew_own_link_others_cannot(anon):
    anon.login("Ben")
    assert anon.post("/link/rotate").status_code == 403
    anon.client.cookies.delete("mp_member_1")
    anon.login("Anna")
    response = anon.post("/link/rotate")
    assert response.status_code == 303 and TOKEN not in response.headers["location"]
    assert anon.get("/plan").status_code == 404  # alter Link (self.base) ungültig


def _space_id(browser: Browser) -> int:
    with SessionLocal() as db:
        return db.scalars(select(Space.id).where(Space.token == browser.base.rsplit("/", 1)[1])).one()


# --- Start/Migration -------------------------------------------------------------------------


def test_existing_database_gets_an_admin_household(web):
    with SessionLocal() as db:
        space = db.scalars(select(Space)).one()
        space.is_admin = False
        db.commit()
        ensure_space(db, Settings(members="Anna,Ben"))
        assert db.get(Space, space.id).is_admin is True


def test_members_setting_only_applies_to_a_new_household(web):
    with SessionLocal() as db:
        ensure_space(db, Settings(members="Anna,Ben,Neu"))
        names = [m.name for m in db.scalars(select(Member).order_by(Member.id))]
    assert names == ["Anna", "Ben"]  # Profile werden danach in der App verwaltet


def test_migration_adds_household_and_badge_columns():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE spaces (id INTEGER PRIMARY KEY, token VARCHAR(100), name VARCHAR(100))"))
        conn.execute(text("CREATE TABLE shopping_items (id INTEGER PRIMARY KEY, space_id INTEGER, name VARCHAR(100))"))
        conn.execute(text("INSERT INTO spaces VALUES (1, 'tok', 'Familie')"))
    migrate(engine)
    migrate(engine)  # idempotent
    inspector = inspect(engine)
    assert {"is_admin", "created_at"} <= {c["name"] for c in inspector.get_columns("spaces")}
    assert {"position", "added_by", "checked_by"} <= {c["name"] for c in inspector.get_columns("shopping_items")}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT is_admin FROM spaces")).scalar() == 0


def test_cli_lists_all_households_and_rotates_by_id(web, capsys):
    from app.cli import main

    create_household(web, "Familie Müller", "Mia")
    assert main(["cli", "link"]) == 0
    out = capsys.readouterr().out
    assert "(Verwaltung)" in out and "Familie Müller" in out and out.count("/s/") == 2
    with SessionLocal() as db:
        other = db.scalars(select(Space).where(Space.is_admin.is_(False))).one()
        old = other.token
    assert main(["cli", "rotate", str(other.id)]) == 0
    with SessionLocal() as db:
        assert db.get(Space, other.id).token != old
        assert db.scalars(select(Space).where(Space.is_admin.is_(True))).one().token == TOKEN  # unverändert
    assert main(["cli", "rotate", "999"]) == 1
    assert main(["cli", "rotate", "abc"]) == 2


# --- Kürzel in der Einkaufsliste --------------------------------------------------------------


def test_badges_use_initials_and_two_letters_on_clash():
    result = households.badges(["Niko", "Partnerin", "Nina"])
    assert result["Partnerin"].text == "P"
    assert result["Niko"].text == "Ni" and result["Nina"].text == "Ni"  # gleicher Anfang → zwei Buchstaben
    assert [b.color for b in result.values()] == [0, 1, 2]
    assert households.badge_for(None, result) is None
    assert households.badge_for("Gelöscht", result).text == "G"


def _listing(browser: Browser) -> str:
    return browser.get("/shopping").text.split('id="shopping-list"', 1)[1]


def test_shopping_list_shows_who_added_and_who_bought(anon):
    anon.login("Anna")
    anon.post("/shopping/add", {"name": "Milch"})
    page = _listing(anon)
    assert re.search(r'<span class="who who-0" title="eingetragen von Anna"[^>]*>A</span>', page)

    anon.client.cookies.delete("mp_member_1")
    anon.login("Ben")
    with SessionLocal() as db:
        item_id = db.scalars(select(ShoppingItem.id)).one()
    anon.post(f"/shopping/{item_id}/toggle", {"state": "1"})
    done = _listing(anon).split("Gekauft", 1)[1]
    assert re.search(r'<span class="who who-1" title="in den Wagen gelegt von Ben"[^>]*>B</span>', done)

    anon.post(f"/shopping/{item_id}/toggle", {"state": "0"})
    with SessionLocal() as db:
        item = db.get(ShoppingItem, item_id)
        assert (item.added_by, item.checked_by) == ("Anna", None)
    assert "eingetragen von Anna" in _listing(anon)


def test_recipe_ingredients_remember_who_put_them_on_the_list(web):
    recipe_id = make_recipe(web, "Pfannkuchen", "250 g Mehl\n3 Eier")
    web.post("/plan/recipe", {"day": DAY, "recipe_id": recipe_id})
    with SessionLocal() as db:
        entry_id = db.scalars(select(PlanEntry.id)).one()
    web.post(f"/plan/entries/{entry_id}/shop")
    with SessionLocal() as db:
        assert {i.added_by for i in db.scalars(select(ShoppingItem))} == {"Anna"}


def test_merging_keeps_the_original_adder(anon):
    anon.login("Anna")
    anon.post("/shopping/add", {"name": "500 g Mehl"})
    anon.client.cookies.delete("mp_member_1")
    anon.login("Ben")
    anon.post("/shopping/add", {"name": "250 g Mehl"})
    with SessionLocal() as db:
        (item,) = db.scalars(select(ShoppingItem)).all()
    assert (item.amount, item.added_by) == (750.0, "Anna")


def test_undo_of_check_off_restores_buyer(anon):
    anon.login("Anna")
    anon.post("/shopping/add", {"name": "Milch"})
    with SessionLocal() as db:
        item_id = db.scalars(select(ShoppingItem.id)).one()
    anon.post(f"/shopping/{item_id}/toggle", {"state": "1"})
    anon.post("/undo")
    with SessionLocal() as db:
        item = db.get(ShoppingItem, item_id)
        assert (item.checked, item.checked_by) == (False, None)


def test_list_carries_own_badge_for_offline_check_off(web):
    web.post("/shopping/add", {"name": "Milch"})
    page = web.get("/shopping").text
    assert 'data-my-name="Anna" data-my-badge="A" data-my-color="0"' in page
    assert 'data-added-by="Anna" data-added-badge="A" data-added-color="0"' in page


def test_items_from_older_versions_have_no_badge(web):
    with SessionLocal() as db:
        space_id = db.scalars(select(Space.id)).one()
        db.add(ShoppingItem(space_id=space_id, name="Altbestand", category="Sonstiges", added_by=None))
        db.commit()
    page = _listing(web)
    row = page.split("Altbestand", 1)[1].split("</div>", 2)[1]
    assert 'class="who' not in row


def test_unknown_date_param_keeps_working(web):
    assert web.get(f"/plan?week={date(2026, 10, 21).isoformat()}").status_code == 200
