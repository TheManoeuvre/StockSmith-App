from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class MaterialType(Base):
    __tablename__ = "material_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    # Kitting check: when an order's items draw on two or more DIFFERENT materials of this type
    # (e.g. two box sizes, both capped to one per order by auto_apply_multiunit_kitting_override),
    # the order can't ship until someone confirms which were actually used — see
    # services/box_check.py. Materials with no type, or a type without this flag, are never checked.
    duplicates_in_kitting_check: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
