"""Square REST calls shared by connect_square (routers/platforms.py) and SquareAdapter
(square.py). Thin and stateless on purpose — no token refresh logic, because a Square
personal access token has none (see docs/plan-square-integration.md); every call here just
takes the token and environment it needs directly rather than a PlatformConnection.
"""

import httpx

from app.models.platform_credential import PlatformEnvironment
from app.services.platforms.errors import PlatformAuthError, PlatformSyncError

_BASE_URLS = {
    PlatformEnvironment.production: "https://connect.squareup.com/v2",
    PlatformEnvironment.sandbox: "https://connect.squareupsandbox.com/v2",
}

# Pinned rather than "latest" so a Square-side version bump can't silently change response
# shapes underneath the adapter. Matches the version exercised by the sandbox spike
# (scripts/dev/square_sandbox_spike.py).
_SQUARE_VERSION = "2024-10-17"


async def _request(access_token: str, environment: PlatformEnvironment, method: str, path: str, json: dict | None = None) -> dict:
    base_url = _BASE_URLS[environment]
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Square-Version": _SQUARE_VERSION,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.request(method, f"{base_url}{path}", headers=headers, json=json)
        except httpx.HTTPError as e:
            raise PlatformSyncError(f"Could not reach Square: {e}") from e

    if response.status_code == 401:
        raise PlatformAuthError("Square rejected this access token")
    if response.status_code >= 400:
        try:
            detail = response.json().get("errors", [{}])[0].get("detail", response.text)
        except ValueError:
            detail = response.text
        raise PlatformSyncError(f"Square returned an error: {detail}")

    try:
        return response.json()
    except ValueError:
        return {}


async def fetch_locations(access_token: str, environment: PlatformEnvironment) -> list[dict]:
    """The seller's locations, as Square's raw {id, name, ...} objects — used both to
    validate a freshly-pasted token and to populate the location picker."""
    body = await _request(access_token, environment, "GET", "/locations")
    return body.get("locations", [])


async def search_orders(
    access_token: str,
    environment: PlatformEnvironment,
    location_id: str,
    *,
    updated_since: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> dict:
    """One page of SearchOrders, sorted oldest-updated-first so a cursor from a failed
    page can always be resumed without skipping anything newer. `updated_since` is an
    RFC 3339 timestamp string (Square's date_time_filter), inclusive."""
    query: dict = {"sort": {"sort_field": "UPDATED_AT", "sort_order": "ASC"}}
    if updated_since is not None:
        query["filter"] = {"date_time_filter": {"updated_at": {"start_at": updated_since}}}
    payload: dict = {"location_ids": [location_id], "limit": limit, "query": query}
    if cursor is not None:
        payload["cursor"] = cursor
    return await _request(access_token, environment, "POST", "/orders/search", json=payload)


async def get_payment(access_token: str, environment: PlatformEnvironment, payment_id: str) -> dict:
    """A single payment by id — used as the follow-up call that returns processing_fee,
    which is absent from the payment object at the moment it's taken (see the sandbox
    spike's finding on this in docs/plan-square-integration.md)."""
    body = await _request(access_token, environment, "GET", f"/payments/{payment_id}")
    return body.get("payment", {})


async def batch_retrieve_catalog_objects(access_token: str, environment: PlatformEnvironment, object_ids: list[str]) -> list[dict]:
    """The CatalogObjects (item variations) behind a set of line items' catalog_object_id
    — the only place Square exposes a SKU for a catalogue-linked line item; it is not on
    the order line item itself. NOT yet exercised against a real sandbox catalogue item
    (the sandbox spike used ad-hoc line items with no catalog_object_id) — treat the
    resulting sku mapping as unverified until checked against a real one."""
    if not object_ids:
        return []
    body = await _request(
        access_token, environment, "POST", "/catalog/batch-retrieve", json={"object_ids": object_ids}
    )
    return body.get("objects", [])
