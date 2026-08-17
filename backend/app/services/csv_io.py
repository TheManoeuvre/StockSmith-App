import csv
import io
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manufacturer import Manufacturer
from app.models.material import Material, MaterialAdjustment, MaterialAdjustmentMode, MaterialUnit
from app.models.material_category import MaterialCategory
from app.models.colour import Colour
from app.models.material_type import MaterialType
from app.models.product import Product
from app.models.supplier import Supplier
from app.services.colours import find_or_create as find_or_create_colour
from app.services import material_categories
from app.services.costing import recompute_material
from app.services.kitting import apply_default_kitting_bom
from app.services.validation import validate_qty_for_unit

MATERIALS_CSV_FIELDS = [
    "name",
    "category",
    "unit",
    "current_qty",
    "reorder_threshold",
    "colour",
    "material_type_name",
    "barcode",
    "manufacturer_name",
    "default_supplier_name",
    "typical_reorder_qty",
    "is_active",
    "product_url",
]

PRODUCTS_CSV_FIELDS = ["name", "sku", "description"]


def _parse_bool(value: str | None, default: bool = True) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in ("false", "0", "no")


async def _find_or_create_by_name(session: AsyncSession, model, name: str):
    result = await session.execute(select(model).where(model.name == name))
    row = result.scalar_one_or_none()
    if row is None:
        row = model(name=name)
        session.add(row)
        await session.flush()
    return row


async def export_materials_csv(session: AsyncSession) -> str:
    result = await session.execute(
        select(Material).order_by(Material.name)
    )
    materials = list(result.scalars())
    manufacturer_ids = {m.manufacturer_id for m in materials if m.manufacturer_id is not None}
    manufacturer_names: dict[int, str] = {}
    if manufacturer_ids:
        rows = await session.execute(select(Manufacturer).where(Manufacturer.id.in_(manufacturer_ids)))
        manufacturer_names = {m.id: m.name for m in rows.scalars()}

    supplier_ids = {m.default_supplier_id for m in materials if m.default_supplier_id is not None}
    supplier_names: dict[int, str] = {}
    if supplier_ids:
        rows = await session.execute(select(Supplier).where(Supplier.id.in_(supplier_ids)))
        supplier_names = {s.id: s.name for s in rows.scalars()}

    # The CSV keeps a `colour` column holding the NAME, not the id — ids aren't portable
    # between machines and this file is meant to be human-editable, the same reasoning behind
    # manufacturer_name and material_type_name.
    colour_ids = {m.colour_id for m in materials if m.colour_id is not None}
    colour_names: dict[int, str] = {}
    if colour_ids:
        rows = await session.execute(select(Colour).where(Colour.id.in_(colour_ids)))
        colour_names = {c.id: c.name for c in rows.scalars()}

    # Names, not ids, for the same reason as colour above — and via a lookup map rather than
    # m.category_ref, because the query above loads no relationships.
    category_ids = {m.category_id for m in materials if m.category_id is not None}
    category_names: dict[int, str] = {}
    if category_ids:
        rows = await session.execute(select(MaterialCategory).where(MaterialCategory.id.in_(category_ids)))
        category_names = {c.id: c.name for c in rows.scalars()}

    material_type_ids = {m.material_type_id for m in materials if m.material_type_id is not None}
    material_type_names: dict[int, str] = {}
    if material_type_ids:
        rows = await session.execute(select(MaterialType).where(MaterialType.id.in_(material_type_ids)))
        material_type_names = {mt.id: mt.name for mt in rows.scalars()}

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=MATERIALS_CSV_FIELDS)
    writer.writeheader()
    for m in materials:
        writer.writerow(
            {
                "name": m.name,
                "category": (category_names.get(m.category_id) if m.category_id else None)
                or (m.category.value if m.category is not None else ""),
                "unit": m.unit.value,
                "current_qty": str(m.current_qty),
                "reorder_threshold": str(m.reorder_threshold),
                "colour": (colour_names.get(m.colour_id) if m.colour_id else None) or m.colour or "",
                "material_type_name": material_type_names.get(m.material_type_id, "") if m.material_type_id else "",
                "barcode": m.barcode or "",
                "manufacturer_name": manufacturer_names.get(m.manufacturer_id, "") if m.manufacturer_id else "",
                "default_supplier_name": supplier_names.get(m.default_supplier_id, "") if m.default_supplier_id else "",
                "typical_reorder_qty": str(m.typical_reorder_qty) if m.typical_reorder_qty is not None else "",
                "is_active": "true" if m.is_active else "false",
                "product_url": m.product_url or "",
            }
        )
    return buf.getvalue()


