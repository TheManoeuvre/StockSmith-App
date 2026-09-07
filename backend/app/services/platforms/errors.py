class PlatformError(Exception):
    """Base for all platform-adapter errors — callers can catch this without caring
    which specific failure mode occurred."""


class PlatformAuthError(PlatformError):
    """Token exchange/refresh failed, or the connection has been revoked on the
    marketplace's side. Callers should surface this as "reconnect required"."""


class PlatformRateLimitError(PlatformError):
    """The marketplace's API rate limit was hit. Callers should back off."""


class PlatformSyncError(PlatformError):
    """A request to the marketplace API failed for a reason other than auth/rate-limit
    (bad request, unexpected response shape, network error)."""


class PlatformListingStructuralError(PlatformSyncError):
    """A quantity push can never succeed with the listing configured as it is on the
    marketplace — the seller has to change something there first (e.g. an Etsy listing
    whose quantity doesn't vary by variation, so every variant is forced to share one
    number). A subclass of PlatformSyncError so existing broad handlers still catch it,
    but distinct so services/listing_push can mark the listing structurally unpushable
    instead of logging it as one more retryable failure the badge keeps counting.

    The message is user-facing and must name the fix."""
