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


class PlatformPushBlockedError(PlatformError):
    """A quantity push cannot succeed against this listing as the marketplace currently
    has it configured, and no retry will change that — only the seller editing the
    listing will.

    Deliberately NOT a PlatformSyncError: every other push failure is transient by
    assumption, and services/listing_reconcile retries those on a schedule. Retrying a
    structural one spends daily API budget on a call that is guaranteed to fail, forever
    (the Etsy "quantity must be consistent across all products" case — see
    EtsyAdapter.push_listing_quantity). The message must name the fix in the seller's own
    terms, because it is shown to them verbatim as the thing to go and change.
    """
