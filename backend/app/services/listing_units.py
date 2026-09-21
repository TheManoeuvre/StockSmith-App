"""Which Listing rows stand for a unit StockSmith currently sells.

A product is sold either as itself (no active variants) or as its active variants — never
both. listing_sync._unit_checks is the definition of that in Python; this module is the
same rule as a SQL predicate, so the push paths can apply it without loading every
variant of every product.

Why a row can be for a unit that no longer exists: Listing rows are keyed
(product, variant, platform) and are never deleted when a product's shape changes. A
product that was checked and linked *before* it gained variants keeps its product-level
row, external_listing_id and all; a variant that is deactivated keeps its row. Nothing in
the UI shows those rows any more (the Stores tab only renders current units), and no sync
check ever revisits them (same reason) — but until this filter existed the hourly
reconcile sweep still pushed them, and they failed every time, forever. Confirmed live on
2026-09-19: a product-level Etsy row pushing the parent SKU to a listing that only carries
the variant SKUs, erroring hourly, counted as a "listing not receiving stock updates" on a
product whose own Stores tab showed every variant synced.

Kept dependency-free (models only) so listing_push, listing_reconcile, listing_sync and
sync_status can all import it without a cycle.
"""

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.sql.elements import ColumnElement

from app.models.listing import Listing
from app.models.variant import ProductVariant


def current_unit_filter() -> ColumnElement[bool]:
    """SQL predicate on Listing: the row's unit is one listing_sync._unit_checks would
    check today. A product-level row (variant_id NULL) qualifies only while the product
    has no active variant; a variant row only while that variant is active."""
    product_has_active_variant = exists(
        select(ProductVariant.id).where(
            ProductVariant.product_id == Listing.product_id,
            ProductVariant.is_active.is_(True),
        )
    )
    this_variant_is_active = exists(
        select(ProductVariant.id).where(
            ProductVariant.id == Listing.variant_id,
            ProductVariant.is_active.is_(True),
        )
    )
    return or_(
        and_(Listing.variant_id.is_(None), ~product_has_active_variant),
        and_(Listing.variant_id.is_not(None), this_variant_is_active),
    )
