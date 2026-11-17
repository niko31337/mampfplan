"""Multiple languages (German, English, French, Spanish, Italian), deliberately lean, without extra packages.

- The key is the German source text (like gettext); if a translation is missing, it is shown.
- The language is per device: cookie `mp_lang`, otherwise the browser language, otherwise German.
- The current language lives in a ContextVar that the middleware sets per request.
"""

import json
import re
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path

LANGUAGES = {"de": "Deutsch", "en": "English", "fr": "Français", "es": "Español", "it": "Italiano"}
DEFAULT_LANGUAGE = "de"
LANG_COOKIE = "mp_lang"
DIR = Path(__file__).resolve().parent

_current: ContextVar[str] = ContextVar("mampfplan_lang", default=DEFAULT_LANGUAGE)


def get_language() -> str:
    return _current.get()


def set_language(lang: str):
    """Set the language for the current request/context; returns a token for resetting it."""
    return _current.set(lang if lang in LANGUAGES else DEFAULT_LANGUAGE)


def reset_language(token) -> None:
    _current.reset(token)


@lru_cache
def catalog(lang: str) -> dict[str, str]:
    if lang == DEFAULT_LANGUAGE:
        return {}
    path = DIR / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def translate(msgid: str, lang: str | None = None) -> str:
    return catalog(lang or get_language()).get(msgid) or msgid


def gettext(msgid: str, **kwargs) -> str:
    text = translate(msgid)
    return text % kwargs if kwargs else text


def ngettext(singular: str, plural: str, n: int, **kwargs) -> str:
    """Unformatted without kwargs (Jinja formats itself, incl. `num`); call with kwargs from Python."""
    text = translate(singular if n == 1 else plural)
    return text % {"num": n, **kwargs} if kwargs else text


def _(msgid: str, **kwargs) -> str:  # short form for Python code
    return gettext(msgid, **kwargs)


def N_(msgid: str) -> str:
    """Only mark (translated later when displayed)."""
    return msgid


_ACCEPT = re.compile(r"\s*([a-zA-Z]{1,8})(?:-[a-zA-Z0-9]{1,8})*\s*(?:;\s*q\s*=\s*([0-9.]+))?\s*")


def from_accept_language(header: str | None) -> str | None:
    """Best supported language from the Accept-Language header (with q values)."""
    best, best_q = None, 0.0
    for position, part in enumerate((header or "").split(",")[:20]):
        match = _ACCEPT.fullmatch(part)
        if not match:
            continue
        lang = match[1].lower()
        try:
            q = float(match[2]) if match[2] else 1.0
        except ValueError:
            continue
        q -= position * 1e-6  # with equal q the earlier entry wins
        if lang in LANGUAGES and q > best_q:
            best, best_q = lang, q
    return best


def pick_language(cookies, headers) -> str:
    chosen = cookies.get(LANG_COOKIE)
    if chosen in LANGUAGES:
        return chosen
    return from_accept_language(headers.get("accept-language")) or DEFAULT_LANGUAGE


# --- Dates and numbers -----------------------------------------------------------------------

WEEKDAYS = {
    "de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "fr": ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"],
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "it": ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"],
}
WEEKDAYS_SHORT = {
    "de": ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    "fr": ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."],
    "es": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"],
    "it": ["lun", "mar", "mer", "gio", "ven", "sab", "dom"],
}
MONTHS = {
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "fr": ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "it": ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"],
}  # fmt: skip


def weekday_name(d, lang: str | None = None) -> str:
    return WEEKDAYS[lang or get_language()][d.weekday()]


def date_long(d, lang: str | None = None) -> str:
    lang = lang or get_language()
    day, month, weekday = d.day, MONTHS[lang][d.month - 1], WEEKDAYS[lang][d.weekday()]
    if lang == "en":
        return f"{weekday}, {day} {month}"
    if lang in ("fr", "it"):
        return f"{weekday} {day} {month}"
    if lang == "es":
        return f"{weekday}, {day} de {month}"
    return f"{weekday}, {day}. {month}"


def date_short(d, lang: str | None = None) -> str:
    lang = lang or get_language()
    if lang == "en":
        return f"{d.day} {MONTHS['en'][d.month - 1][:3]}"
    return f"{d.day:02d}/{d.month:02d}" if lang in ("fr", "es", "it") else d.strftime("%d.%m.")


def date_numeric(d, lang: str | None = None) -> str:
    lang = lang or get_language()
    if lang == "en":
        return f"{d.day} {MONTHS['en'][d.month - 1][:3]} {d.year}"
    return f"{d.day:02d}/{d.month:02d}/{d.year}" if lang in ("fr", "es", "it") else d.strftime("%d.%m.%Y")


def decimal_separator(lang: str | None = None) -> str:
    return "." if (lang or get_language()) == "en" else ","
