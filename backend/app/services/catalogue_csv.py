"""CSV for the parts of the catalogue a shop has most of and least wants to type: variants, and
recipes (BOMs). The products CSV (services/csv_io.py) covers products themselves; these two
follow it, so the order to import in is products, then variants, then BOMs.

Everything is referred to by name or SKU, never by database id, so a file moves between
installs and reads sensibly in a spreadsheet:

- a product by its SKU (`product_sku`); `product_name` is exported for people and ignored on
  import;
- a variant by its product's SKU plus its name;
- a material by its name, which is unique.

**Variants file** — one row per variant. A row whose product SKU + variant name already exists
updates it; otherwise it creates one. A blank optional cell leaves that field as it is.

**BOMs file** — one row per recipe line. A blank `variant_name` is a line of the product's own
recipe; a named variant is one of that variant's overrides (a different quantity, an extra
material, or — with `replaces_material` — a swap for one of the product's materials). Importing
*replaces* each recipe the file mentions, as a whole: a product or variant that appears gets
exactly the lines listed, and one that doesn't appear is left alone. A recipe with any bad row
is skipped entirely rather than half-written, and every bad row is reported.
"""

import csv
import io
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.material import Material
from app.models.product import Product, ProductMaterial
from app.models.variant import ProductVariant, ProductVariantMaterial
from app.schemas.variant import VariantBomLine
from app.services import listing_push, variant_platform_conflicts
from app.services.validation import validate_lines_against_units
from app.services.variant_boms import apply_variant_bom_overrides

VARIANTS_CSV_FIELDS = [
    "product_sku",
    "product_name",
    "variant_name",
    "sku_suffix",
    "attribute1",
    "attribute2",
    "attribute3",
    "sale_price",
    "is_active",
]

BOMS_CSV_FIELDS = ["product_sku", "product_name", "variant_name", "material", "qty_required", "replaces_material"]


def _cell(row: dict, key: str) -> str:
    return (row.get(key) or "").strip()


def _decimal(value: str, field: str) -> Decimal:
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"{field} must be a number, not {value!r}") from None
    if number < 0:
        raise ValueError(f"{field} can't be negative")
    return number


def _bool(value: str, field: str) -> bool:
    lowered = value.lower()
    if lowered in {"true", "yes", "1", "y"}:
        return True
    if lowered in {"false", "no", "0", "n"}:
        return False
    raise ValueError(f"{field} must be true or false, not {value!r}")


def _http_detail(exc: HTTPException) -> str:
    detail = exc.detail
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or detail)
    return str(detail)


def _reader(content: bytes) -> csv.DictReader:
    return csv.DictReader(io.StringIO(content.decode("utf-8-sig")))


async def _products_by_sku(session: AsyncSession) -> dict[str, Product]:
    return {p.sku: p for p in (await session.scalars(select(Product))).all() if p.sku}


# --- Variants -------------------------------------------------------------------------------


async def export_variants_csv(session: AsyncSession) -> str:
    rows = (
        await session.execute(
            select(ProductVariant, Product)
            .join(Product, Product.id == ProductVariant.product_id)
            .order_by(Product.name, ProductVariant.variant_name)
        )
    ).all()
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=VARIANTS_CSV_FIELDS)
    writer.writeheader()
    for variant, product in rows:
        writer.writerow(
            {
                "product_sku": product.sku or "",
                "product_name": product.name,
                "variant_name": variant.variant_name,
                "sku_suffix": variant.sku_suffix or "",
                "attribute1": variant.attribute1_value or "",
                "attribute2": variant.attribute2_value or "",
                "attribute3": variant.attribute3_value or "",
                "sale_price": "" if variant.sale_price is None else f"{Decimal(variant.sale_price):.2f}",
                "is_active": "true" if variant.is_active else "false",
            }
        )
    return buf.getvalue()


