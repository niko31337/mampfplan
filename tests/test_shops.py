"""Several shopping lists (one per store): configuration, choosing the list, moving items, order, collapsing."""

import re
from urllib.parse import unquote

import httpx
from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import CategoryOrder, Shop, ShoppingItem, Space
from app.services import images, shops
from app.services.images import ImageCache
from tests.conftest import TOKEN


def space_id() -> int:
    with SessionLocal() as db:
        return db.scalars(select(Space.id).where(Space.token == TOKEN)).one()


def all_shops() -> list[Shop]:
    with SessionLocal() as db:
        return list(db.scalars(select(Shop).where(Shop.space_id == space_id()).order_by(Shop.position, Shop.id)))


def shop_id(name: str) -> int:
    return next(s.id for s in all_shops() if s.name == name)


def items() -> list[ShoppingItem]:
    with SessionLocal() as db:
        return list(db.scalars(select(ShoppingItem).order_by(ShoppingItem.id)))


def item(name: str) -> ShoppingItem:
    return next(i for i in items() if i.name == name)


def add_shops(web, *kinds):
    for kind in kinds:
        assert web.post("/shops", {"kind": kind}).status_code == 303


# --- One list ------------------------------------------------------------------------------------


def test_household_starts_with_one_list_and_everything_lands_there(web):
    page = web.get("/shopping").text
    assert 'name="shop_id"' not in page  # nothing to choose
    assert "shop-head" not in page  # no collapsible list headers either
    web.post("/shopping/add", {"name": "Milch"})
    [shop] = all_shops()
    assert shop.kind == shops.DEFAULT_KIND
    assert item("Milch").shop_id == shop.id


def test_items_from_before_multiple_lists_are_adopted(web):
    web.post("/shopping/add", {"name": "Milch"})
    with SessionLocal() as db:  # data like before the update: no list
        db.execute(update(ShoppingItem).values(shop_id=None))
        db.commit()
    assert "Milch" in web.get("/shopping").text
    assert item("Milch").shop_id == all_shops()[0].id


def test_default_list_name_is_translated(web):
    web.client.cookies.set("mp_lang", "en")
    page = web.get("/more").text
    assert "Shopping list" in page


# --- Configuring lists ---------------------------------------------------------------------------


def test_add_known_stores_rename_and_reorder(web):
    add_shops(web, "lidl", "aldi_sued", "dm")
    assert [s.name for s in all_shops()] == ["Einkaufsliste", "Lidl", "Aldi Süd", "dm"]
    assert [s.kind for s in all_shops()][1:] == ["lidl", "aldi_sued", "dm"]

    web.post(f"/shops/{shop_id('dm')}/move", {"direction": "up"})
    assert [s.name for s in all_shops()] == ["Einkaufsliste", "Lidl", "dm", "Aldi Süd"]

    order = [shop_id("Aldi Süd"), shop_id("Lidl"), shop_id("dm"), shop_id("Einkaufsliste")]
    assert web.post("/shops/order", {"ids": order}, htmx=True).status_code == 204
    assert [s.id for s in all_shops()] == order

    web.post(f"/shops/{shop_id('Einkaufsliste')}/rename", {"name": "  Wochenmarkt  "})
    assert all_shops()[-1].name == "Wochenmarkt"
    assert all_shops()[-1].kind == shops.CUSTOM_KIND


def test_custom_name_of_known_store_gets_its_logo(web):
    add_shops(web)
    web.post("/shops", {"kind": "custom", "name": "rewe"})
    assert all_shops()[-1].kind == "rewe"
    web.post("/shops", {"kind": "custom", "name": "Bäcker Müller"})
    assert all_shops()[-1].kind == shops.CUSTOM_KIND


def test_invalid_list_names_are_rejected(web):
    web.get("/shopping")
    assert web.post("/shops", {"kind": "custom", "name": "   "}).status_code == 400
    add_shops(web, "lidl")
    response = web.post("/shops", {"kind": "custom", "name": "LIDL"})
    assert response.status_code == 400
    assert "gibt es schon" in response.text


def test_deleting_a_list_moves_its_items_and_can_be_undone(web):
    add_shops(web, "lidl")
    lidl = shop_id("Lidl")
    web.post("/shopping/add", {"name": "Bananen", "shop_id": lidl})
    web.post("/shopping/categories/order", {"shop_id": lidl, "categories": ["Tiefkühl"]})

    assert web.post(f"/shops/{lidl}/delete").status_code == 303
    [remaining] = all_shops()
    assert item("Bananen").shop_id == remaining.id
    with SessionLocal() as db:
        assert not db.scalars(select(CategoryOrder).where(CategoryOrder.shop_id == lidl)).all()

    web.post("/undo")
    assert shop_id("Lidl") == lidl
    assert item("Bananen").shop_id == lidl


