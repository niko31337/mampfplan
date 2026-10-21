from tests.conftest import BASE


def test_landing_page_does_not_leak_token(anon):
    response = anon.client.get("/")
    assert response.status_code == 200
    assert "test-token" not in response.text


def test_unknown_token_is_404(anon):
    assert anon.client.get("/s/falsches-token/plan", follow_redirects=False).status_code == 404


def test_profile_required(anon):
    response = anon.get("/plan")
    assert response.status_code == 303
    assert response.headers["location"] == f"{BASE}/who"


def test_htmx_without_profile_gets_hx_redirect(anon):
    response = anon.client.get(f"{BASE}/plan", headers={"HX-Request": "true"}, follow_redirects=False)
    assert response.headers["HX-Redirect"] == f"{BASE}/who"


def test_post_without_csrf_is_rejected(web):
    response = web.post("/plan/note", {"day": "2026-10-21", "text": "x"}, csrf=False)
    assert response.status_code == 403


def test_post_with_wrong_csrf_is_rejected(web):
    response = web.client.post(
        f"{BASE}/plan/note", data={"day": "2026-10-21", "text": "x"}, headers={"X-CSRF-Token": "falsch"}, follow_redirects=False
    )
    assert response.status_code == 403


def test_csrf_form_field_is_accepted(web):
    token = web.client.cookies.get("mp_csrf")
    response = web.client.post(
        f"{BASE}/plan/note", data={"day": "2026-10-21", "text": "x", "csrf_token": token}, follow_redirects=False
    )
    assert response.status_code == 303


def test_security_headers_and_no_docs(web):
    response = web.get("/plan")
    assert response.status_code == 200
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert web.client.get("/docs").status_code == 404
    assert web.client.get("/openapi.json").status_code == 404


def test_cookies_are_httponly(anon):
    response = anon.client.get(f"{BASE}/who")
    cookies = response.headers.get_list("set-cookie")
    assert any("mp_csrf" in c and "HttpOnly" in c for c in cookies)


def test_cli_rotate_invalidates_old_link(web, capsys):
    from app.cli import main

    assert main(["cli", "rotate"]) == 0
    new_link = capsys.readouterr().out.strip()
    assert new_link.rsplit("/", 1)[1] != "test-token-0123456789abcdefghij"
    assert web.get("/plan").status_code == 404  # alter Token ist ungültig


def test_html_is_escaped(web):
    web.post("/plan/note", {"day": "2026-10-21", "text": "<script>alert(1)</script>"})
    page = web.get("/plan?week=2026-10-21").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
