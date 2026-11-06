"""Abruf fremder Seiten für den Rezept-Konverter: SSRF-Schutz, Weiterleitungen, Limits."""

import httpx
import pytest

from app.converter import fetch as fetching
from app.converter.fetch import FetchError, check_host, decode_html, fetch, validate_url

PUBLIC = "93.184.215.14"


@pytest.fixture
def dns(monkeypatch):
    """Gefälschte Namensauflösung: Host → Adressen."""
    table = {"rezepte.example": [PUBLIC], "cdn.example": [PUBLIC]}
    monkeypatch.setattr(fetching, "_resolve", lambda host: table.get(host, [PUBLIC]))
    return table


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def html_response(body="<html><body>Hallo</body></html>", **headers):
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/html; charset=utf-8", **headers})


@pytest.mark.parametrize(
    "url",
    [
        "http://rezepte.example/a",  # kein https
        "ftp://rezepte.example/a",
        "file:///etc/passwd",
        "https://rezepte.example:8443/a",  # anderer Port
        "https://user:pass@rezepte.example/a",  # Zugangsdaten
        "https:///pfad",
        "javascript:alert(1)",
        "",
        "https://" + "a" * 2001,
    ],
)
def test_invalid_urls_are_rejected(url):
    with pytest.raises(FetchError):
        validate_url(url)


def test_valid_url_drops_the_fragment():
    assert validate_url(" https://rezepte.example/a?b=1#kommentare ") == "https://rezepte.example/a?b=1"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "10.88.0.1",  # VPN
        "192.168.1.202",  # Heimnetz
        "172.18.0.2",  # Docker-Netz
        "169.254.169.254",  # Cloud-Metadaten
        "100.64.0.1",  # Carrier-NAT
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:192.168.1.1",
    ],
)
def test_internal_addresses_are_rejected(monkeypatch, address):
    monkeypatch.setattr(fetching, "_resolve", lambda host: [address])
    with pytest.raises(FetchError, match="internes Netz"):
        check_host("intern.example")


def test_host_with_one_internal_address_is_rejected(monkeypatch):
    monkeypatch.setattr(fetching, "_resolve", lambda host: [PUBLIC, "192.168.1.1"])
    with pytest.raises(FetchError):
        check_host("gemischt.example")


def test_public_host_is_fetched(dns):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return html_response()

    page = fetch("https://rezepte.example/a", client=client(handler))
    assert seen == ["https://rezepte.example/a"]
    assert page.text() == "<html><body>Hallo</body></html>"


def test_internal_host_is_never_requested(monkeypatch):
    monkeypatch.setattr(fetching, "_resolve", lambda host: ["192.168.1.202"])
    calls = []
    with pytest.raises(FetchError):
        fetch("https://ha.example/", client=client(lambda r: calls.append(r) or html_response()))
    assert calls == []


def test_redirects_are_followed_and_checked(dns):
    dns["intern.example"] = ["10.0.0.1"]

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(301, headers={"location": "/ziel"})
        if request.url.path == "/ziel":
            return httpx.Response(302, headers={"location": "https://intern.example/geheim"})
        raise AssertionError("interner Host wurde angefragt")

    with pytest.raises(FetchError, match="internes Netz"):
        fetch("https://rezepte.example/start", client=client(handler))


def test_relative_redirect_to_public_page_works(dns):
    def handler(request):
        if request.url.path == "/alt":
            return httpx.Response(301, headers={"location": "/neu"})
        return html_response("<p>neu</p>")

    page = fetch("https://rezepte.example/alt", client=client(handler))
    assert page.url == "https://rezepte.example/neu"


@pytest.mark.parametrize("location", ["http://rezepte.example/neu", "https://rezepte.example:8080/neu", "file:///etc/passwd"])
def test_redirect_to_disallowed_url_is_rejected(dns, location):
    with pytest.raises(FetchError):
        fetch("https://rezepte.example/alt", client=client(lambda r: httpx.Response(302, headers={"location": location})))


def test_redirect_loop_stops(dns):
    with pytest.raises(FetchError, match="Weiterleitungen"):
        fetch("https://rezepte.example/a", client=client(lambda r: httpx.Response(302, headers={"location": "/a"})))


def test_error_status_is_reported(dns):
    with pytest.raises(FetchError, match="404"):
        fetch("https://rezepte.example/weg", client=client(lambda r: httpx.Response(404)))


def test_non_html_is_rejected(dns):
    response = httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"})
    with pytest.raises(FetchError, match="keine Webseite"):
        fetch("https://rezepte.example/a.pdf", client=client(lambda r: response))


def test_size_limit(dns):
    big = html_response("x" * 2000)
    with pytest.raises(FetchError, match="zu groß"):
        fetch("https://rezepte.example/a", client=client(lambda r: big), max_bytes=1000)


def test_network_errors_become_fetch_errors(dns):
    def handler(request):
        raise httpx.ConnectTimeout("timeout")

    with pytest.raises(FetchError, match="nicht erreichbar"):
        fetch("https://rezepte.example/a", client=client(handler))


def test_images_can_be_fetched_with_image_types(dns):
    response = httpx.Response(200, content=b"\xff\xd8\xff" + b"j" * 50, headers={"content-type": "image/jpeg"})
    image = fetch("https://cdn.example/a.jpg", accept=("image/jpeg",), client=client(lambda r: response))
    assert image.content.startswith(b"\xff\xd8\xff")


def test_decoding_uses_header_meta_and_fallback():
    assert decode_html("Möhre".encode("latin-1"), "iso-8859-1") == "Möhre"
    assert decode_html(b'<meta charset="windows-1252">M\xf6hre') == '<meta charset="windows-1252">Möhre'
    assert decode_html("Möhre".encode()) == "Möhre"
    assert "M" in decode_html(b"M\xf6hre", "unbekannt")
