from pydantic import BaseModel, ConfigDict

from app.models.listing import ListingPlatform


class ListingProfileBase(BaseModel):
    name: str
    is_default: bool = False

    etsy_taxonomy_id: int | None = None
    etsy_who_made: str | None = None
    etsy_when_made: str | None = None
    etsy_is_supply: bool | None = None
    etsy_shipping_profile_id: int | None = None
    etsy_return_policy_id: int | None = None
    etsy_shop_section_id: int | None = None
    etsy_processing_min: int | None = None
    etsy_processing_max: int | None = None

    ebay_category_id: str | None = None
    ebay_condition: str | None = None
    ebay_fulfillment_policy_id: str | None = None
    ebay_payment_policy_id: str | None = None
    ebay_return_policy_id: str | None = None
    ebay_merchant_location_key: str | None = None
    ebay_marketplace_id: str | None = None


class ListingProfileCreate(ListingProfileBase):
    pass


class ListingProfileUpdate(BaseModel):
    """Every field optional so a PATCH can touch one without resending the rest.

    Note this means a field cannot be cleared back to null through this schema — an unset
    field and an explicitly-null one are indistinguishable after model_dump(
    exclude_unset=True). Clearing is rare enough here that the simpler shape wins; if it
    becomes needed it wants a sentinel, not a change of default.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    is_default: bool | None = None

    etsy_taxonomy_id: int | None = None
    etsy_who_made: str | None = None
    etsy_when_made: str | None = None
    etsy_is_supply: bool | None = None
    etsy_shipping_profile_id: int | None = None
    etsy_return_policy_id: int | None = None
    etsy_shop_section_id: int | None = None
    etsy_processing_min: int | None = None
    etsy_processing_max: int | None = None

    ebay_category_id: str | None = None
    ebay_condition: str | None = None
    ebay_fulfillment_policy_id: str | None = None
    ebay_payment_policy_id: str | None = None
    ebay_return_policy_id: str | None = None
    ebay_merchant_location_key: str | None = None
    ebay_marketplace_id: str | None = None


class ListingProfileRead(ListingProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: ListingPlatform


class ProductPlatformSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: int
    platform: ListingPlatform
    listing_profile_id: int | None
    is_target: bool | None
    listing_title: str | None
    listing_description: str | None
    # What the listing would actually carry once the fallback chain is applied, and where
    # each value came from — so the editor can show "inherited from the product name"
    # rather than presenting a fallback as though someone authored it.
    resolved_title: str
    resolved_title_source: str
    resolved_description: str | None
    resolved_description_source: str


class ProductPlatformSettingsWrite(BaseModel):
    listing_profile_id: int | None = None
    is_target: bool | None = None
    listing_title: str | None = None
    listing_description: str | None = None


class ReadinessIssue(BaseModel):
    field: str
    severity: str
    message: str
    fix_hint: str | None


class DraftReadinessReport(BaseModel):
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
    issues: list[ReadinessIssue]


class NamedOption(BaseModel):
    """An id the user should never have to see, paired with something they recognise.

    Used for every marketplace reference that is a bare number in the API and has no
    surfaced id in the seller UI — shipping profiles, return policies, eBay's policies.
    `id` is a string because the two marketplaces disagree on whether these are numeric."""

    id: str
    label: str


class TaxonomyNode(BaseModel):
    # `path` rather than `name` alone because leaf names repeat across the tree — Etsy has
    # several nodes called "Stands", and only the ancestry tells them apart.
    id: int
    name: str
    path: str
    level: int
