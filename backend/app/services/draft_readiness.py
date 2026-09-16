"""Can a draft listing be built for this product right now, and if not, what's missing.

Purely local — no adapter, no marketplace call. That is what lets the product page load it
alongside everything else and drive a button's enabled state without a round trip, so the
user never clicks "create draft" only to be told it was never possible.

Three classes of finding, and the distinction is the whole design:

  * **blocker** — the call cannot be made. Either the marketplace requires the field
    outright, or the value breaches a hard limit. Never guessed around: taxonomy, policies
    and condition determine categorisation, tax treatment and fee band, so inventing one
    is how a seller ends up paying a wrong final-value fee.
  * **warning** — the draft can be created, but something will be adjusted or is worth
    knowing first.
  * **omitted** — an optional field with no value simply isn't sent. No key, never an
    empty string, never a zero. This is the "incomplete fields are omitted rather than
    block the draft" rule, and it covers everything not listed as required below.

The required sets are taken from the marketplaces' own schemas. Etsy's createDraftListing
requires exactly quantity, title, description, price, who_made, when_made and taxonomy_id,
plus a shipping profile and a readiness state (processing profile) for a physical listing
— the docs list readiness_state_id as optional, but the live endpoint 400s a physical
draft without one, so it is treated as required here too. Notably `image_ids` is *optional*
there:
a draft can be created with no image at all, and only publishing needs one — so a missing
hero image is a warning here, not a blocker.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import AssetType, ProductAsset
from app.models.listing import ListingPlatform
from app.models.listing_profile import ListingProfile
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.models.variant import ProductVariant
from app.services import listing_copy, listing_profiles, listing_push
from app.services.shipping_profiles import get_shipping_profiles_by_id, resolve_variant_shipping_profile
from app.services.platform_limits import (
    LimitField,
    Severity,
    check_length,
    check_product,
    load_limit_table,
    resolve_effective_limits,
)

BLOCKER = "blocker"
WARNING = "warning"


@dataclass
class ReadinessIssue:
    field: str
    severity: str
    message: str
    # Where the user goes to fix it. Naming the place is the difference between a report
    # that is actionable and one that is merely accurate.
    fix_hint: str | None = None


# Where a draft's marketplace shipping id came from — see resolve_marketplace_shipping.
SHIPPING_FROM_SHIPPING_PROFILE = "shipping_profile"
SHIPPING_FROM_LISTING_PROFILE = "listing_profile"


@dataclass
class MarketplaceShipping:
    """The marketplace shipping profile / postage policy id a draft should carry, and where
    it came from. `value` is None when neither source has one; `source` says which won."""

    value: int | str | None
    source: str | None
    # The product's (or its variants' shared) local shipping profile, whether or not it is
    # linked — so the readiness message can name it.
    shipping_profile: ShippingProfile | None

    @property
    def source_label(self) -> str | None:
        if self.source == SHIPPING_FROM_SHIPPING_PROFILE and self.shipping_profile is not None:
            return f"shipping profile '{self.shipping_profile.name}'"
        if self.source == SHIPPING_FROM_LISTING_PROFILE:
            return "listing profile (fallback)"
        return None


@dataclass
class DraftReadiness:
    product_id: int
    platform: ListingPlatform
    can_create: bool
    profile_id: int | None
    profile_name: str | None
    title: str
    title_source: str
    description_chars: int
    unit_count: int
    priced_unit_count: int
    image_count: int
    issues: list[ReadinessIssue] = field(default_factory=list)
    shipping: MarketplaceShipping | None = None

    @property
    def blockers(self) -> list[ReadinessIssue]:
        return [i for i in self.issues if i.severity == BLOCKER]

    @property
    def shipping_source(self) -> str | None:
        return self.shipping.source if self.shipping else None

    @property
    def shipping_source_label(self) -> str | None:
        return self.shipping.source_label if self.shipping else None


# Fields each marketplace refuses a create call without. Everything absent from these
# lists falls under the omit rule.
_ETSY_REQUIRED: list[tuple[str, str, str]] = [
    ("etsy_taxonomy_id", "Category", "Etsy needs a category (taxonomy) before it will accept a listing."),
    ("etsy_who_made", "Who made it", "Etsy needs to know who made this."),
    ("etsy_when_made", "When made", "Etsy needs to know when this was made."),
    (
        "etsy_readiness_state_id",
        "Processing profile",
        "Etsy requires a processing profile (readiness state) for a physical listing.",
    ),
]

_EBAY_REQUIRED: list[tuple[str, str, str]] = [
    ("ebay_category_id", "Category", "eBay needs a category id."),
    ("ebay_condition", "Condition", "eBay needs an item condition."),
    ("ebay_payment_policy_id", "Payment policy", "eBay needs a payment business policy."),
    ("ebay_return_policy_id", "Returns policy", "eBay needs a returns business policy."),
    ("ebay_merchant_location_key", "Location", "eBay needs the location this ships from."),
]


# The marketplace shipping id is required by both marketplaces, but unlike the fields above
# it has two sources, so it is checked by _shipping_issue rather than listed here.
_SHIPPING_FIELD = {
    ListingPlatform.etsy: ("etsy_shipping_profile_id", "Etsy shipping profile"),
    ListingPlatform.ebay: ("ebay_fulfillment_policy_id", "eBay postage policy"),
}


def _link_id(profile: ShippingProfile, platform: ListingPlatform) -> int | str | None:
    if platform == ListingPlatform.etsy:
        return profile.etsy_shipping_profile_id
    if platform == ListingPlatform.ebay:
        return profile.ebay_fulfillment_policy_id
    return None


def _listing_profile_shipping(profile: ListingProfile | None, platform: ListingPlatform) -> int | str | None:
    if profile is None:
        return None
    if platform == ListingPlatform.etsy:
        return profile.etsy_shipping_profile_id
    if platform == ListingPlatform.ebay:
        return profile.ebay_fulfillment_policy_id
    return None


async def resolve_marketplace_shipping(
    session: AsyncSession,
    product: Product,
    variants: list[ProductVariant],
    platform: ListingPlatform,
    listing_profile: ListingProfile | None,
) -> MarketplaceShipping:
    """The product's ShippingProfile is the single source of "how this ships": if it is
    linked to this marketplace, its link id is the draft's shipping profile / postage
    policy. The ListingProfile's own shipping field is only a fallback for a product whose
    shipping profile has no link (or that has no shipping profile at all).

    The product-level profile is used; a product with none of its own whose active
    variants all share one profile counts as having that one, since a draft is a single
    listing covering every variant and there is only one shipping id to send."""
    profiles_by_id = await get_shipping_profiles_by_id(session)
    local = resolve_variant_shipping_profile(profiles_by_id, None, product)
    if local is None and variants:
        shared = {
            resolve_variant_shipping_profile(profiles_by_id, v, product).id
            for v in variants
            if resolve_variant_shipping_profile(profiles_by_id, v, product) is not None
        }
        if len(shared) == 1 and all(v.shipping_profile_id is not None for v in variants):
            local = profiles_by_id.get(shared.pop())

    if local is not None:
        linked = _link_id(local, platform)
        if linked is not None:
            return MarketplaceShipping(value=linked, source=SHIPPING_FROM_SHIPPING_PROFILE, shipping_profile=local)
    fallback = _listing_profile_shipping(listing_profile, platform)
    if fallback is not None:
        return MarketplaceShipping(value=fallback, source=SHIPPING_FROM_LISTING_PROFILE, shipping_profile=local)
    return MarketplaceShipping(value=None, source=None, shipping_profile=local)


def _shipping_issue(
    platform: ListingPlatform, shipping: MarketplaceShipping, listing_profile: ListingProfile | None
) -> ReadinessIssue | None:
    """Three distinct situations, three distinct messages: a shipping profile that is not
    linked to this marketplace is a different fix (link it) from having no shipping profile
    at all (assign one), and both differ from the old single "set it on the listing
    profile" — which is now only the fallback."""
    if shipping.value is not None:
        return None
    field_name, label = _SHIPPING_FIELD[platform]
    marketplace = "Etsy" if platform == ListingPlatform.etsy else "eBay"
    thing = "shipping profile" if platform == ListingPlatform.etsy else "postage policy"
    fallback_hint = (
        f", or set a fallback {thing} on the '{listing_profile.name}' listing profile (Settings › Integrations)"
        if listing_profile is not None
        else ""
    )
    if shipping.shipping_profile is not None:
        return ReadinessIssue(
            field=field_name,
            severity=BLOCKER,
            message=(
                f"The product's shipping profile '{shipping.shipping_profile.name}' is not linked to {marketplace}, "
                f"and {marketplace} requires a {thing} for a physical listing."
            ),
            fix_hint=(
                f"Link '{shipping.shipping_profile.name}' to its {label} in Settings › Shipping profiles{fallback_hint}."
            ),
        )
    return ReadinessIssue(
        field=field_name,
        severity=BLOCKER,
        message=f"This product has no shipping profile, and {marketplace} requires a {thing} for a physical listing.",
        fix_hint=f"Assign the product a shipping profile that is linked to {marketplace} (Pricing tab){fallback_hint}.",
    )


