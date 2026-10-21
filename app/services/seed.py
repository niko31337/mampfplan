import json
import logging
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import CatalogItem, Member, Space

log = logging.getLogger("mampfplan")
CATALOG_FILE = Path(__file__).resolve().parents[2] / "seed" / "catalog.json"


def seed_catalog(db: Session) -> None:
    existing = set(db.scalars(select(CatalogItem.name)))
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    for category, names in data.items():
        for name in names:
            if name not in existing:
                db.add(CatalogItem(name=name, category=category))
    db.commit()


def ensure_space(db: Session, settings: Settings) -> Space:
    """Legt beim ersten Start die Familie samt Mitgliedern an und gibt den Teilen-Link aus."""
    space = db.scalars(select(Space).order_by(Space.id)).first()
    if space is None:
        space = Space(token=settings.space_token or secrets.token_urlsafe(32), name=settings.space_name)
        db.add(space)
        db.flush()
        log.warning("Erster Start: persönlicher Link: %s", share_url(settings, space))

    known = set(db.scalars(select(Member.name).where(Member.space_id == space.id)))
    for name in settings.member_names:
        if name not in known:
            db.add(Member(space_id=space.id, name=name))
    db.commit()
    return space


def share_url(settings: Settings, space: Space) -> str:
    return f"{settings.public_url.rstrip('/')}/s/{space.token}"
