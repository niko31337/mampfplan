"""Seiten und Bilder von beliebigen, vom Nutzer eingegebenen Adressen laden – gegen SSRF abgesichert.

Der Rezept-Konverter muss fremde Seiten abrufen. Damit niemand über die App interne Dienste
erreicht (Router, NAS, Home Assistant, Docker-Netz, Cloud-Metadaten …), gilt:

- nur https auf Port 443, keine Zugangsdaten in der Adresse
- der Hostname muss ausschließlich auf öffentliche IP-Adressen zeigen (vor jeder Anfrage geprüft)
- nach dem Verbindungsaufbau wird die tatsächlich verbundene Adresse noch einmal geprüft
  (schützt gegen DNS-Rebinding), bevor die Antwort gelesen wird
- Weiterleitungen werden einzeln verfolgt und jede Zieladresse erneut geprüft (höchstens 5)
- Größenlimit, kurze Zeitlimits, kein Proxy aus Umgebungsvariablen
"""

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.recipe_sources.base import USER_AGENT

MAX_URL_LENGTH = 2000
MAX_PAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
HTML_TYPES = ("text/html", "application/xhtml+xml")


class FetchError(Exception):
    """Adresse nicht erlaubt oder Seite nicht abrufbar. Die Meldung ist für Nutzer gedacht."""


@dataclass
class Fetched:
    url: str  # endgültige Adresse nach Weiterleitungen
    content: bytes
    content_type: str
    charset: str | None

    def text(self) -> str:
        return decode_html(self.content, self.charset)


def validate_url(url: str) -> str:
    """Prüft die Form der Adresse und gibt sie bereinigt zurück (ohne Fragment)."""
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise FetchError("Bitte einen gültigen Link eingeben.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchError("Bitte einen gültigen Link eingeben.") from exc
    if parts.scheme != "https":
        raise FetchError("Nur Links mit https:// werden unterstützt.")
    if not parts.hostname or parts.username is not None or parts.password is not None:
        raise FetchError("Bitte einen gültigen Link eingeben.")
    if port not in (None, 443):
        raise FetchError("Links auf andere Ports als 443 werden nicht abgerufen.")
    return parts._replace(fragment="").geturl()


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def _resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:
        raise FetchError("Die Seite wurde nicht gefunden.") from exc
    return [info[4][0] for info in infos]


def check_host(host: str) -> None:
    """Der Host muss ausschließlich auf öffentliche Adressen zeigen."""
    addresses = _resolve(host)
    if not addresses or not all(_is_public(a) for a in addresses):
        raise FetchError("Dieser Link zeigt auf ein internes Netz und wird nicht abgerufen.")


def _check_peer(response: httpx.Response) -> None:
    stream = response.extensions.get("network_stream")
    if stream is None:  # z. B. HTTP/2 oder Test-Transport: Prüfung vor der Anfrage muss genügen
        return
    peer = stream.get_extra_info("server_addr")
    if peer and not _is_public(str(peer[0])):
        raise FetchError("Dieser Link zeigt auf ein internes Netz und wird nicht abgerufen.")


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"},
    )


def fetch(
    url: str,
    *,
    accept: tuple[str, ...] = HTML_TYPES,
    max_bytes: int = MAX_PAGE_BYTES,
    client: httpx.Client | None = None,
) -> Fetched:
    own_client = client is None
    client = client or make_client()
    try:
        current = validate_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            check_host(urlsplit(current).hostname)
            try:
                with client.stream("GET", current, headers={"Accept": ", ".join(accept) + ", */*;q=0.1"}) as response:
                    _check_peer(response)
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        current = validate_url(urljoin(current, location))
                        continue
                    if response.status_code != 200:
                        raise FetchError(f"Die Seite antwortet mit Fehler {response.status_code}.")
                    content_type, charset = _content_type(response.headers.get("content-type", ""))
                    if content_type not in accept:
                        raise FetchError("Unter diesem Link liegt keine Webseite.")
                    return Fetched(current, _read_limited(response, max_bytes), content_type, charset)
            except httpx.HTTPError as exc:
                raise FetchError("Die Seite ist gerade nicht erreichbar.") from exc
        raise FetchError("Zu viele Weiterleitungen.")
    finally:
        if own_client:
            client.close()


def _content_type(header: str) -> tuple[str, str | None]:
    main, *params = [p.strip() for p in header.split(";")]
    charset = None
    for param in params:
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset":
            charset = value.strip().strip('"') or None
    return main.lower(), charset


def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise FetchError("Die Seite ist zu groß.")
    chunks, total = [], 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise FetchError("Die Seite ist zu groß.")
        chunks.append(chunk)
    return b"".join(chunks)


_META_CHARSET = re.compile(rb"""<meta[^>]+charset=["']?([A-Za-z0-9_-]+)""", re.I)


def decode_html(content: bytes, charset: str | None = None) -> str:
    candidates = [charset] if charset else []
    found = _META_CHARSET.search(content[:4096])
    if found:
        candidates.append(found[1].decode("ascii"))
    candidates.append("utf-8")
    for candidate in candidates:
        try:
            return content.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")
