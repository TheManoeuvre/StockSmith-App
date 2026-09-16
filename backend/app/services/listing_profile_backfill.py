"""Deriving listing profiles from the Etsy listings that already exist.

The metadata a draft needs — taxonomy, who made it, shipping profile, processing times —
was never stored locally, so §Stage 3 would otherwise start with an empty form and a list
of fields nobody can look up without opening Etsy in another window. But every one of
those values is already on the listings these products are matched to, and comes back on
the same crawl the backfill already performs.

The useful observation is that they *repeat*. A shop's catalogue spans a handful of
genuine combinations, not one per product — which is exactly the premise profiles are
built on. So this groups matched listings by their metadata signature and proposes one
profile per distinct combination, with the products that would use it. Setting profiles up
becomes reviewing three suggestions rather than filling in nine fields.

Nothing is created without being asked for, and a proposal missing a required field is
still shown: seeing that eleven products share an incomplete combination is how you learn
which single field to go and set.

Shipping is proposed separately. Etsy's shipping_profile_id used to be part of the
grouping signature, and it shattered proposals: two products that differ only in postage
are the same *kind* of listing, and the draft now takes its shipping id from the product's
own ShippingProfile (linked to Etsy) rather than from the listing profile. So each distinct
Etsy shipping profile seen on the matched listings that no local ShippingProfile is linked
to becomes its own proposal — create a local profile for it (title and buyer price from
Etsy) or link an existing one — shown as a separate group in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing, ListingPlatform
from app.models.listing_profile import ListingProfile
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.services import listing_profiles

# The fields that define a distinct profile. Deliberately not every field Etsy returns:
# shop_section_id and the processing window vary per listing without changing what kind of
# thing is being sold, so including them would shatter three real profiles into fifteen.
_SIGNATURE_FIELDS = (
    "taxonomy_id",
    "who_made",
    "when_made",
    "is_supply",
    "return_policy_id",
)


@dataclass(frozen=True)
class ProfileSignature:
    taxonomy_id: int | None
    who_made: str | None
    when_made: str | None
    is_supply: bool | None
    return_policy_id: int | None

    @property
    def is_complete(self) -> bool:
        """Whether a draft could actually be created from this — the same required set
        draft_readiness enforces, minus the parts that aren't marketplace metadata. Shipping
        is not part of it any more: it comes from the product's linked ShippingProfile."""
        return all(value is not None for value in (self.taxonomy_id, self.who_made, self.when_made))


@dataclass
class ProfileProposal:
    signature: ProfileSignature
    suggested_name: str
    product_ids: list[int] = field(default_factory=list)
    product_names: list[str] = field(default_factory=list)
    # Carried through from a representative listing rather than the signature, since these
    # are worth adopting but too variable to group on.
    processing_min: int | None = None
    processing_max: int | None = None
    shop_section_id: int | None = None

    @property
    def is_complete(self) -> bool:
        return self.signature.is_complete


@dataclass
class ShippingProfileProposal:
    """One Etsy shipping profile in use on matched listings that no local ShippingProfile is
    linked to yet. Accepting it either creates a local profile (named and priced from Etsy)
    or links an existing one; either way the products on those listings that have no
    shipping profile of their own are pointed at it."""

    etsy_shipping_profile_id: int
    # From Etsy's own profile list when the caller supplies it; falls back to the id.
    title: str
    domestic_price: Decimal | None
    is_calculated: bool
    product_ids: list[int] = field(default_factory=list)
    product_names: list[str] = field(default_factory=list)


@dataclass
class ProfileBackfillResult:
    profiles_created: int
    products_assigned: int
    shipping_profiles_created: int = 0
    shipping_profiles_linked: int = 0
    shipping_products_assigned: int = 0


def _signature(listing: dict) -> ProfileSignature:
    return ProfileSignature(
        taxonomy_id=listing.get("taxonomy_id"),
        who_made=listing.get("who_made"),
        when_made=listing.get("when_made"),
        is_supply=listing.get("is_supply"),
        return_policy_id=listing.get("return_policy_id"),
    )


def _suggest_name(signature: ProfileSignature, product_names: list[str]) -> str:
    """A name a human will recognise. Etsy's taxonomy id is a number nobody knows by sight,
    so lead with the making details, which are the part that actually distinguishes one
    profile from another in a single shop."""
    made = {"i_did": "Handmade", "someone_else": "Made by someone else", "collective": "Collective"}.get(
        signature.who_made or "", "Listing"
    )
    if signature.is_supply:
        made = f"{made} supply"
    if signature.taxonomy_id is not None:
        return f"{made} (category {signature.taxonomy_id})"
    return made


