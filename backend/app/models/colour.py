from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Colour(Base):
    """A material colour, promoted from free text on `materials.colour` to a real lookup row.

    Unlike manufacturer, supplier and material type — which were foreign keys from the start —
    colour was a plain string copied onto every material. So "Black", "black" and " BLACK " were
    three different colours as far as the app was concerned, the datalist offered all three, and
    correcting one meant editing every material that used it.

    `hex_code` exists because the field was labelled "Colour / hex" in the UI, so a real share of
    the values are literally `#FF00AA`. The migration keeps the original text as the name and
    fills this in when the value parses as a hex colour, rather than trying to invent a name for
    it.
    """

    __tablename__ = "colours"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    hex_code: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
