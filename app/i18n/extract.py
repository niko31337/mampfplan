"""Alle übersetzbaren Texte (msgids) aus Templates und Python-Code einsammeln.

Wird von den Tests genutzt (jede Übersetzungsdatei muss vollständig sein) und hilft beim Ergänzen:
    python -m app.i18n.extract fr     → listet fehlende französische Übersetzungen
"""

import json
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1]

_STR = r"""(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')"""
_TEMPLATE_CALLS = [
    re.compile(r"(?<![\w.])_\(\s*" + _STR),
    re.compile(r"ngettext\(\s*" + _STR + r"\s*,\s*" + _STR),
]
_PY_CALLS = [
    re.compile(r"(?<![\w.])_\(\s*" + _STR),
    re.compile(r"(?<![\w.])N_\(\s*" + _STR),
    re.compile(r"history\.msg\(\s*" + _STR),
    re.compile(r"HTTPException\(\s*(?:status_code=)?\d+\s*,\s*(?:detail=)?" + _STR),
]


def _unescape(raw: str) -> str:
    return raw.encode("utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8") if "\\" in raw else raw


def _strings(match: re.Match) -> list[str]:
    groups = [g for g in match.groups() if g is not None]
    return [_unescape(g) for g in groups]


def msgids() -> set[str]:
    found: set[str] = set()
    for path in (APP / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        for pattern in _TEMPLATE_CALLS:
            for match in pattern.finditer(text):
                found.update(_strings(match))
    for path in APP.rglob("*.py"):
        if path.parent.name == "i18n":
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in _PY_CALLS:
            for match in pattern.finditer(text):
                found.update(_strings(match))
    from app.services.shopping import CATEGORY_ORDER
    from app.web import JS_TEXTS

    found.update(JS_TEXTS)
    found.update(CATEGORY_ORDER)
    return {m for m in found if m.strip()}


def missing(lang: str) -> list[str]:
    path = Path(__file__).with_name(f"{lang}.json")
    have = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return sorted(m for m in msgids() if m not in have)


if __name__ == "__main__":
    for msgid in missing(sys.argv[1]) if len(sys.argv) > 1 else sorted(msgids()):
        print(json.dumps(msgid, ensure_ascii=False))
