from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProductAttributeValueCode(Base):
    """The short numeric code standing in for one attribute value on one product.

    Exists so a generated SKU has a length that depends on how many attributes a product
    has, not on how long anyone's colour names are. "Sunflower Yellow" costs sixteen
    characters in a SKU and two here, which is the difference between fitting inside Etsy's
    cap and not.

    **A code is an allocation, never a position.** This table is the whole reason: if the
    code were the value's index in the product's attribute list, deleting the second colour
    would renumber every colour after it, silently changing the SKU of variants that are
    already live on a marketplace. Codes are assigned on first use, stored, and never
    reused — a value that is deleted and recreated gets a fresh code rather than inheriting
    a retired one, because the retired one may still be printed on a listing somewhere.

    Scoped per product rather than globally (the alternative considered): both marketplaces
    display variation names beside the SKU on orders and packing slips, so the SKU does not
    have to be self-describing on its own — the context is already on the page. Per-product
    scoping also keeps the number space tiny, which is what allows two digits.
    """

    __tablename__ = "product_attribute_value_codes"
    __table_args__ = (
        UniqueConstraint("product_id", "attribute_slot", "value", name="uq_attribute_value_codes_value"),
        # One code per slot per product. Without this a race could hand the same code to two
        # values, and the SKUs built from them would collide.
        UniqueConstraint("product_id", "attribute_slot", "code", name="uq_attribute_value_codes_code"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    # 1, 2 or 3 — matching Product.variant_attribute{1,2,3}_name.
    attribute_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[str] = mapped_column(String, nullable=False)
    code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
