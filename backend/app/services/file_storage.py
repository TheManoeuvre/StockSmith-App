import io
from pathlib import Path

from PIL import Image
from slugify import slugify

from app.config import settings
from app.models.asset import AssetType

_SUBFOLDER_BY_ASSET_TYPE: dict[AssetType, str] = {
    AssetType.main_image: "images",
    AssetType.listing_image: "images",
    AssetType.step: "cad",
    AssetType.threemf: "cad",
    AssetType.gcode: "gcode",
}

_THUMBNAIL_ASSET_TYPES = {AssetType.main_image, AssetType.listing_image}
_THUMBNAIL_MAX_DIM = 320


def generate_thumbnail(data: bytes, max_dim: int = _THUMBNAIL_MAX_DIM) -> bytes:
    """Downscales image bytes to fit within max_dim x max_dim (aspect preserved) and
    re-encodes as JPEG — the list/hero UI never displays images larger than ~192px, so
    serving multi-MB originals for those is pure waste. Raises PIL's UnidentifiedImageError
    for non-image data; callers only invoke this for known image asset types."""
    image = Image.open(io.BytesIO(data))
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    image.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


def _thumbnail_filename(original_filename: str) -> str:
    return f"thumb-{Path(original_filename).stem}.jpg"


def thumbnail_path_for(relative_path: str) -> Path:
    """Given an original asset's stored relative_path, derive where its thumbnail
    (if any) lives on disk — same folder, filename per `_thumbnail_filename`."""
    original = Path(relative_path)
    return original.parent / _thumbnail_filename(original.name)


def asset_root() -> Path:
    return Path(settings.asset_root)


def product_folder_name(product_id: int, product_name: str) -> str:
    return f"{product_id:04d}-{slugify(product_name)}"


def resolve_product_folder(product_id: int, product_name: str) -> Path:
    return asset_root() / "products" / product_folder_name(product_id, product_name)


def material_folder_name(material_id: int, material_name: str) -> str:
    return f"{material_id:04d}-{slugify(material_name)}"


def resolve_material_folder(material_id: int, material_name: str) -> Path:
    return asset_root() / "materials" / material_folder_name(material_id, material_name)


def _unique_filename(folder: Path, filename: str) -> str:
    if not (folder / filename).exists():
        return filename
    stem, _, suffix = filename.rpartition(".")
    stem, suffix = (stem, f".{suffix}") if stem else (filename, "")
    n = 2
    while (folder / f"{stem}-{n}{suffix}").exists():
        n += 1
    return f"{stem}-{n}{suffix}"


def save_upload(
    product_id: int, product_name: str, asset_type: AssetType, original_filename: str, data: bytes
) -> tuple[str, str]:
    """Writes the upload to disk, returns (relative_path, filename_used)."""
    subfolder = _SUBFOLDER_BY_ASSET_TYPE[asset_type]
    folder = resolve_product_folder(product_id, product_name) / subfolder
    folder.mkdir(parents=True, exist_ok=True)

    filename = _unique_filename(folder, original_filename)
    (folder / filename).write_bytes(data)

    if asset_type in _THUMBNAIL_ASSET_TYPES:
        (folder / _thumbnail_filename(filename)).write_bytes(generate_thumbnail(data))

    relative_path = Path("products") / product_folder_name(product_id, product_name) / subfolder / filename
    return relative_path.as_posix(), filename


def save_material_image(material_id: int, material_name: str, original_filename: str, data: bytes) -> tuple[str, str]:
    """Writes a material's single image to disk as a fixed `main.<ext>` filename (unlike
    products, which can have several files per type) — caller is responsible for deleting
    any previous image file first if replacing. Returns (relative_path, filename_used)."""
    folder = resolve_material_folder(material_id, material_name)
    folder.mkdir(parents=True, exist_ok=True)

    suffix = Path(original_filename).suffix or ""
    filename = f"main{suffix}"
    (folder / filename).write_bytes(data)
    (folder / _thumbnail_filename(filename)).write_bytes(generate_thumbnail(data))

    relative_path = Path("materials") / material_folder_name(material_id, material_name) / filename
    return relative_path.as_posix(), filename


def save_platform_icon(platform: str, data: bytes, original_filename: str) -> str:
    """Writes a platform connection's shop icon to disk as a fixed `icon.<ext>` filename
    (one icon per platform, like save_material_image) — overwrites any previous icon on
    reconnect. Returns the relative_path."""
    folder = asset_root() / "platforms" / platform
    folder.mkdir(parents=True, exist_ok=True)

    suffix = Path(original_filename).suffix or ""
    filename = f"icon{suffix}"
    (folder / filename).write_bytes(data)

    relative_path = Path("platforms") / platform / filename
    return relative_path.as_posix()


def resolve_asset_path(relative_path: str) -> Path:
    return asset_root() / relative_path


def delete_asset_file(relative_path: str) -> None:
    path = resolve_asset_path(relative_path)
    path.unlink(missing_ok=True)
    thumb_path = resolve_asset_path(thumbnail_path_for(relative_path).as_posix())
    thumb_path.unlink(missing_ok=True)
