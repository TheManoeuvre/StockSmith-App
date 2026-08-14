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
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.listing import Listing, ListingPlatform
from app.models.listing_profile import ListingProfile
from app.models.product import Product
from app.services import listing_profiles

# The fields that define a distinct profile. Deliberately not every field Etsy returns:
# shop_section_id and the processing window vary per listing without changing what kind of
# thing is being sold, so including them would shatter three real profiles into fifteen.
_SIGNATURE_FIELDS = (
    "taxonomy_id",
    "who_made",
    "when_made",
    "is_supply",
    "shipping_profile_id",
    "return_policy_id",
)


@dataclass(frozen=True)
class ProfileSignature:
    taxonomy_id: int | None
    who_made: str | None
    when_made: str | None
    is_supply: bool | None
    shipping_profile_id: int | None
    return_policy_id: int | None

    @property
    def is_complete(self) -> bool:
        """Whether a draft could actually be created from this — the same required set
        draft_readiness enforces, minus the parts that aren't marketplace metadata."""
        return all(
            value is not None
            for value in (self.taxonomy_id, self.who_made, self.when_made, self.shipping_profile_id)
        )


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
class ProfileBackfillResult:
    profiles_created: int
    products_assigned: int


def _signature(listing: dict) -> ProfileSignature:
    return ProfileSignature(
        taxonomy_id=listing.get("taxonomy_id"),
        who_made=listing.get("who_made"),
        when_made=listing.get("when_made"),
        is_supply=listing.get("is_supply"),
        shipping_profile_id=listing.get("shipping_profile_id"),
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
            etsy_shipping_profile_id=signature.shipping_profile_id,
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
