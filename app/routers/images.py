from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from app.models import Space
from app.services import images, shops
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


@router.get("/shops/logo/{key}")
def shop_logo(_space: Annotated[Space, Depends(get_space)], key: str):
    """Logo of a known store: its favicon (fetched once on the server), otherwise a colored tile."""
    preset = shops.PRESETS.get(key)
    if preset is None:
        raise HTTPException(404, "Logo nicht verfügbar")
    cache = images.get_image_cache()
    for url in preset.logo_urls:
        found = cache.get_from_site(url, preset.domain)
        if found is not None:
            path, mime = found
            return FileResponse(path, media_type=mime, headers=LOGO_HEADERS)
    # fallback: don't cache for long, so the real favicon shows up once it can be fetched
    return Response(
        shops.fallback_logo_svg(preset),
        media_type="image/svg+xml",
        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
    )


LOGO_HEADERS = {"Cache-Control": "private, max-age=604800", "X-Content-Type-Options": "nosniff"}
