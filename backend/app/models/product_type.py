from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ProductType(Base):
    """The products analog of MaterialType — "Keyring", "Coaster", "Desk toy".

    Products had no grouping axis at all before this: no category enum, no lookup table,
    nothing but name/SKU/description. Two things now need one — the ABC Type tier
    (see models/abc_classification.py) and scoping a stock take to part of the catalogue —
    so it's modelled the same way material types already are, which means it inherits the
    rename/merge/delete machinery in services/reference_data.py for free.
    """

    __tablename__ = "product_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