def _profile_issues(platform: ListingPlatform, profile: ListingProfile | None) -> list[ReadinessIssue]:
    if profile is None:
        return [
            ReadinessIssue(
                field="listing_profile",
                severity=BLOCKER,
                message="No listing profile applies to this product.",
                fix_hint="Create one in Settings › Integrations, or pick one for this product.",
            )
        ]

    required = _ETSY_REQUIRED if platform == ListingPlatform.etsy else _EBAY_REQUIRED
    issues = []
    for attribute, label, message in required:
        if getattr(profile, attribute, None) is None:
            issues.append(
                ReadinessIssue(
                    field=attribute,
                    severity=BLOCKER,
                    message=message,
                    fix_hint=f"Set {label} on the '{profile.name}' profile.",
                )
            )
    # is_supply is documented as optional, but who_made and when_made both state they
    # require it. Treated as required-in-practice and sent always; flagged only as a
    # warning because that reading is inferred rather than confirmed against a live call.
    if platform == ListingPlatform.etsy and profile.etsy_is_supply is None:
        issues.append(
            ReadinessIssue(
                field="etsy_is_supply",
                severity=WARNING,
                message="Etsy's who_made/when_made documentation says is_supply is needed too.",
                fix_hint=f"Set 'Is a supply' on the '{profile.name}' profile.",
            )
        )
    return issues


