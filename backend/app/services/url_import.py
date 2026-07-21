import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status

_MAX_BYTES = 20 * 1024 * 1024
_ALLOWED_SCHEMES = {"http", "https"}


def _reject_unsafe_host(hostname: str) -> None:
    """Basic SSRF hardening: the URL is user-supplied and the server fetches it, so
    reject anything resolving to loopback/private/link-local/reserved addresses —
    otherwise a user (or a malicious listing page) could point this at internal
    infrastructure (e.g. http://169.254.169.254/ cloud metadata, http://localhost:5432)."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not resolve host")

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="URL resolves to a disallowed address"
            )


async def fetch_image_bytes(url: str) -> tuple[bytes, str]:
    """Fetches image bytes from a user-supplied URL for the paste-a-listing-image-URL
    import flow. Returns (bytes, filename). Validates scheme, host, content-type, and
    size before trusting the response."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only http/https URLs are supported")
    if not parsed.hostname:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL is missing a host")

    _reject_unsafe_host(parsed.hostname)

    # Redirects are not followed: an attacker-controlled redirect could otherwise point
    # the server at an internal host after the initial hostname check above already passed.
    async with httpx.AsyncClient(follow_redirects=False, timeout=15.0) as client:
        try:
            response = await client.get(url)
        except httpx.HTTPError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to fetch URL: {e}")

        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail=f"URL returned HTTP {response.status_code}"
            )

        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL did not return an image")

        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > _MAX_BYTES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image exceeds 20MB size limit")

        data = response.content
        if len(data) > _MAX_BYTES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image exceeds 20MB size limit")

    extension = content_type.split("/")[-1].split(";")[0].strip() or "jpg"
    filename_from_url = parsed.path.rsplit("/", 1)[-1]
    filename = filename_from_url if "." in filename_from_url else f"image.{extension}"
    return data, filename
