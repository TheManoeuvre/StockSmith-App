"""Minimal Square REST calls needed to connect a Square account — not a full
PlatformAdapter (see docs/plan-square-integration.md phase 4, not yet built). Square is
connected via a pasted personal access token rather than an OAuth redirect, so there is no
authorize_url/exchange_code dance to implement; the only thing needed before a location can
be chosen is a way to validate that token and list the seller's locations.
"""

import httpx

from app.models.platform_credential import PlatformEnvironment
from app.services.platforms.errors import PlatformAuthError, PlatformSyncError

_BASE_URLS = {
    PlatformEnvironment.production: "https://connect.squareup.com/v2",
    PlatformEnvironment.sandbox: "https://connect.squareupsandbox.com/v2",
}

# Pinned rather than "latest" so a Square-side version bump can't silently change response
# shapes underneath the adapter this will grow into. Matches the version exercised by the
# sandbox spike (scripts/dev/square_sandbox_spike.py).
_SQUARE_VERSION = "2024-10-17"


async def fetch_locations(access_token: str, environment: PlatformEnvironment) -> list[dict]:
    """The seller's locations, as Square's raw {id, name, ...} objects — used both to
    validate a freshly-pasted token and to populate the location picker."""
    base_url = _BASE_URLS[environment]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Square-Version": _SQUARE_VERSION,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(f"{base_url}/locations", headers=headers)
        except httpx.HTTPError as e:
            raise PlatformSyncError(f"Could not reach Square: {e}") from e

    if response.status_code == 401:
        raise PlatformAuthError("Square rejected this access token")
    if response.status_code >= 400:
        detail = response.json().get("errors", [{}])[0].get("detail", response.text)
        raise PlatformSyncError(f"Square returned an error: {detail}")

    return response.json().get("locations", [])
