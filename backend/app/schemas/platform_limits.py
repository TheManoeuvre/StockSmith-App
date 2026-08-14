from pydantic import BaseModel

from app.models.listing import ListingPlatform
from app.services.platform_limits import LimitField, Severity


class FieldViolation(BaseModel):
    """One value that breaches one limit.

    `message` carries the complete human sentence rather than leaving the frontend to
    assemble one from the parts — the house style (services/variants.py) is that the
    backend names the offending thing, the limit and the platform in one go, so the same
    wording appears everywhere it surfaces. The structured fields are there for sorting,
    filtering and highlighting, not for rebuilding the sentence.
    """

    field: LimitField
    severity: Severity
    current_value: str
    current_length: int | None
    limit: str
    imposed_by: ListingPlatform
    message: str
    # Present only where a mechanical fix is unambiguous. None means a human decides —
    # notably for every blocker, where "just truncate it" would be wrong.
    suggested_value: str | None


class UnitCompatibility(BaseModel):
    """Violations for one variant (or for the product itself when it has none), matching
    how listing_sync partitions a product into checkable units."""

    variant_id: int | None
    variant_name: str | None
    sku: str | None
    violations: list[FieldViolation]


class ProductCompatibility(BaseModel):
    product_id: int
    product_name: str
    product_sku: str | None
    # True when anything on the product or its units would stop a listing being created.
    is_blocked: bool
    violations: list[FieldViolation]
    units: list[UnitCompatibility]


class CatalogueCompatibilityReport(BaseModel):
    """Summary counts with per-product drill-down.

    `products` holds only the entries with something to report; `total_products` is the
    whole active catalogue, so the UI can say "3 of 26" without a second call.
    """

    platform: ListingPlatform
    total_products: int
    blocked_count: int
    warning_count: int
    products: list[ProductCompatibility]
