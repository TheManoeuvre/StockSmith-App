"""Fills blank local fields from the Etsy listings a product is already matched to.

Every active product in the live catalogue has a NULL description, half have no
sale_price, and a handful have no hero image — while the listings those products are
already synced to carry all three on Etsy's side. Nothing was ever wrong; the data simply
only ever travelled one way. Draft-push needs description and price locally, so this
closes the gap using a crawl the app already performs on every sync.

Three rules shape the whole module:

  * **Fill blanks only.** A value StockSmith already holds is never overwritten, and a
    disagreement is not a conflict to resolve — it is StockSmith's own number and stays.
    That keeps the operation safe to run repeatedly and safe to run without reading every
    row of the preview.
  * **Preview then apply, per product.** The user ticks what to take. The apply re-derives
    everything from a fresh crawl rather than trusting the previewed payload, on the same
    reasoning as push_product_corrections: a preview minutes old is not what should be
    written.
  * **Per-offering prices, never the listing price.** Etsy documents `listing.price` as
    *the minimum possible price*, so on a varying product it is the cheapest variant.
    Writing that to every variant would quietly under-price the range.

This helps only products already live on Etsy — by definition not the ones the draft-push
flow targets. It is nonetheless what makes the catalogue capable of producing a draft at
all, since nothing can be drafted without a description.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import AssetType, ProductAsset
from app.models.listing import Listing, ListingPlatform
from app.models.platform_connection import PlatformConnection
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services import file_storage
from app.services.url_import import fetch_image_bytes
from app.services.variants import compute_full_sku

# What a caller may ask to take. Kept as plain strings rather than an enum column: this is
# a transient request shape, not stored state.
FIELD_DESCRIPTION = "description"
FIELD_PRICE = "price"
FIELD_IMAGE = "image"
ALL_FIELDS = (FIELD_DESCRIPTION, FIELD_PRICE, FIELD_IMAGE)


@dataclass
class VariantPriceProposal:
    variant_id: int
    variant_name: str
    sku: str
    proposed_price: Decimal


@dataclass
class ProductProposal:
    product_id: int
    product_name: str
    external_listing_id: str
    listing_title: str | None
    # Each is None when there is nothing to fill — either the marketplace had no value or
    # StockSmith already holds one.
    description: str | None = None
    description_chars: int = 0
    sale_price: Decimal | None = None
    image_url: str | None = None
    variant_prices: list[VariantPriceProposal] = field(default_factory=list)

    @property
    def has_anything(self) -> bool:
        return bool(
            self.description or self.sale_price is not None or self.image_url or self.variant_prices
        )


@dataclass
class BackfillPreview:
    products: list[ProductProposal]
    # Products matched to an Etsy listing that need nothing — reported as a count so the
    # user can see the scan covered them without a screen of "nothing to do" rows.
    already_complete: int
    # Matched to no Etsy listing at all, so there is nothing to read from.
    unmatched: int


@dataclass
class BackfillResult:
    products_updated: int
    descriptions_filled: int
    prices_filled: int
    images_filled: int
    errors: list[str] = field(default_factory=list)


def _price_from_money(money: dict | None) -> Decimal | None:
    """Etsy money is an integer amount over a divisor, not a float."""
    if not money:
        return None
    amount = money.get("amount")
    divisor = money.get("divisor") or 1
    if amount is None or not divisor:
        return None
    return (Decimal(amount) / Decimal(divisor)).quantize(Decimal("0.01"))


def _offering_price(product_entry: dict) -> Decimal | None:
    for offering in product_entry.get("offerings", []):
        if offering.get("is_deleted"):
            continue
        price = _price_from_money(offering.get("price"))
        if price is not None:
            return price
    return None


def _prices_by_sku(listing: dict) -> dict[str, Decimal]:
    """Per-SKU prices off the listing's inventory — the only prices that are actually
    per-variant. See the module docstring on why listing.price is not usable here."""
    prices: dict[str, Decimal] = {}
    for entry in (listing.get("inventory") or {}).get("products", []):
        sku = entry.get("sku")
        if not sku or entry.get("is_deleted"):
            continue
        price = _offering_price(entry)
        if price is not None:
            prices[sku] = price
    return prices


def _hero_image_url(listing: dict) -> str | None:
    """The rank-1 image, which is the one Etsy shows first. Falls back to the lowest rank
    present, since rank numbering has been seen to start above 1 on older listings."""
    images = [img for img in (listing.get("images") or []) if img.get("url_fullxfull")]
    if not images:
        return None
    images.sort(key=lambda img: img.get("rank") or 0)
    return images[0]["url_fullxfull"]


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


async def _matched_listings(session: AsyncSession) -> dict[int, str]:
    """product_id -> Etsy listing id, for products with at least one confirmed match.

    On Etsy `external_listing_id` holds the listing id (on eBay it holds the SKU — the
    documented overloading), so this is only meaningful for the Etsy rows it filters to.
    """
    rows = (
        await session.execute(
            select(Listing.product_id, Listing.external_listing_id).where(
                Listing.platform == ListingPlatform.etsy, Listing.external_listing_id.is_not(None)
            )
        )
    ).all()
    return {product_id: listing_id for product_id, listing_id in rows}


async def build_preview(session: AsyncSession, listings: list[dict]) -> BackfillPreview:
    """Works out what could be filled, from an already-fetched crawl.

    Takes the listings rather than fetching them so the same logic is drivable from a test
    with a captured payload and no marketplace at all."""
    by_id = {str(listing.get("listing_id")): listing for listing in listings}
    matched = await _matched_listings(session)

    products = list(
        (await session.execute(select(Product).where(Product.is_active.is_(True)))).scalars()
    )
    variants = list(
        (
            await session.execute(
                select(ProductVariant).where(ProductVariant.is_active.is_(True))
            )
        ).scalars()
    )
    variants_by_product: dict[int, list[ProductVariant]] = {}
    for variant in variants:
        variants_by_product.setdefault(variant.product_id, []).append(variant)

    has_image = set(
        (
            await session.execute(
                select(ProductAsset.product_id).where(ProductAsset.asset_type == AssetType.main_image)
            )
        ).scalars()
    )

    proposals: list[ProductProposal] = []
    already_complete = 0
    unmatched = 0

    for product in products:
        listing_id = matched.get(product.id)
        listing = by_id.get(listing_id) if listing_id else None
        if listing is None:
            unmatched += 1
            continue

        proposal = ProductProposal(
            product_id=product.id,
            product_name=product.name,
            external_listing_id=str(listing_id),
            listing_title=listing.get("title") or None,
        )

        if _blank(product.description):
            description = listing.get("description")
            # rich_description is HTML and Etsy's own schema requires sanitising it before
            # rendering. Product.description is plain text, so take the plain field —
            # which Etsy documents as always populated — and leave rich text alone.
            if not _blank(description):
                proposal.description = description
                proposal.description_chars = len(description)

        prices = _prices_by_sku(listing)
        product_variants = variants_by_product.get(product.id, [])
        if product_variants:
            for variant in product_variants:
                if variant.sale_price is not None:
                    continue
                sku = compute_full_sku(product.sku, variant.sku_suffix)
                price = prices.get(sku) if sku else None
                if price is not None:
                    proposal.variant_prices.append(
                        VariantPriceProposal(
                            variant_id=variant.id,
                            variant_name=variant.variant_name,
                            sku=sku,
                            proposed_price=price,
                        )
                    )
        elif product.sale_price is None:
            price = prices.get(product.sku) if product.sku else None
            if price is None:
                # A single-product listing with no SKU-keyed offering: the listing price
                # is unambiguous here, because there is only one thing being sold.
                price = _price_from_money(listing.get("price"))
            proposal.sale_price = price

        if product.id not in has_image:
            proposal.image_url = _hero_image_url(listing)

        if proposal.has_anything:
            proposals.append(proposal)
        else:
            already_complete += 1

    proposals.sort(key=lambda p: p.product_name.lower())
    return BackfillPreview(products=proposals, already_complete=already_complete, unmatched=unmatched)


async def apply_backfill(
    session: AsyncSession,
    connection: PlatformConnection,
    listings: list[dict],
    selections: dict[int, set[str]],
) -> BackfillResult:
    """Applies the ticked fields for the ticked products.

    Re-derives every value from `listings` (a fresh crawl) rather than accepting values
    from the caller — the preview the user looked at may be minutes old, and this writes
    to their catalogue.

    A failed image download is recorded and skipped rather than aborting: one unreachable
    CDN URL should not cost the user the other twenty-five products' descriptions.
    """
    preview = await build_preview(session, listings)
    by_product = {p.product_id: p for p in preview.products}

    result = BackfillResult(products_updated=0, descriptions_filled=0, prices_filled=0, images_filled=0)

    for product_id, fields in selections.items():
        proposal = by_product.get(product_id)
        if proposal is None:
            continue
        product = await session.get(Product, product_id)
        if product is None:
            continue

        touched = False

        if FIELD_DESCRIPTION in fields and proposal.description and _blank(product.description):
            product.description = proposal.description
            result.descriptions_filled += 1
            touched = True

        if FIELD_PRICE in fields:
            if proposal.sale_price is not None and product.sale_price is None:
                product.sale_price = proposal.sale_price
                result.prices_filled += 1
                touched = True
            for variant_price in proposal.variant_prices:
                variant = await session.get(ProductVariant, variant_price.variant_id)
                if variant is not None and variant.sale_price is None:
                    variant.sale_price = variant_price.proposed_price
                    result.prices_filled += 1
                    touched = True

        if FIELD_IMAGE in fields and proposal.image_url:
            try:
                data, filename = await fetch_image_bytes(proposal.image_url)
                relative_path, stored_name = file_storage.save_upload(
                    product.id, product.name, AssetType.main_image, filename, data
                )
                session.add(
                    ProductAsset(
                        product_id=product.id,
                        asset_type=AssetType.main_image,
                        file_path=relative_path,
                        original_filename=stored_name,
                    )
                )
                result.images_filled += 1
                touched = True
            except Exception as e:  # noqa: BLE001 — reported per product, never fatal
                result.errors.append(f"{product.name}: could not import image ({e})")

        if touched:
            result.products_updated += 1

    await session.commit()
    return result
