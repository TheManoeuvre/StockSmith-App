from pydantic import BaseModel

from app.services.platform_limits import LimitField


class PlatformFieldLimitRead(BaseModel):
    """One limit, showing the shipped default and any override side by side.

    Both are returned deliberately. An editor that only showed the effective number would
    make an override indistinguishable from a default, so nobody could tell whether a
    surprising value came from StockSmith or from something they changed a year ago — and
    "reset to default" would have nothing to reset to.
    """

    field: LimitField
    label: str
    kind: str  # "int" | "text"
    default_value: str | None
    override_value: str | None
    effective_value: str | None
    is_override: bool
    note: str | None


class PlatformFieldLimitWrite(BaseModel):
    # Exactly one of these carries the value, matching the storage split: numeric limits
    # are compared by the resolver, so they cannot be kept as text.
    int_value: int | None = None
    text_value: str | None = None
    note: str | None = None