def test_last_list_cannot_be_deleted(web):
    web.get("/shopping")
    [shop] = all_shops()
    response = web.post(f"/shops/{shop.id}/delete")
    assert response.status_code == 400
    assert len(all_shops()) == 1


def test_lists_of_other_households_are_off_limits(web):
    with SessionLocal() as db:
        other = Space(token="other-token-0123456789abcdefghij", name="Andere")
        db.add(other)
        db.flush()
        foreign = Shop(space_id=other.id, name="Fremd", kind="custom", position=0)
        db.add(foreign)
        db.commit()
        foreign_id = foreign.id
    assert web.post(f"/shops/{foreign_id}/delete").status_code == 404
    assert web.post(f"/shops/{foreign_id}/rename", {"name": "X"}).status_code == 404
    assert web.post("/shopping/add", {"name": "Milch", "shop_id": foreign_id}).status_code == 400


# --- Adding with several lists -------------------------------------------------------------------


def test_with_several_lists_the_user_must_choose(web):
    add_shops(web, "lidl", "dm")
    page = web.get("/shopping").text
    assert page.count('name="shop_id"') == 3
    assert "/shops/logo/lidl" in page and "/shops/logo/dm" in page

    response = web.post("/shopping/add", {"name": "Milch"}, htmx=True)
    assert response.status_code == 400
    assert "Einkaufsliste" in unquote(response.headers["x-mampf-message"])
    assert not items()

    web.post("/shopping/add", {"name": "Zahnpasta", "shop_id": shop_id("dm")}, htmx=True)
    assert item("Zahnpasta").shop_id == shop_id("dm")


def test_same_item_on_two_lists_is_not_merged(web):
    add_shops(web, "lidl", "dm")
    web.post("/shopping/add", {"name": "2 l Milch", "shop_id": shop_id("Lidl")})
    web.post("/shopping/add", {"name": "1 l Milch", "shop_id": shop_id("dm")})
    web.post("/shopping/add", {"name": "1 l Milch", "shop_id": shop_id("dm")})
    assert sorted((i.shop_id, i.amount) for i in items()) == sorted([(shop_id("Lidl"), 2), (shop_id("dm"), 2)])


def test_recipe_ingredients_go_to_the_chosen_list(web):
    from tests.test_plan import make_recipe

    add_shops(web, "rewe")
    recipe = make_recipe(web, "Pfannkuchen", "200 g Mehl\n2 Eier")
    with SessionLocal() as db:
        from app.models import Recipe

        ingredient_ids = [i.id for i in db.get(Recipe, recipe).ingredients]
    assert web.post(f"/recipes/{recipe}/shop", {"ingredient_ids": ingredient_ids}).status_code == 400
    assert 'name="shop_id"' in web.get(f"/recipes/{recipe}").text
    web.post(f"/recipes/{recipe}/shop", {"ingredient_ids": ingredient_ids, "shop_id": shop_id("Rewe")})
    assert {i.shop_id for i in items()} == {shop_id("Rewe")}

    web.post("/plan/recipe", {"day": "2026-10-21", "recipe_id": recipe})
    with SessionLocal() as db:
        from app.models import PlanEntry

        entry = db.scalars(select(PlanEntry)).one().id
    assert web.post(f"/plan/entries/{entry}/shop", htmx=True).status_code == 400
    first = all_shops()[0].id
    web.post(f"/plan/entries/{entry}/shop", {"shop_id": first}, htmx=True)
    assert {i.shop_id for i in items()} == {shop_id("Rewe"), first}


def test_catalog_offers_the_list_choice(web):
    add_shops(web, "lidl")
    page = web.get("/shopping/catalog").text
    assert 'id="catalog-shop"' in page
    assert 'hx-include="#catalog-shop"' in page


# --- Per list: categories, moving between lists --------------------------------------------------


def test_category_order_is_per_list(web):
    add_shops(web, "lidl", "aldi_nord")
    lidl, aldi = shop_id("Lidl"), shop_id("Aldi Nord")
    web.post("/shopping/categories/order", {"shop_id": lidl, "categories": ["Getränke", "Tiefkühl"]})
    web.post("/shopping/categories/order", {"shop_id": aldi, "categories": ["Tiefkühl", "Getränke"]})
    with SessionLocal() as db:
        from app.services.shopping import category_order

        lidl_order = [c for c in category_order(db, space_id(), lidl) if c in ("Getränke", "Tiefkühl")]
        aldi_order = [c for c in category_order(db, space_id(), aldi) if c in ("Getränke", "Tiefkühl")]
    assert lidl_order == ["Getränke", "Tiefkühl"]
    assert aldi_order == ["Tiefkühl", "Getränke"]

    page = web.get(f"/shopping/categories?shop={aldi}").text
    assert f'data-shop="{aldi}"' in page
    assert 'aria-current="page"' in page


