from tests.conftest import BASE
from tests.test_plan import make_recipe


def test_service_worker_is_served_from_root_with_wide_scope(anon):
    response = anon.client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["service-worker-allowed"] == "/"
    assert "no-cache" in response.headers["cache-control"]
    assert "mampfplan-static" in response.text


def test_offline_fallback_page_is_public_and_precached(anon):
    response = anon.client.get("/offline")
    assert response.status_code == 200 and "Offline" in response.text
    assert "/offline" in anon.client.get("/sw.js").text  # wird beim Installieren vorab gecacht


def test_csp_allows_worker_but_still_blocks_inline_script(web):
    csp = web.get("/plan").headers["content-security-policy"]
    assert "worker-src 'self'" in csp and "script-src 'self'" in csp and "unsafe-inline" not in csp


def test_static_assets_for_offline_use_exist(anon):
    for path in ("/static/sortable.min.js", "/static/htmx.min.js", "/static/app.js", "/static/style.css", "/static/icon.svg"):
        assert anon.client.get(path).status_code == 200, path


def test_all_precached_assets_are_served(anon):
    sw = anon.client.get("/sw.js").text
    start = sw.index("const PRECACHE = [") + len("const PRECACHE = [")
    entries = [e.strip().strip(",").strip('"') for e in sw[start : sw.index("];", start)].split("\n") if e.strip()]
    assert entries
    for path in entries:
        assert anon.client.get(path).status_code == 200, path


def test_pages_include_manifest_and_scripts_in_expected_order(web):
    page = web.get("/plan").text
    assert page.index("htmx.min.js") < page.index("sortable.min.js") < page.index("app.js")
    assert f'href="{BASE}/manifest.webmanifest"' in page


def test_recipe_pages_request_a_screen_wake_lock(web):
    recipe_id = make_recipe(web, "Pasta")
    detail = web.get(f"/recipes/{recipe_id}").text
    assert "data-wake-lock" in detail and "data-wake-status" in detail
    assert "data-wake-lock" not in web.get("/plan").text  # nur auf Rezeptseiten
    assert "data-wake-lock" not in web.get("/shopping").text
