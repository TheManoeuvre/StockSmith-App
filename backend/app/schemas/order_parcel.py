from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.listing import ListingPlatform
from app.models.order_parcel import PostageChargeSource, ReplacementParcelReason, ReplacementParcelSource


class ReplacementParcelItemInput(BaseModel):
    """Exactly one of product_id/variant_id (a product item — variant_id alone is enough,
    the product is derived) or material_id (packaging). qty is Decimal so a material can be
    fractional; a product item must be a whole number, checked here rather than in the
    service so the 422 names the field."""

    product_id: int | None = None
    variant_id: int | None = None
    material_id: int | None = None
    qty: Decimal

    @model_validator(mode="after")
    def _require_one_owner(self) -> "ReplacementParcelItemInput":
        is_product = self.product_id is not None or self.variant_id is not None
        is_material = self.material_id is not None
        if is_product == is_material:
            raise ValueError("An item needs either a product/variant or a material, not both")
        if self.qty <= 0:
            raise ValueError("qty must be positive")
        if is_product and self.qty != self.qty.to_integral_value():
            raise ValueError("A product item's qty must be a whole number")
        return self


class ReplacementParcelCreate(BaseModel):
    reason: ReplacementParcelReason = ReplacementParcelReason.other
    postage_cost: Decimal | None = None
    # Link an already-synced marketplace label instead of typing a figure — see
    # OrderPostageCharge. Only charges on the same order with sequence >= 2 qualify.
    postage_charge_id: int | None = None
    tracking_number: str | None = None
    carrier: str | None = None
    notes: str | None = None
    sent_at: datetime | None = None
    items: list[ReplacementParcelItemInput] = []

    @model_validator(mode="after")
    def _require_something(self) -> "ReplacementParcelCreate":
        # A postage-only resend (the buyer's parcel came back and went out again) is a
        # real case; an empty parcel with no postage is not.
        if not self.items and self.postage_cost is None and self.postage_charge_id is None:
            raise ValueError("A parcel needs at least one item or a postage cost")
        return self


class ReplacementParcelUpdate(BaseModel):
    """Metadata only — items are never edited in place (see OrderReplacementParcel).
    Every field is optional; only the ones sent are written. postage_charge_id=None in
    the body unlinks, which is why it's tracked via model_fields_set rather than None."""

    reason: ReplacementParcelReason | None = None
    postage_cost: Decimal | None = None
    postage_charge_id: int | None = None
    tracking_number: str | None = None
    carrier: str | None = None
    notes: str | None = None
    sent_at: datetime | None = None
    needs_review: bool | None = None


class PostageChargeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    platform: ListingPlatform
    source: PostageChargeSource
    external_id: str
    # None for a bulk marketplace label whose per-order cost isn't reported (see
    # OrderPostageCharge.amount) — `description` says so.
    amount: Decimal | None = None
    currency: str | None = None
    posted_at: datetime | None = None
    description: str | None = None
    sequence: int
    replacement_parcel_id: int | None = None


class ReplacementParcelItemRead(BaseModel):
    id: int
    product_id: int | None = None
    variant_id: int | None = None
    material_id: int | None = None
    product_name: str | None = None
    variant_name: str | None = None
    material_name: str | None = None
    material_unit: str | None = None
    qty: Decimal
    unit_cost_snapshot: Decimal | None = None
    line_cost: Decimal | None = None


class ReplacementParcelRead(BaseModel):
    id: int
    order_id: int
    reason: ReplacementParcelReason
    source: ReplacementParcelSource
    needs_review: bool
    postage_cost: Decimal | None = None
    # The figure profit actually uses: the linked marketplace label's amount when there
    # is one, else postage_cost.
    effective_postage: Decimal | None = None
    postage_charge: PostageChargeRead | None = None
    tracking_number: str | None = None
    carrier: str | None = None
    notes: str | None = None
    sent_at: datetime
    created_at: datetime
    items: list[ReplacementParcelItemRead] = []
    items_cost: Decimal | None = None
