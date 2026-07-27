import csv
import io
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.manufacturer import Manufacturer
from app.models.material import Material, MaterialAdjustment, MaterialAdjustmentMode, MaterialCategory, MaterialUnit
from app.models.material_type import MaterialType
from app.models.product import Product
from app.models.supplier import Supplier
from app.services.costing import recompute_material

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
                "category": m.category.value,
                "unit": m.unit.value,
                "current_qty": str(m.current_qty),
                "reorder_threshold": str(m.reorder_threshold),
                "colour": m.colour or "",
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
            category = MaterialCategory(row["category"].strip())
            unit = MaterialUnit(row["unit"].strip())
            reorder_threshold = Decimal(row.get("reorder_threshold") or "0")
            target_qty = Decimal(row["current_qty"]) if row.get("current_qty") else None
            typical_reorder_qty = (
                Decimal(row["typical_reorder_qty"]) if row.get("typical_reorder_qty") else None
            )
            is_active = _parse_bool(row.get("is_active"), default=True)

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

            existing = (
                await session.execute(select(Material).where(func.trim(Material.name) == name))
            ).scalar_one_or_none()

            if existing is None:
                material = Material(
                    name=name,
                    category=category,
                    unit=unit,
                    reorder_threshold=reorder_threshold,
                    colour=row.get("colour") or None,
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
                existing.unit = unit
                existing.reorder_threshold = reorder_threshold
                existing.colour = row.get("colour") or None
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
