from decimal import Decimal

from pydantic import BaseModel


class VariantPriceProposal(BaseModel):
    variant_id: int
    variant_name: str
    sku: str
    proposed_price: Decimal


class ProductBackfillProposal(BaseModel):
    """What could be filled for one product. Every field is None when there is nothing to
    take — either Etsy had no value, or StockSmith already holds one and is never
    overwritten."""

    product_id: int
    product_name: str
    external_listing_id: str
    listing_title: str | None
    description: str | None
    # Length rather than the full text in the summary view: a 3,000-character description
    # in a list of 26 rows is unreadable, and the point of the preview is to decide, not
    # to proofread.
    description_chars: int
    sale_price: Decimal | None
    image_url: str | None
    variant_prices: list[VariantPriceProposal]


class EtsyBackfillPreview(BaseModel):
    products: list[ProductBackfillProposal]
    # Matched to an Etsy listing but needing nothing — a count, so the user can see the
    # scan covered them without a screen of "nothing to do" rows.
    already_complete: int
    # No confirmed Etsy match, so there is nothing to read from.
    unmatched: int


class BackfillSelection(BaseModel):
    product_id: int
    # Any of "description", "price", "image". Unknown names are ignored rather than
    # rejected, so a newer frontend asking for a field this build doesn't support
    # degrades to filling the rest.
    fields: list[str]


class EtsyBackfillRequest(BaseModel):
    items: list[BackfillSelection]


class EtsyBackfillResult(BaseModel):
    products_updated: int
    descriptions_filled: int
    prices_filled: int
    images_filled: int
    errors: list[str]


class ProfileProposalRead(BaseModel):
    """One distinct metadata combination found across the matched Etsy listings.

    `index` is its position in the proposal list, which is how the apply call refers back
    to it — the combination itself has no id until it is accepted."""

    index: int
    suggested_name: str
    is_complete: bool
    product_count: int
    product_names: list[str]
    taxonomy_id: int | None
    who_made: str | None
    when_made: str | None
    is_supply: bool | None
    return_policy_id: int | None
    processing_min: int | None
    processing_max: int | None


class ShippingProfileProposalRead(BaseModel):
    """One Etsy shipping profile in use on matched listings with no linked local
    ShippingProfile. Accept it by creating a local profile (title and Etsy buyer price
    from Etsy) or by linking an existing one."""

    etsy_shipping_profile_id: int
    title: str
    domestic_price: Decimal | None
    is_calculated: bool
    product_count: int
    product_names: list[str]


class ProfileProposalsRead(BaseModel):
    proposals: list[ProfileProposalRead]
    shipping_profiles: list[ShippingProfileProposalRead] = []


class ProfileSelection(BaseModel):
    index: int
    # The suggested name unless the user renamed it before accepting.
    name: str


class ShippingProfileSelection(BaseModel):
    etsy_shipping_profile_id: int
    # For a new local profile: its name (the Etsy title unless renamed).
    name: str | None = None
    # Or link this existing local profile instead of creating one.
    link_shipping_profile_id: int | None = None


class ApplyProfileProposalsRequest(BaseModel):
    items: list[ProfileSelection] = []
    shipping_items: list[ShippingProfileSelection] = []
    assign_products: bool = True


class ApplyProfileProposalsResult(BaseModel):
    profiles_created: int
    products_assigned: int
    shipping_profiles_created: int = 0
    shipping_profiles_linked: int = 0
    shipping_products_assigned: int = 0
