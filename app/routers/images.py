from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.models import Space
from app.services import images
from app.web import get_space

router = APIRouter(prefix="/s/{token}")

# Dateiname enthält den Hash der Original-URL, der Inhalt ändert sich nie → dauerhaft cachebar.
IMAGE_HEADERS = {"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}


@router.get("/img")
def cached_image(_space: Annotated[Space, Depends(get_space)], u: Annotated[str, Query(max_length=images.MAX_URL_LENGTH)]):
    """Liefert ein Rezeptbild aus dem Server-Cache (lädt es beim ersten Mal von der erlaubten Quelle)."""
    if not images.is_allowed_url(u):
        raise HTTPException(404, "Bild nicht verfügbar")
    found = images.get_image_cache().get(u)
    if found is None:
        raise HTTPException(404, "Bild nicht verfügbar")
    path, mime = found
    return FileResponse(path, media_type=mime, headers=IMAGE_HEADERS)