async def evaluate(
    session: AsyncSession, product_id: int, platform: ListingPlatform
) -> DraftReadiness | None:
    """Returns None when the product doesn't exist, so the caller can 404 with better
    wording than this could produce."""
    product = await session.get(Product, product_id)
    if product is None:
        return None

    variants = list(
        (
            await session.execute(
                select(ProductVariant).where(
                    ProductVariant.product_id == product_id, ProductVariant.is_active.is_(True)
                )
            )
        ).scalars()
    )
    image_count = (
        await session.execute(
            select(func.count(ProductAsset.id)).where(
                ProductAsset.product_id == product_id,
                ProductAsset.asset_type.in_([AssetType.main_image, AssetType.listing_image]),
            )
        )
    ).scalar_one()

    settings = await listing_copy.get_settings(session, product_id, platform)
    copy = listing_copy.resolve_copy(product, settings)
    profile = await listing_profiles.resolve_profile(session, product_id, platform)

    issues: list[ReadinessIssue] = list(_profile_issues(platform, profile))
    shipping = await resolve_marketplace_shipping(session, product, variants, platform, profile)
    shipping_issue = _shipping_issue(platform, shipping, profile)
    if shipping_issue is not None:
        issues.append(shipping_issue)

    if not copy.description or not copy.description.strip():
        issues.append(
            ReadinessIssue(
                field="description",
                severity=BLOCKER,
                message="Etsy requires a description and this product has none."
                if platform == ListingPlatform.etsy
                else "A listing needs a description and this product has none.",
                fix_hint="Add one on the product, or backfill it from Etsy in Settings.",
            )
        )

    # Price and quantity, per unit. A unit with no price is excluded from the draft rather
    # than failing the whole push, matching how listing_sync already treats a product as a
    # set of independently-checkable units.
    units = variants or [None]
    priced = 0
    unpriced_names: list[str] = []
    for variant in units:
        price = None
        if variant is not None:
            price = variant.sale_price if variant.sale_price is not None else product.sale_price
        else:
            price = product.sale_price
        if price is None:
            unpriced_names.append(variant.variant_name if variant is not None else "(product)")
        else:
            priced += 1

    if priced == 0:
        issues.append(
            ReadinessIssue(
                field="price",
                severity=BLOCKER,
                message="Nothing here has a price, and a listing needs one.",
                fix_hint="Set a sale price on the product or its variants.",
            )
        )
    elif unpriced_names:
        shown = ", ".join(unpriced_names[:3])
        issues.append(
            ReadinessIssue(
                field="price",
                severity=WARNING,
                message=(
                    f"{len(unpriced_names)} variation(s) have no price and would be left out "
                    f"of the listing: {shown}."
                ),
                fix_hint="Set a sale price on those variants to include them.",
            )
        )

    if image_count == 0:
        issues.append(
            ReadinessIssue(
                field="images",
                severity=WARNING,
                message="No image attached. The draft can be created, but Etsy won't let you "
                "publish it without one."
                if platform == ListingPlatform.etsy
                else "No image attached.",
                fix_hint="Add one on the product, or backfill it from Etsy in Settings.",
            )
        )

    # Conformance against this platform's limits. Only blockers matter here — warnings are
    # already the compatibility panel's job, and repeating them would make the same
    # problem appear twice in two places with different wording.
    table = await load_limit_table(session)
    effective = resolve_effective_limits({platform}, table)
    product_violations, unit_violations = check_product(
        product, variants, effective, {platform}, image_count=image_count, table=table
    )
    for violation in product_violations + [v for unit in unit_violations for v in unit.violations]:
        if violation.severity is Severity.blocker:
            issues.append(
                ReadinessIssue(
                    field=violation.field.value,
                    severity=BLOCKER,
                    message=violation.message,
                    fix_hint="See the compatibility panel in Settings › Integrations.",
                )
            )

    # Title conformance is checked against the resolved listing title, which may differ
    # from the product name the compatibility scan looked at.
    title_violation = None
    if copy.title_source != "product_name":
        title_violation = check_length(LimitField.title_max_length, copy.title, effective)
    if title_violation is not None and title_violation.severity is Severity.blocker:
        issues.append(
            ReadinessIssue(field="listing_title", severity=BLOCKER, message=title_violation.message)
        )

    return DraftReadiness(
        product_id=product_id,
        platform=platform,
        can_create=not any(i.severity == BLOCKER for i in issues),
        profile_id=profile.id if profile else None,
        profile_name=profile.name if profile else None,
        title=copy.title,
        title_source=copy.title_source,
        description_chars=len(copy.description or ""),
        unit_count=len(units),
        priced_unit_count=priced,
        image_count=image_count,
        issues=issues,
        shipping=shipping,
    )


async def expected_quantity(session: AsyncSession, product_id: int, variant_id: int | None) -> int | None:
    """The quantity a draft would carry for one unit.

    Delegates to listing_push.resolve_push_quantity rather than recomputing: that is
    already the single definition of "how many of these can we sell", including buildable
    capacity and any platform ceiling, and a second answer here would drift from it."""
    return await listing_push.resolve_push_quantity(session, product_id, variant_id)