async def propose_profiles(session: AsyncSession, listings: list[dict]) -> list[ProfileProposal]:
    """Groups matched Etsy listings into distinct metadata combinations.

    Takes the crawl rather than fetching it so the grouping is testable against a captured
    payload with no marketplace involved."""
    by_id = {str(listing.get("listing_id")): listing for listing in listings}

    matched = (
        await session.execute(
            select(Listing.product_id, Listing.external_listing_id).where(
                Listing.platform == ListingPlatform.etsy, Listing.external_listing_id.is_not(None)
            )
        )
    ).all()
    product_to_listing = {product_id: listing_id for product_id, listing_id in matched}

    products = {
        product.id: product
        for product in (
            await session.execute(select(Product).where(Product.is_active.is_(True)))
        ).scalars()
    }

    grouped: dict[ProfileSignature, ProfileProposal] = {}
    for product_id, listing_id in product_to_listing.items():
        product = products.get(product_id)
        listing = by_id.get(listing_id)
        if product is None or listing is None:
            continue

        signature = _signature(listing)
        # A listing carrying none of it tells us nothing and would group every such
        # product under an empty proposal that can't be used for anything.
        if all(getattr(signature, name) is None for name in _SIGNATURE_FIELDS):
            continue

        proposal = grouped.get(signature)
        if proposal is None:
            proposal = ProfileProposal(
                signature=signature,
                suggested_name=_suggest_name(signature, []),
                processing_min=listing.get("processing_min"),
                processing_max=listing.get("processing_max"),
                shop_section_id=listing.get("shop_section_id"),
            )
            grouped[signature] = proposal
        proposal.product_ids.append(product_id)
        proposal.product_names.append(product.name)

    # Most-used first: the biggest group is the one that should become the default, and
    # putting it at the top makes that the obvious choice rather than a decision.
    return sorted(grouped.values(), key=lambda p: (-len(p.product_ids), p.suggested_name))


async def apply_proposals(
    session: AsyncSession,
    listings: list[dict],
    selections: dict[int, str],
    *,
    assign_products: bool = True,
) -> ProfileBackfillResult:
    """Creates the chosen proposals as profiles and points their products at them.

    `selections` maps a proposal's index in propose_profiles' output to the name to create
    it under, so the user can rename a suggestion before accepting it. Re-derived from a
    fresh crawl rather than trusting a previewed payload, for the same reason the value
    backfill does.

    The first profile created becomes the platform default — otherwise every product would
    report "no listing profile applies" while one plainly exists.
    """
    proposals = await propose_profiles(session, listings)
    created = 0
    assigned = 0

    for index, name in selections.items():
        if index < 0 or index >= len(proposals):
            continue
        proposal = proposals[index]
        signature = proposal.signature

        profile = ListingProfile(
            platform=ListingPlatform.etsy,
            name=name.strip() or proposal.suggested_name,
            is_default=False,
            etsy_taxonomy_id=signature.taxonomy_id,
            etsy_who_made=signature.who_made,
            etsy_when_made=signature.when_made,
            etsy_is_supply=signature.is_supply,
            etsy_return_policy_id=signature.return_policy_id,
            etsy_shop_section_id=proposal.shop_section_id,
            etsy_processing_min=proposal.processing_min,
            etsy_processing_max=proposal.processing_max,
        )
        session.add(profile)
        await session.flush()
        created += 1

        if await listing_profiles.get_default_profile(session, ListingPlatform.etsy) is None:
            await listing_profiles.promote_to_default(session, ListingPlatform.etsy, profile)

        if assign_products:
            for product_id in proposal.product_ids:
                settings = await listing_profiles.get_or_create_settings(
                    session, product_id, ListingPlatform.etsy
                )
                settings.listing_profile_id = profile.id
                assigned += 1

    await session.commit()
    return ProfileBackfillResult(profiles_created=created, products_assigned=assigned)


# --- Shipping profiles -----------------------------------------------------------------------


async def _matched_products(session: AsyncSession, listings: list[dict]) -> list[tuple[Product, dict]]:
    """Every active product with an Etsy listing in the crawl, paired with that listing."""
    by_id = {str(listing.get("listing_id")): listing for listing in listings}
    matched = (
        await session.execute(
            select(Listing.product_id, Listing.external_listing_id).where(
                Listing.platform == ListingPlatform.etsy, Listing.external_listing_id.is_not(None)
            )
        )
    ).all()
    products = {
        product.id: product
        for product in (await session.execute(select(Product).where(Product.is_active.is_(True)))).scalars()
    }
    pairs = []
    for product_id, listing_id in dict(matched).items():
        product = products.get(product_id)
        listing = by_id.get(listing_id)
        if product is not None and listing is not None:
            pairs.append((product, listing))
    return pairs


