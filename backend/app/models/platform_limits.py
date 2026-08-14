from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, portable_enum
from app.models.listing import ListingPlatform
from app.services.platform_limits import LimitField


class PlatformFieldLimit(Base):
    """A user override of one shipped field limit for one platform.

    Stores *only* overrides, and ships empty. That is the whole design, and it is the
    opposite of how platform_fee_components works — deliberately.

    Fee components are seeded with researched values and `_ensure_platform_fee_components`
    returns early once any row exists, so an install that edited one component never
    receives corrected seed values again. For a rate the user owns, that is arguably fine.
    For a marketplace's field limit it would be a bug generator: shipping a corrected
    SKU cap in a new release would silently never reach anyone whose table had been
    populated.

    Sparse overrides invert it. The code default in services/platform_limits.py always
    wins unless a human explicitly overrode that one field, so corrections ship normally
    and the settings UI can show "default 50 / overridden to 45" rather than pretending
    the stored number was always the truth.

    Values are split by type rather than stored as text because the numeric ones are
    compared, not just displayed — the resolver picks the smallest, and a string column
    would sort "9" above "10".
    """

    __tablename__ = "platform_field_limits"
    __table_args__ = (
        UniqueConstraint("platform", "field_key", name="uq_platform_field_limits_platform_field"),
        CheckConstraint(
            "int_value IS NOT NULL OR text_value IS NOT NULL",
            name="ck_platform_field_limits_has_value",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[ListingPlatform] = mapped_column(
        portable_enum(ListingPlatform, name="listing_platform"), nullable=False
    )
    field_key: Mapped[LimitField] = mapped_column(portable_enum(LimitField, name="limit_field"), nullable=False)
    int_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_value: Mapped[str | None] = mapped_column(String, nullable=True)
    # Why the override exists. A number someone changed eighteen months ago with no note
    # is indistinguishable from a mistake.
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
