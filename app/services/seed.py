import json
import logging
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CatalogItem, CatalogName, Member, Space

log = logging.getLogger("mampfplan")
CATALOG_FILE = Path(__file__).resolve().parents[2] / "seed" / "catalog.json"
TRANSLATIONS_FILE = CATALOG_FILE.with_name("catalog.translations.json")


def seed_catalog(db: Session) -> None:
    existing = set(db.scalars(select(CatalogItem.name)))
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    for category, names in data.items():
        for name in names:
            if name not in existing:
                db.add(CatalogItem(name=name, category=category))
    db.commit()
    _seed_catalog_names(db)


def _seed_catalog_names(db: Session) -> None:
    """Übersetzte Katalognamen (en/fr/es) nachtragen; vorhandene bleiben unverändert."""
    if not TRANSLATIONS_FILE.exists():
        return
    data = json.loads(TRANSLATIONS_FILE.read_text(encoding="utf-8"))
    items = {item.name: item.id for item in db.scalars(select(CatalogItem))}
    present = set(db.execute(select(CatalogName.catalog_item_id, CatalogName.lang)).all())
    for german, names in data.items():
        item_id = items.get(german)
        if item_id is None or not isinstance(names, list):
            continue
        for lang, name in zip(("en", "fr", "es"), names, strict=False):
            if (item_id, lang) not in present and name:
                db.add(CatalogName(catalog_item_id=item_id, lang=lang, name=name[:100]))
    db.commit()


def ensure_space(db: Session, settings: Settings) -> Space:
    """Legt beim ersten Start den Verwaltungs-Haushalt samt Profilen (MEMBERS) an und gibt den Link aus.

    Danach werden Haushalte und Profile in der App verwaltet; MEMBERS wirkt nur, solange der erste
    Haushalt noch keine Profile hat.
    """
    space = db.scalars(select(Space).order_by(Space.id)).first()
    if space is None:
        space = Space(
            token=settings.space_token or secrets.token_urlsafe(32), name=settings.space_name, is_admin=True
        )
        db.add(space)
        db.flush()
        log.warning("Erster Start: persönlicher Link: %s", share_url(settings, space))
    elif not db.scalars(select(Space.id).where(Space.is_admin.is_(True))).first():
        space.is_admin = True  # Bestandsdatenbank vor Einführung der Haushalte: erster Haushalt verwaltet

    if not db.scalars(select(Member.id).where(Member.space_id == space.id)).first():
        for name in settings.member_names:
            db.add(Member(space_id=space.id, name=name))
    db.commit()
    return space


def share_url(settings: Settings, space: Space) -> str:
    return f"{settings.public_url.rstrip('/')}/s/{space.token}"