async def import_materials_csv(session: AsyncSession, content: bytes) -> dict:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    created = 0
    updated = 0
    failed: list[dict] = []

    for i, row in enumerate(reader, start=2):  # row 1 is the header
        try:
            name = (row.get("name") or "").strip()
            if not name:
                raise ValueError("name is required")
            # Matched case-insensitively, unlike the enum lookup this replaces. That used to
            # fail the row loudly on "Filament", which was a fine way to make someone fix the
            # file. Now that the set of categories is open, an exact match wouldn't fail — it
            # would succeed by creating a *second* category beside the first, splitting the
            # user's materials across two rows that mean one thing.
            category_row = await material_categories.find_or_create(session, row.get("category"))
            if category_row is None:
                raise ValueError("category is required")
            category = material_categories.legacy_value_for(category_row.name)
            unit = MaterialUnit(row["unit"].strip())
            reorder_threshold = Decimal(row.get("reorder_threshold") or "0")
            target_qty = Decimal(row["current_qty"]) if row.get("current_qty") else None
            typical_reorder_qty = (
                Decimal(row["typical_reorder_qty"]) if row.get("typical_reorder_qty") else None
            )
            is_active = _parse_bool(row.get("is_active"), default=True)

            validate_qty_for_unit(reorder_threshold, unit, "reorder_threshold")
            if typical_reorder_qty is not None:
                validate_qty_for_unit(typical_reorder_qty, unit, "typical_reorder_qty")
            if target_qty is not None:
                validate_qty_for_unit(target_qty, unit, "current_qty")

            manufacturer_id = None
            manufacturer_name = (row.get("manufacturer_name") or "").strip()
            if manufacturer_name:
                manufacturer_id = (await _find_or_create_by_name(session, Manufacturer, manufacturer_name)).id

            default_supplier_id = None
            default_supplier_name = (row.get("default_supplier_name") or "").strip()
            if default_supplier_name:
                default_supplier_id = (await _find_or_create_by_name(session, Supplier, default_supplier_name)).id

            material_type_id = None
            material_type_name = (row.get("material_type_name") or "").strip()
            if material_type_name:
                material_type_id = (await _find_or_create_by_name(session, MaterialType, material_type_name)).id

            colour_row = await find_or_create_colour(session, row.get("colour"))

            existing = (
                await session.execute(select(Material).where(func.trim(Material.name) == name))
            ).scalar_one_or_none()

            if existing is None:
                material = Material(
                    name=name,
                    category=category,
                    category_id=category_row.id,
                    unit=unit,
                    reorder_threshold=reorder_threshold,
                    colour=colour_row.name if colour_row else None,
                    colour_id=colour_row.id if colour_row else None,
                    material_type_id=material_type_id,
                    barcode=row.get("barcode") or None,
                    manufacturer_id=manufacturer_id,
                    default_supplier_id=default_supplier_id,
                    typical_reorder_qty=typical_reorder_qty,
                    is_active=is_active,
                    product_url=row.get("product_url") or None,
                )
                session.add(material)
                await session.flush()
                if target_qty and target_qty > 0:
                    session.add(
                        MaterialAdjustment(
                            material_id=material.id,
                            mode=MaterialAdjustmentMode.set,
                            qty_delta=target_qty,
                            target_qty=target_qty,
                            reason="CSV import",
                        )
                    )
                    await recompute_material(session, material.id)
                created += 1
            else:
                existing.category = category
                existing.category_id = category_row.id
                existing.unit = unit
                existing.reorder_threshold = reorder_threshold
                existing.colour = colour_row.name if colour_row else None
                existing.colour_id = colour_row.id if colour_row else None
                existing.material_type_id = material_type_id
                existing.barcode = row.get("barcode") or None
                existing.manufacturer_id = manufacturer_id
                existing.default_supplier_id = default_supplier_id
                existing.typical_reorder_qty = typical_reorder_qty
                existing.is_active = is_active
                existing.product_url = row.get("product_url") or None
                if target_qty is not None and target_qty != Decimal(existing.current_qty):
                    delta = target_qty - Decimal(existing.current_qty)
                    session.add(
                        MaterialAdjustment(
                            material_id=existing.id,
                            mode=MaterialAdjustmentMode.set,
                            qty_delta=delta,
                            target_qty=target_qty,
                            reason="CSV import",
                        )
                    )
                    await recompute_material(session, existing.id)
                updated += 1

            await session.commit()
        except (ValueError, KeyError, InvalidOperation) as e:
            # Roll back just this row — a per-row commit boundary means earlier
            # successful rows in the same import are unaffected.
            await session.rollback()
            failed.append({"row": i, "error": str(e)})
        except HTTPException as e:
            # validate_qty_for_unit raises HTTPException (it's shared with request
            # handlers) — caught separately so a whole-number violation fails just
            # this row instead of aborting the rest of the import.
            await session.rollback()
            failed.append({"row": i, "error": str(e.detail)})

    return {"created": created, "updated": updated, "failed": failed}


async def export_products_csv(session: AsyncSession) -> str:
    result = await session.execute(select(Product).order_by(Product.name))
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=PRODUCTS_CSV_FIELDS)
    writer.writeheader()
    for p in result.scalars():
        writer.writerow({"name": p.name, "sku": p.sku or "", "description": p.description or ""})
    return buf.getvalue()


async def import_products_csv(session: AsyncSession, content: bytes) -> dict:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    created = 0
    updated = 0
    failed: list[dict] = []

    for i, row in enumerate(reader, start=2):
        try:
            name = (row.get("name") or "").strip()
            if not name:
                raise ValueError("name is required")
            sku = (row.get("sku") or "").strip() or None
            description = row.get("description") or None

            existing = None
            if sku:
                existing = (await session.execute(select(Product).where(Product.sku == sku))).scalar_one_or_none()

            if existing is None:
                product = Product(name=name, sku=sku, description=description)
                session.add(product)
                await session.flush()
                if not sku:
                    product.sku = f"SKU-{product.id:04d}"
                await apply_default_kitting_bom(session, product.id)
                created += 1
            else:
                existing.name = name
                existing.description = description
                updated += 1

            await session.commit()
        except (ValueError, KeyError) as e:
            await session.rollback()
            failed.append({"row": i, "error": str(e)})

    return {"created": created, "updated": updated, "failed": failed}