async def propose_shipping_profiles(
    session: AsyncSession, listings: list[dict], etsy_profiles: list[dict] | None = None
) -> list[ShippingProfileProposal]:
    """Each distinct Etsy shipping profile on the matched listings that no local
    ShippingProfile is linked to. `etsy_profiles` is EtsyAdapter.fetch_shipping_profiles'
    output, used for the title and domestic price; without it the proposal still stands,
    named by id."""
    linked = {
        row[0]
        for row in (
            await session.execute(
                select(ShippingProfile.etsy_shipping_profile_id).where(
                    ShippingProfile.etsy_shipping_profile_id.is_not(None)
                )
            )
        ).all()
    }
    details = {int(p["id"]): p for p in (etsy_profiles or []) if p.get("id") is not None}

    grouped: dict[int, ShippingProfileProposal] = {}
    for product, listing in await _matched_products(session, listings):
        etsy_id = listing.get("shipping_profile_id")
        if etsy_id is None or int(etsy_id) in linked:
            continue
        etsy_id = int(etsy_id)
        proposal = grouped.get(etsy_id)
        if proposal is None:
            detail = details.get(etsy_id)
            proposal = ShippingProfileProposal(
                etsy_shipping_profile_id=etsy_id,
                title=(detail or {}).get("title") or f"Etsy shipping profile {etsy_id}",
                domestic_price=(detail or {}).get("domestic_price"),
                is_calculated=(detail or {}).get("profile_type") == "calculated",
            )
            grouped[etsy_id] = proposal
        proposal.product_ids.append(product.id)
        proposal.product_names.append(product.name)

    return sorted(grouped.values(), key=lambda p: (-len(p.product_ids), p.title))


@dataclass
class ShippingSelection:
    """How to accept one shipping proposal: link an existing local profile (by id), or
    create a new one under `name` (the Etsy title unless renamed)."""

    etsy_shipping_profile_id: int
    name: str | None = None
    link_shipping_profile_id: int | None = None


async def apply_shipping_proposals(
    session: AsyncSession,
    listings: list[dict],
    selections: list[ShippingSelection],
    *,
    etsy_profiles: list[dict] | None = None,
    assign_products: bool = True,
) -> ProfileBackfillResult:
    """Creates or links the accepted shipping proposals and points their products at them.

    A new local profile takes Etsy's title and, for a manual profile, Etsy's domestic buyer
    price as both price_etsy and the default price — a freshly-created profile with £0
    postage would silently flatter every margin until someone noticed. Costs are left at
    zero: Etsy knows nothing about what the carrier charges the seller.

    Products are assigned only when they have no shipping profile of their own — the same
    never-overwrite rule adoption follows."""
    proposals = {p.etsy_shipping_profile_id: p for p in await propose_shipping_profiles(session, listings, etsy_profiles)}
    result = ProfileBackfillResult(profiles_created=0, products_assigned=0)

    for selection in selections:
        proposal = proposals.get(selection.etsy_shipping_profile_id)
        if proposal is None:
            continue
        if selection.link_shipping_profile_id is not None:
            profile = await session.get(ShippingProfile, selection.link_shipping_profile_id)
            if profile is None or profile.etsy_shipping_profile_id is not None:
                continue
            profile.etsy_shipping_profile_id = proposal.etsy_shipping_profile_id
            if not proposal.is_calculated and proposal.domestic_price is not None:
                profile.price_etsy = proposal.domestic_price
            result.shipping_profiles_linked += 1
        else:
            price = proposal.domestic_price if not proposal.is_calculated else None
            profile = ShippingProfile(
                name=(selection.name or "").strip() or proposal.title,
                price=price if price is not None else Decimal(0),
                price_etsy=price,
                etsy_shipping_profile_id=proposal.etsy_shipping_profile_id,
            )
            session.add(profile)
            result.shipping_profiles_created += 1
        await session.flush()

        if assign_products:
            for product_id in proposal.product_ids:
                product = await session.get(Product, product_id)
                if product is not None and product.shipping_profile_id is None:
                    product.shipping_profile_id = profile.id
                    result.shipping_products_assigned += 1

    await session.commit()
    return result
