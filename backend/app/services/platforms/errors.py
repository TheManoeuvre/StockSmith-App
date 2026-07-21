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
