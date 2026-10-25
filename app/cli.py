"""Kleine Verwaltungsbefehle.

    python -m app.cli link          zeigt die Links aller Haushalte
    python -m app.cli rotate [ID]   neuer Link für Haushalt ID (Standard: Verwaltungs-Haushalt);
                                    der alte Link wird sofort ungültig, die Daten bleiben erhalten
"""

import sys

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Space
from app.services import households
from app.services.seed import share_url


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command not in {"link", "rotate"} or len(argv) > 3 or (command == "link" and len(argv) > 2):
        print(__doc__)
        return 2
    settings = get_settings()
    with SessionLocal() as db:
        if command == "link":
            infos = households.list_households(db)
            if not infos:
                print("Noch kein Haushalt vorhanden – bitte die App einmal starten.")
                return 1
            for info in infos:
                marker = " (Verwaltung)" if info.space.is_admin else ""
                print(f"[{info.space.id}] {info.space.name}{marker}: {share_url(settings, info.space)}")
            return 0

        if len(argv) == 3:
            if not argv[2].isdigit():
                print("ID muss eine Zahl sein (siehe: python -m app.cli link)")
                return 2
            space = db.get(Space, int(argv[2]))
        else:
            space = db.scalars(select(Space).where(Space.is_admin.is_(True)).order_by(Space.id)).first()
        if space is None:
            print("Haushalt nicht gefunden (siehe: python -m app.cli link)")
            return 1
        households.rotate_token(db, space)
        print(share_url(settings, space))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