def test_move_item_to_another_list_keeps_its_category(web):
    add_shops(web, "lidl", "dm")
    lidl, dm = shop_id("Lidl"), shop_id("dm")
    web.post("/shopping/add", {"name": "Zahnpasta", "shop_id": lidl})
    category = item("Zahnpasta").category
    assert category == "Hygiene & Drogerie"

    response = web.post(f"/shopping/{item('Zahnpasta').id}/shop", {"shop_id": dm}, htmx=True)
    assert response.status_code == 200
    moved = item("Zahnpasta")
    assert (moved.shop_id, moved.category) == (dm, category)
    # the list shows it in the dm section, in its category
    section = re.search(rf'<details class="shop-list" data-shop="{dm}".*?</details>', response.text, re.S)[0]
    assert "Zahnpasta" in section and 'data-category="Hygiene &amp; Drogerie"' in section


def test_move_to_list_that_has_the_item_merges(web):
    add_shops(web, "lidl", "dm")
    web.post("/shopping/add", {"name": "2 Seife", "shop_id": shop_id("Lidl")})
    web.post("/shopping/add", {"name": "3 Seife", "shop_id": shop_id("dm")})
    lidl_soap = next(i for i in items() if i.shop_id == shop_id("Lidl"))
    web.post(f"/shopping/{lidl_soap.id}/shop", {"shop_id": shop_id("dm")})
    [soap] = items()
    assert (soap.shop_id, soap.amount) == (shop_id("dm"), 5)
    web.post("/undo")
    assert sorted(i.amount for i in items()) == [2, 3]


def test_drag_into_another_list(web):
    add_shops(web, "lidl", "dm")
    web.post("/shopping/add", {"name": "Bananen", "shop_id": shop_id("Lidl")})
    bananas = item("Bananen")
    web.post(
        f"/shopping/{bananas.id}/move",
        {"category": "Obst & Gemüse", "shop_id": shop_id("dm"), "ids": [bananas.id]},
    )
    assert item("Bananen").shop_id == shop_id("dm")


# --- Shopping page -------------------------------------------------------------------------------


def test_lists_appear_in_configured_order_and_can_be_collapsed(web):
    add_shops(web, "lidl", "dm")
    lidl, dm = shop_id("Lidl"), shop_id("dm")
    web.post("/shops/order", {"ids": [dm, lidl]})
    page = web.get("/shopping").text
    positions = [page.index(f'class="shop-list" data-shop="{s.id}"') for s in all_shops()]
    assert positions == sorted(positions)
    assert page.index(f'data-shop="{dm}"') < page.index(f'data-shop="{lidl}"')
    assert re.search(rf'data-shop="{lidl}" open>', page)

    web.client.cookies.set(f"mp_closed_{space_id()}", f"{lidl}.999")
    page = web.get("/shopping").text
    assert re.search(rf'data-shop="{lidl}">', page)  # collapsed
    assert re.search(rf'data-shop="{dm}" open>', page)


def test_household_deletion_removes_lists(web):
    from app.services import households

    with SessionLocal() as db:
        other = Space(token="other-token-0123456789abcdefghij", name="Andere")
        db.add(other)
        db.flush()
        other_id = other.id
        db.commit()
    with SessionLocal() as db:
        shops.shops_of(db, other_id)
        assert db.scalars(select(Shop).where(Shop.space_id == other_id)).all()
        households.delete_household(db, db.get(Space, other_id))
    with SessionLocal() as db:
        assert not db.scalars(select(Shop).where(Shop.space_id == other_id)).all()


# --- Logos ---------------------------------------------------------------------------------------

ICO = b"\x00\x00\x01\x00" + b"\x00" * 60


def test_logo_is_the_favicon_fetched_on_the_server(web, tmp_path, monkeypatch):
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "www.lidl.de":
            return httpx.Response(301, headers={"location": "https://static.lidl.de/favicon.ico"})
        return httpx.Response(200, content=ICO)

    cache = ImageCache(tmp_path / "cache", httpx.Client(transport=httpx.MockTransport(upstream)), 10_000_000)
    monkeypatch.setattr(images, "get_image_cache", lambda: cache)
    response = web.get("/shops/logo/lidl")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/x-icon"
    assert calls == ["https://www.lidl.de/favicon.ico", "https://static.lidl.de/favicon.ico"]
    web.get("/shops/logo/lidl")
    assert len(calls) == 2  # cached


def test_logo_redirect_to_foreign_site_is_not_followed(web, tmp_path, monkeypatch):
    calls = []

    def upstream(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.example/x.ico"})

    cache = ImageCache(tmp_path / "cache", httpx.Client(transport=httpx.MockTransport(upstream)), 10_000_000)
    monkeypatch.setattr(images, "get_image_cache", lambda: cache)
    response = web.get("/shops/logo/rewe")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")  # colored tile instead
    assert "evil.example" not in calls
    assert web.get("/shops/logo/unknown").status_code == 404
