"""Supplier logos: fetch a favicon from the supplier's website, or normalise an uploaded image.

Both routes end the same way — a PNG no bigger than 256px on a side, stored as
`suppliers/<id>/logo.png` under the asset root. PNG rather than the JPEG material thumbnails use,
because logos are mostly transparent-background artwork and JPEG would turn that black.
"""

import io
import re
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException, status
from PIL import Image, UnidentifiedImageError

from app.services.file_storage import asset_root, delete_asset_file
from app.services.url_import import _ALLOWED_SCHEMES, _reject_unsafe_host

_LOGO_MAX_DIM = 256
_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_MAX_HTML_BYTES = 512 * 1024
_MAX_ICON_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 3
_MAX_CANDIDATES = 4
_FETCH_TIMEOUT = 5.0

_LINK_TAG = re.compile(r"<link\b[^>]*>", re.IGNORECASE)
_ATTR = re.compile(r"""([a-zA-Z-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")


def normalise_logo(data: bytes) -> bytes:
    """Decode any image PIL understands (PNG, JPEG, GIF, ICO, WebP…) and re-encode as a PNG that
    fits within 256px. Raises ValueError for anything that isn't a decodable image — SVG
    included, which PIL can't read."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            # .ico files hold several sizes; PIL loads the largest by default after load().
            converted = image.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("Not a supported image (use PNG, JPEG, GIF, WebP or ICO)") from exc
    converted.thumbnail((_LOGO_MAX_DIM, _LOGO_MAX_DIM))
    buf = io.BytesIO()
    converted.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _logo_relative_path(supplier_id: int) -> str:
    return (Path("suppliers") / f"{supplier_id:04d}" / "logo.png").as_posix()


def store_logo(supplier_id: int, png: bytes) -> str:
    """Write the normalised PNG, replacing any previous file. Returns the relative path."""
    relative = _logo_relative_path(supplier_id)
    target = asset_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(png)
    return relative


def remove_logo_file(relative_path: str | None) -> None:
    if relative_path:
        delete_asset_file(relative_path)


def logo_from_upload(data: bytes) -> bytes:
    """Validate an uploaded logo, returning the normalised PNG or raising a 400."""
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No image received")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Logo exceeds 5MB size limit")
    try:
        return normalise_logo(data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _get(client: httpx.AsyncClient, url: str, max_bytes: int) -> httpx.Response | None:
    """GET with manual redirect-following, re-checking the host at every hop.

    Real sites redirect constantly (http→https, bare→www), so unlike the listing-image import this
    has to follow them — but letting httpx do it would skip the SSRF check on each new host.
    Returns None on any failure; a missing favicon is never an error worth surfacing.
    """
    for _ in range(_MAX_REDIRECTS + 1):
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
            return None
        try:
            _reject_unsafe_host(parsed.hostname)
            response = await client.get(url)
        except (HTTPException, httpx.HTTPError):
            return None
        if response.is_redirect:
            location = response.headers.get("location")
            if not location:
                return None
            url = urljoin(url, location)
            continue
        if response.status_code != 200 or len(response.content) > max_bytes:
            return None
        return response
    return None


def _icon_candidates(html: str, base_url: str) -> list[str]:
    """Icon URLs declared in the page's <link> tags, best first: touch icons (large, square) ahead
    of plain favicons."""
    touch: list[str] = []
    plain: list[str] = []
    for tag in _LINK_TAG.findall(html):
        attrs = {m.group(1).lower(): unescape(m.group(2) or m.group(3) or m.group(4) or "") for m in _ATTR.finditer(tag)}
        rel = attrs.get("rel", "").lower().split()
        href = attrs.get("href", "").strip()
        if not href or "icon" not in " ".join(rel):
            continue
        absolute = urljoin(base_url, href)
        (touch if "apple-touch-icon" in rel else plain).append(absolute)
    return touch + plain


async def fetch_favicon(website_url: str) -> bytes | None:
    """Best-effort: find and download the site's icon, returning a normalised PNG or None.

    Never raises — the supplier is saved regardless, and "no favicon found" is an ordinary
    outcome (the user can upload one).
    """
    url = website_url.strip()
    if not url:
        return None
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES or not parsed.hostname:
        return None
    origin = f"{parsed.scheme}://{parsed.netloc}"

    headers = {"User-Agent": "StockSmith favicon fetch"}
    async with httpx.AsyncClient(follow_redirects=False, timeout=_FETCH_TIMEOUT, headers=headers) as client:
        candidates: list[str] = []
        page = await _get(client, url, _MAX_HTML_BYTES)
        if page is not None and "html" in page.headers.get("content-type", "").lower():
            candidates = _icon_candidates(page.text, str(page.url))
        candidates.append(f"{origin}/favicon.ico")

        seen: set[str] = set()
        tried = 0
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            if tried >= _MAX_CANDIDATES:
                break
            tried += 1
            response = await _get(client, candidate, _MAX_ICON_BYTES)
            if response is None:
                continue
            try:
                return normalise_logo(response.content)
            except ValueError:
                continue  # an SVG, or an HTML error page served as 200 — try the next one
    return None