async def import_variants_csv(session: AsyncSession, content: bytes) -> dict:
    created = 0
    updated = 0
    failed: list[dict] = []
    products = await _products_by_sku(session)

    for line_no, row in enumerate(_reader(content), start=2):
        try:
            sku = _cell(row, "product_sku")
            name = _cell(row, "variant_name")
            if not sku:
                raise ValueError("product_sku is required")
            if not name:
                raise ValueError("variant_name is required")
            product = products.get(sku)
            if product is None:
                raise ValueError(f"no product has SKU {sku!r} — import products first")
            if product.is_bundle:
                raise ValueError(f"{product.name} is a bundle, and bundles don't have variants")

            fields: dict = {}
            for column, attr in (
                ("sku_suffix", "sku_suffix"),
                ("attribute1", "attribute1_value"),
                ("attribute2", "attribute2_value"),
                ("attribute3", "attribute3_value"),
            ):
                if _cell(row, column):
                    fields[attr] = _cell(row, column)
            if _cell(row, "sale_price"):
                fields["sale_price"] = _decimal(_cell(row, "sale_price"), "sale_price")
            if _cell(row, "is_active"):
                fields["is_active"] = _bool(_cell(row, "is_active"), "is_active")

            variant = (
                await session.execute(
                    select(ProductVariant).where(
                        ProductVariant.product_id == product.id, ProductVariant.variant_name == name
                    )
                )
            ).scalar_one_or_none()

            if variant is None:
                if fields.get("is_active", True):
                    # The same check the variant editor runs: one more active variant may take
                    # the product past a marketplace's limit.
                    await variant_platform_conflicts.require_no_platform_conflicts(
                        session,
                        product.id,
                        "ask",
                        active_variant_count=await variant_platform_conflicts.active_variant_count(session, product.id)
                        + 1,
                    )
                session.add(ProductVariant(product_id=product.id, variant_name=name, **fields))
                created += 1
            else:
                for attr, value in fields.items():
                    setattr(variant, attr, value)
                updated += 1
            await session.commit()
        except (ValueError, HTTPException, IntegrityError) as exc:
            await session.rollback()
            if isinstance(exc, HTTPException):
                message = _http_detail(exc)
            elif isinstance(exc, IntegrityError):
                message = "another variant of this product already has that name or attribute combination"
            else:
                message = str(exc)
            failed.append({"row": line_no, "error": message})
            # A rollback expires loaded rows; reload so later rows don't trip over them.
            products = await _products_by_sku(session)

    return {"created": created, "updated": updated, "failed": failed}


# --- BOMs -----------------------------------------------------------------------------------


async def export_boms_csv(session: AsyncSession) -> str:
    materials = dict((await session.execute(select(Material.id, Material.name))).all())
    products = (await session.scalars(select(Product).order_by(Product.name))).all()
    base_lines = (await session.scalars(select(ProductMaterial).order_by(ProductMaterial.id))).all()
    variants = (await session.scalars(select(ProductVariant).order_by(ProductVariant.variant_name))).all()
    overrides = (await session.scalars(select(ProductVariantMaterial).order_by(ProductVariantMaterial.id))).all()

    base_by_product: dict[int, list[ProductMaterial]] = {}
    for line in base_lines:
        base_by_product.setdefault(line.product_id, []).append(line)
    variants_by_product: dict[int, list[ProductVariant]] = {}
    for v in variants:
        variants_by_product.setdefault(v.product_id, []).append(v)
    overrides_by_variant: dict[int, list[ProductVariantMaterial]] = {}
    for o in overrides:
        overrides_by_variant.setdefault(o.variant_id, []).append(o)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=BOMS_CSV_FIELDS)
    writer.writeheader()

    def qty(value) -> str:
        return format(Decimal(value).normalize(), "f")

    for product in products:
        common = {"product_sku": product.sku or "", "product_name": product.name}
        for line in base_by_product.get(product.id, []):
            writer.writerow(
                {**common, "variant_name": "", "material": materials[line.material_id], "qty_required": qty(line.qty_required), "replaces_material": ""}
            )
        for variant in variants_by_product.get(product.id, []):
            for o in overrides_by_variant.get(variant.id, []):
                writer.writerow(
                    {
                        **common,
                        "variant_name": variant.variant_name,
                        "material": materials[o.material_id],
                        "qty_required": qty(o.qty_required),
                        "replaces_material": materials[o.replaces_material_id] if o.replaces_material_id else "",
                    }
                )
    return buf.getvalue()


