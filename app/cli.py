"""Kleine Verwaltungsbefehle.

    python -m app.cli link      zeigt den persönlichen Link
    python -m app.cli rotate    erzeugt einen neuen Link (der alte wird sofort ungültig)
"""

import secrets
import sys

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Space
from app.services.seed import share_url

COMMANDS = {"link", "rotate"}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print(__doc__)
        return 2
    with SessionLocal() as db:
        space = db.scalars(select(Space).order_by(Space.id)).first()
        if space is None:
            print("Noch kein Space vorhanden – bitte die App einmal starten.")
            return 1
        if argv[1] == "rotate":
            space.token = secrets.token_urlsafe(32)
            db.commit()
        print(share_url(get_settings(), space))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
