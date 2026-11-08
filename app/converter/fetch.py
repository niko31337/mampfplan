"""Fetch pages and images from arbitrary user-supplied addresses, hardened against SSRF.

The recipe converter has to fetch foreign pages. So that nobody can reach internal services through
the app (router, NAS, Home Assistant, Docker network, cloud metadata …):

- https on port 443 only, no credentials in the address
- the hostname must resolve exclusively to public IP addresses (checked before every request)
- after connecting, the address actually connected to is checked again
  (protects against DNS rebinding) before the response is read
- redirects are followed one by one and every target is checked again (at most 5)
- size limit, short timeouts, no proxy from environment variables
"""

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from app.recipe_sources.base import USER_AGENT
from app.i18n import _

MAX_URL_LENGTH = 2000
MAX_PAGE_BYTES = 6 * 1024 * 1024
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_REDIRECTS = 5
TIMEOUT = httpx.Timeout(10.0, connect=5.0)
HTML_TYPES = ("text/html", "application/xhtml+xml")


class FetchError(Exception):
    """Address not allowed or page not retrievable. The message is meant for users."""


@dataclass
class Fetched:
    url: str  # final address after redirects
    content: bytes
    content_type: str
    charset: str | None

    def text(self) -> str:
        return decode_html(self.content, self.charset)


def validate_url(url: str) -> str:
    """Checks the shape of the address and returns it cleaned up (without fragment)."""
    url = (url or "").strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise FetchError(_("Bitte einen gültigen Link eingeben."))
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise FetchError(_("Bitte einen gültigen Link eingeben.")) from exc
    if parts.scheme != "https":
        raise FetchError(_("Nur Links mit https:// werden unterstützt."))
    if not parts.hostname or parts.username is not None or parts.password is not None:
        raise FetchError(_("Bitte einen gültigen Link eingeben."))
    if port not in (None, 443):
        raise FetchError(_("Links auf andere Ports als 443 werden nicht abgerufen."))
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
        raise FetchError(_("Die Seite wurde nicht gefunden.")) from exc
    return [info[4][0] for info in infos]


def check_host(host: str) -> None:
    """The host must resolve exclusively to public addresses."""
    addresses = _resolve(host)
    if not addresses or not all(_is_public(a) for a in addresses):
        raise FetchError(_("Dieser Link zeigt auf ein internes Netz und wird nicht abgerufen."))


def _check_peer(response: httpx.Response) -> None:
    stream = response.extensions.get("network_stream")
    if stream is None:  # e.g. HTTP/2 or test transport: the check before the request has to suffice
        return
    peer = stream.get_extra_info("server_addr")
    if peer and not _is_public(str(peer[0])):
        raise FetchError(_("Dieser Link zeigt auf ein internes Netz und wird nicht abgerufen."))


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
        for _hop in range(MAX_REDIRECTS + 1):
            check_host(urlsplit(current).hostname)
            try:
                with client.stream("GET", current, headers={"Accept": ", ".join(accept) + ", */*;q=0.1"}) as response:
                    _check_peer(response)
                    if response.is_redirect:
                        location = response.headers.get("location", "")
                        current = validate_url(urljoin(current, location))
                        continue
                    if response.status_code != 200:
                        raise FetchError(_("Die Seite antwortet mit Fehler %(code)s.", code=response.status_code))
                    content_type, charset = _content_type(response.headers.get("content-type", ""))
                    if content_type not in accept:
                        raise FetchError(_("Unter diesem Link liegt keine Webseite."))
                    return Fetched(current, _read_limited(response, max_bytes), content_type, charset)
            except httpx.HTTPError as exc:
                raise FetchError(_("Die Seite ist gerade nicht erreichbar.")) from exc
        raise FetchError(_("Zu viele Weiterleitungen."))
    finally:
        if own_client:
            client.close()


def _content_type(header: str) -> tuple[str, str | None]:
    main, *params = [p.strip() for p in header.split(";")]
    charset = None
    for param in params:
        key, _sep, value = param.partition("=")
        if key.strip().lower() == "charset":
            charset = value.strip().strip('"') or None
    return main.lower(), charset


def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise FetchError(_("Die Seite ist zu groß."))
    chunks, total = [], 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise FetchError(_("Die Seite ist zu groß."))
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
