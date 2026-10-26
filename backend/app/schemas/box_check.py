from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class BoxSelection(BaseModel):
    material_id: int
    qty: Decimal = Field(gt=0)


class BoxCheckConfirm(BaseModel):
    """The boxes a person says were (or will be) used. Anything flagged for the kitting check
    that is NOT listed ends up at zero — that is how "this one wasn't used" is expressed."""

    boxes: list[BoxSelection] = Field(min_length=1)
    # Shipped orders only: a box taken off the order goes back on the shelf unless the person
    # says it was used up anyway. Ignored before shipping, where nothing has left stock yet.
    return_to_stock: bool = True

    @field_validator("boxes")
    @classmethod
    def _unique_materials(cls, boxes: list[BoxSelection]) -> list[BoxSelection]:
        ids = [b.material_id for b in boxes]
        if len(ids) != len(set(ids)):
            raise ValueError("Each box can only be listed once")
        return boxes


class BoxCheckBoxRead(BaseModel):
    material_id: int
    material_name: str
    # True when one of the order's items resolves to this box; False for a box added by hand.
    is_candidate: bool
    # What the order currently has for this box: the effective kitting qty before shipping, the
    # quantity actually consumed once shipped.
    qty: Decimal
    unit_cost: Decimal
    free_stock: Decimal


class BoxCheckRead(BaseModel):
    # "pre_ship": order still to ship. "shipped": fully shipped, so a change is a correction that
    # moves stock. "none": cancelled — nothing to check.
    mode: Literal["pre_ship", "shipped", "none"]
    # Whether the panel has anything to say: two or more candidate boxes before shipping, any
    # box consumed once shipped.
    applies: bool
    # True while the order is blocked (pre_ship) or awaiting review (shipped).
    needs_check: bool
    confirmed_at: datetime | None
    boxes: list[BoxCheckBoxRead]


class BoxCheckChangeRead(BaseModel):
    material_id: int
    material_name: str
    current_qty: Decimal
    new_qty: Decimal
    # Positive = goes back into stock, negative = taken out of stock. Always zero before
    # shipping, and for a reduction the person chose to scrap.
    stock_effect: Decimal


class BoxCheckPreview(BaseModel):
    changes: list[BoxCheckChangeRead]
    kitting_cost_before: Decimal
    kitting_cost_after: Decimal
    # Change to the order's net profit: the reverse of the change in packaging cost.
    net_profit_delta: Decimal
    warnings: list[str] = []
