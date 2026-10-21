"""Bild-Cache: Rezeptbilder werden einmal serverseitig geladen und danach von hier ausgeliefert.

So fragen die Browser der Familie nie direkt bei Chefkoch & Co. an. Geladen wird nur von fest
hinterlegten Bildservern (kein Abruf beliebiger URLs → kein SSRF) – ohne Weiterleitungen, mit
Größenlimit und Prüfung des Dateiinhalts (nur JPEG/PNG/GIF/WebP, nie SVG).
"""

import hashlib
import logging
import os
import threading
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from app.config import get_settings
from app.recipe_sources.base import USER_AGENT

log = logging.getLogger("mampfplan.images")

ALLOWED_HOSTS = frozenset(
    {
        "img.chefkoch-cdn.de",  # Chefkoch
        "www.themealdb.com",  # TheMealDB
        "themealdb.com",
        "img.spoonacular.com",  # Spoonacular
    }
)
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_URL_LENGTH = 500
DOWNLOAD_TIMEOUT = 8.0
FAILURE_TTL_SECONDS = 300
TOUCH_AFTER_SECONDS = 24 * 3600
PRUNE_EVERY_N_WRITES = 20

MIME_BY_EXT = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}


class ImageError(Exception):
    pass


def is_allowed_url(url: str | None) -> bool:
    """Nur https-URLs der erlaubten Bildserver (ohne Zugangsdaten und ohne fremden Port)."""
    if not url or len(url) > MAX_URL_LENGTH:
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and parts.hostname in ALLOWED_HOSTS
        and parts.username is None
        and parts.password is None
        and port in (None, 443)
    )


def sniff_extension(data: bytes) -> str | None:
    """Dateityp am Inhalt erkennen – dem Content-Type der Gegenseite wird nicht vertraut."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def cached_image_src(url: str | None, base: str) -> str:
    """Adresse, unter der die eigene App das (zwischengespeicherte) Bild ausliefert; leer, wenn nicht erlaubt."""
    if not is_allowed_url(url):
        return ""
    return f"{base}/img?u={quote(url, safe='')}"


class ImageCache:
    def __init__(self, directory: Path, client: httpx.Client, max_bytes: int):
        self.directory = directory
        self.client = client
        self.max_bytes = max_bytes
        self._failures: dict[str, float] = {}
        self._writes = 0
        self._lock = threading.Lock()
        self.directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def lookup(self, url: str) -> tuple[Path, str] | None:
        key = self.key(url)
        for ext, mime in MIME_BY_EXT.items():
            path = self.directory / f"{key}.{ext}"
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > TOUCH_AFTER_SECONDS:  # zuletzt benutzt vermerken, damit Aufräumen die ältesten zuerst löscht
                try:
                    os.utime(path)
                except OSError:
                    pass
            return path, mime
        return None

    def get(self, url: str) -> tuple[Path, str] | None:
        """Pfad und MIME-Typ des Bildes – aus dem Cache oder frisch geladen; None, wenn nicht möglich."""
        if not is_allowed_url(url):
            return None
        hit = self.lookup(url)
        if hit:
            return hit
        key = self.key(url)
        if time.monotonic() - self._failures.get(key, -FAILURE_TTL_SECONDS) < FAILURE_TTL_SECONDS:
            return None  # kürzlich gescheitert: nicht bei jedem Seitenaufruf erneut versuchen
        try:
            data, ext = self._download(url)
        except ImageError as exc:
            log.info("Bild nicht geladen (%s): %s", exc, urlsplit(url).path[-60:])
            self._failures[key] = time.monotonic()
            return None
        final = self.directory / f"{key}.{ext}"
        temp = self.directory / f".{key}.{os.getpid()}.{threading.get_ident()}.tmp"
        temp.write_bytes(data)
        os.replace(temp, final)  # atomar: nie halb geschriebene Dateien ausliefern
        self._failures.pop(key, None)
        self._after_write()
        return final, MIME_BY_EXT[ext]

    def _download(self, url: str) -> tuple[bytes, str]:
        try:
            with self.client.stream("GET", url, follow_redirects=False) as response:
                if response.status_code != 200:  # inkl. Weiterleitungen: könnten auf fremde Server zeigen
                    raise ImageError(f"HTTP {response.status_code}")
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > MAX_IMAGE_BYTES:
                    raise ImageError("zu groß")
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_IMAGE_BYTES:
                        raise ImageError("zu groß")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise ImageError(type(exc).__name__) from exc
        data = b"".join(chunks)
        ext = sniff_extension(data)
        if ext is None:
            raise ImageError("kein unterstütztes Bildformat")
        return data, ext

    def _after_write(self) -> None:
        with self._lock:
            self._writes += 1
            due = self._writes % PRUNE_EVERY_N_WRITES == 0
        if due:
            self.prune()

    def prune(self) -> int:
        """Älteste Dateien löschen, bis der Cache unter dem Limit liegt. Rückgabe: gelöschte Dateien."""
        files = []
        for path in self.directory.iterdir():
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if path.is_file():
                files.append((stat.st_mtime, stat.st_size, path))
        total = sum(size for _, size, _ in files)
        removed = 0
        for _, size, path in sorted(files):
            if total <= self.max_bytes:
                break
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            total -= size
            removed += 1
        return removed


def _make_client() -> httpx.Client:
    return httpx.Client(timeout=DOWNLOAD_TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=False)


@lru_cache
def get_image_cache() -> ImageCache:
    settings = get_settings()
    return ImageCache(settings.image_cache_path, _make_client(), settings.image_cache_max_mb * 1024 * 1024)