async def import_boms_csv(session: AsyncSession, content: bytes) -> dict:
    products = await _products_by_sku(session)
    materials = {m.name.casefold(): m for m in (await session.scalars(select(Material))).all()}
    failed: list[dict] = []

    # (product_sku, variant_name) -> list of (row number, material id, qty, replaces id)
    groups: "OrderedDict[tuple[str, str], list[tuple[int, int, Decimal, int | None]]]" = OrderedDict()
    bad_groups: set[tuple[str, str]] = set()

    def material_id(name: str, field: str) -> int:
        material = materials.get(name.casefold())
        if material is None:
            raise ValueError(f"no material is called {name!r} — check the spelling, or import materials first")
        return material.id

    for line_no, row in enumerate(_reader(content), start=2):
        key = (_cell(row, "product_sku"), _cell(row, "variant_name"))
        groups.setdefault(key, [])
        try:
            if not key[0]:
                raise ValueError("product_sku is required")
            if key[0] not in products:
                raise ValueError(f"no product has SKU {key[0]!r} — import products first")
            if not _cell(row, "material"):
                raise ValueError("material is required")
            mat_id = material_id(_cell(row, "material"), "material")
            if not _cell(row, "qty_required"):
                raise ValueError("qty_required is required")
            amount = _decimal(_cell(row, "qty_required"), "qty_required")
            replaces = _cell(row, "replaces_material")
            replaces_id = material_id(replaces, "replaces_material") if replaces else None
            if not key[1]:
                if replaces_id is not None:
                    raise ValueError("replaces_material only applies to a variant's line — fill in variant_name")
                if amount == 0:
                    raise ValueError("a product's own recipe can't use 0 of a material — leave the line out instead")
            groups[key].append((line_no, mat_id, amount, replaces_id))
        except ValueError as exc:
            failed.append({"row": line_no, "error": str(exc)})
            bad_groups.add(key)

    def skip(key: tuple[str, str], line_no: int, reason: str) -> None:
        failed.append({"row": line_no, "error": reason})
        bad_groups.add(key)

    created = 0
    updated = 0

    # Products' own recipes first: a variant's overrides are checked against them.
    ordered = sorted(groups.items(), key=lambda item: item[0][1] != "")
    for key, lines in ordered:
        if key in bad_groups or not lines:
            continue
        product = products[key[0]]
        first_row = lines[0][0]
        label = f"{product.name} ({key[1]})" if key[1] else product.name
        try:
            if not key[1]:
                seen: set[int] = set()
                for line_no, mat_id, _, _ in lines:
                    if mat_id in seen:
                        raise ValueError(f"row {line_no}: the same material is listed twice in {label}'s recipe")
                    seen.add(mat_id)
                await validate_lines_against_units(session, [(m, q) for _, m, q, _ in lines], "qty_required")
                had_lines = await session.scalar(
                    select(func.count()).select_from(ProductMaterial).where(ProductMaterial.product_id == product.id)
                )
                await session.execute(delete(ProductMaterial).where(ProductMaterial.product_id == product.id))
                session.add_all(
                    ProductMaterial(product_id=product.id, material_id=m, qty_required=q) for _, m, q, _ in lines
                )
                await session.commit()
                # A recipe change moves how many can be made, which is what's pushed to listings.
                listing_push.enqueue_for_product(product.id)
            else:
                variant = (
                    await session.execute(
                        select(ProductVariant).where(
                            ProductVariant.product_id == product.id, ProductVariant.variant_name == key[1]
                        )
                    )
                ).scalar_one_or_none()
                if variant is None:
                    raise ValueError(f"{product.name} has no variant called {key[1]!r} — import variants first")
                had_lines = await session.scalar(
                    select(func.count())
                    .select_from(ProductVariantMaterial)
                    .where(ProductVariantMaterial.variant_id == variant.id)
                )
                await apply_variant_bom_overrides(
                    session,
                    variant,
                    [VariantBomLine(material_id=m, qty_required=q, replaces_material_id=r) for _, m, q, r in lines],
                )
            if had_lines:
                updated += 1
            else:
                created += 1
        except (ValueError, HTTPException) as exc:
            await session.rollback()
            message = _http_detail(exc) if isinstance(exc, HTTPException) else str(exc)
            skip(key, first_row, f"{label}'s recipe wasn't changed: {message}")
            products = await _products_by_sku(session)
            materials = {m.name.casefold(): m for m in (await session.scalars(select(Material))).all()}

    failed.sort(key=lambda f: f["row"])
    return {"created": created, "updated": updated, "failed": failed}
