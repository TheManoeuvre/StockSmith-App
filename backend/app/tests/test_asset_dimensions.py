"""image_dimensions underpins the Assets tab's "1600x1600" column: image asset types get
their pixel size recorded at upload/import, CAD/gcode uploads stay null."""

import io

from PIL import Image

from app.schemas.asset import AssetRead
from app.services.file_storage import image_dimensions


def _png_bytes(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_reads_dimensions_from_image_bytes():
    assert image_dimensions(_png_bytes(1600, 900)) == (1600, 900)


def test_returns_none_for_non_image_bytes():
    # A STEP / gcode upload — not a decodable image, so its row keeps null dimensions.
    assert image_dimensions(b"ISO-10303-21;\nHEADER;\n") is None
    assert image_dimensions(b"") is None


def test_asset_read_exposes_dimension_fields():
    assert {"width_px", "height_px"} <= set(AssetRead.model_fields)
