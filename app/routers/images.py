from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.models import Space
from app.services import images
from app.web import get_space

router = APIRouter(prefix="/s/{token}")

# The file name contains the hash of the original URL, the content never changes → cacheable forever.
IMAGE_HEADERS = {"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"}


@router.get("/img")
def cached_image(_space: Annotated[Space, Depends(get_space)], u: Annotated[str, Query(max_length=images.MAX_URL_LENGTH)]):
    """Serves a recipe image from the server cache (fetches it from the allowed source the first time)."""
    if not images.is_cacheable_url(u):
        raise HTTPException(404, "Bild nicht verfügbar")
    found = images.get_image_cache().get(u)
    if found is None:
        raise HTTPException(404, "Bild nicht verfügbar")
    path, mime = found
    return FileResponse(path, media_type=mime, headers=IMAGE_HEADERS)
