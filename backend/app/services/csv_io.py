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

# Keyed on line_id, deviating from the names-not-ids rule the two exports above follow.
# That rule exists because those files are meant to move between machines; this one is
# scoped to a single take on a single database and round-trips within hours, so an id is
# both stable and unambiguous in a way a name isn't (two variants of one product differ
# only by a suffix). item_type/item_id ride along as a cross-check so a file edited into
# the wrong shape fails loudly rather than writing a count onto the wrong item.
# section/group/subgroup replace what was a single `category` column. The sheet is printed
# and carried around, so it has to show the same headings the screen does — one column
# could only ever express one level of a three-level arrangement. Read-only either way:
# import keys on line_id and ignores them.
STOCK_TAKE_CSV_FIELDS = [
    "line_id",
    "item_type",
    "item_id",
    "section",
    "group",
    "subgroup",
    "name",
    "unit",
    "expected_qty",
    "allocated_qty",
    "counted_qty",
    "notes",
]


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


async def _stock_take_lookups(session: AsyncSession, lines) -> tuple[dict, dict, dict]:
    from sqlalchemy.orm import selectinload

    from app.models.variant import ProductVariant

    material_ids = {line.material_id for line in lines if line.material_id}
    product_ids = {line.product_id for line in lines if line.product_id}
    variant_ids = {line.variant_id for line in lines if line.variant_id}
    materials = (
        {
            m.id: m
            for m in (
                await session.execute(
                    # group_lines reads material_type_name through a relationship — a lazy
                    # load in async raises MissingGreenlet rather than fetching.
                    select(Material)
                    .where(Material.id.in_(material_ids))
                    .options(selectinload(Material.material_type))
                )
            ).scalars()
        }
        if material_ids
        else {}
    )
    products = (
        {
            p.id: p
            for p in (
                await session.execute(
                    # product_category_name reads through a relationship, and a lazy load in
                    # async raises MissingGreenlet rather than fetching.
                    select(Product).where(Product.id.in_(product_ids)).options(selectinload(Product.product_category))
                )
            ).scalars()
        }
        if product_ids
        else {}
    )
    variants = (
        {
            v.id: v
            for v in (
                await session.execute(select(ProductVariant).where(ProductVariant.id.in_(variant_ids)))
            ).scalars()
        }
        if variant_ids
        else {}
    )
    return materials, products, variants


async def export_stock_take_csv(session: AsyncSession, stock_take_id: int) -> str:
    """The count sheet as a file — to print, or to fill in a spreadsheet away from the PC.

    counted_qty and notes come back pre-filled if counting is already under way, so the
    file round-trips rather than discarding work when re-exported.

    allocated_qty is included read-only and ignored on import. A printed sheet is exactly
    where nobody would otherwise know that five of the twelve are picked and boxed by the
    door rather than on the shelf — which is the difference between a real variance and a
    count that only looks short.
    """
    from app.models.stock_take import StockTakeLine
    from app.services.stock_takes import group_lines

    lines = list(
        (
            await session.execute(
                select(StockTakeLine)
                .where(StockTakeLine.stock_take_id == stock_take_id)
                .order_by(StockTakeLine.id)
            )
        ).scalars()
    )
    materials, products, variants = await _stock_take_lookups(session, lines)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=STOCK_TAKE_CSV_FIELDS)
    writer.writeheader()
    # Rows come out in the same order the count sheet shows them — this is the copy that
    # gets printed and walked around with, so it is the one that most needs to match the
    # shelves. Same call as the screen, so the two cannot drift apart.
    for grouped in group_lines(lines, materials, products, variants):
        line = grouped.line
        writer.writerow(
            {
                "line_id": line.id,
                "item_type": "material" if line.material_id else "product",
                "item_id": line.material_id or line.product_id,
                "section": grouped.section,
                "group": grouped.group,
                "subgroup": grouped.subgroup,
                "name": grouped.name,
                "unit": grouped.unit,
                "expected_qty": str(line.expected_qty),
                "allocated_qty": "" if line.allocated_qty_at_start is None else str(line.allocated_qty_at_start),
                "counted_qty": "" if line.counted_qty is None else str(line.counted_qty),
                "notes": line.notes or "",
            }
        )
    return buf.getvalue()


async def import_stock_take_csv(
    session: AsyncSession, stock_take_id: int, content: bytes, dry_run: bool = True, on_error: str = "skip"
) -> dict:
    """Read counts back off a filled-in sheet.

    Two calls, one endpoint, mirroring BulkBomAmendModal: the client posts with
    dry_run=True to get the preview it shows the user, then posts the same file again with
    dry_run=False once they have confirmed. Stateless — no staging table, and no trusting a
    client-supplied list of "the rows you already approved". Validation is deterministic,
    so the second pass reaches the same verdict as the first.

    Per-row and independent, following import_materials_csv: one bad row reports its own
    line number (counted from 2, so it matches what the spreadsheet shows) and leaves the
    rest alone.

    A blank counted_qty is not a zero — it means that row was not counted, so the line is
    left exactly as it was. Same rule as leaving the field blank in the app.
    """
    from app.models.stock_take import StockTake, StockTakeLine, StockTakeLineStatus, StockTakeStatus

    take = await session.get(StockTake, stock_take_id)
    if take is None:
        raise HTTPException(status_code=404, detail="Stock take not found")
    if take.status is StockTakeStatus.closed:
        raise HTTPException(status_code=400, detail="This stock take is closed")

    lines_by_id = {
        line.id: line
        for line in (
            await session.execute(select(StockTakeLine).where(StockTakeLine.stock_take_id == stock_take_id))
        ).scalars()
    }
    materials = {
        m.id: m
        for m in (
            await session.execute(
                select(Material).where(
                    Material.id.in_({line.material_id for line in lines_by_id.values() if line.material_id})
                )
            )
        ).scalars()
    }

    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    failed: list[dict] = []
    staged: list[tuple] = []
    skipped_blank = 0

    for i, row in enumerate(reader, start=2):  # row 1 is the header
        try:
            raw_line_id = (row.get("line_id") or "").strip()
            if not raw_line_id:
                raise ValueError("line_id is required")
            line = lines_by_id.get(int(raw_line_id))
            if line is None:
                raise ValueError(f"line_id {raw_line_id} is not on this stock take")

            # Cross-check: catches a file whose rows have been re-sorted, pasted between
            # sheets, or carried over from a different take — cases where the counts are
            # real but would land on the wrong items.
            item_type = (row.get("item_type") or "").strip()
            expected_type = "material" if line.material_id else "product"
            if item_type and item_type != expected_type:
                raise ValueError(f"item_type '{item_type}' doesn't match this line ({expected_type})")
            raw_item_id = (row.get("item_id") or "").strip()
            if raw_item_id and int(raw_item_id) != (line.material_id or line.product_id):
                raise ValueError("item_id doesn't match the item on this line")

            raw_count = (row.get("counted_qty") or "").strip()
            if not raw_count:
                skipped_blank += 1
                continue

            # Parsed explicitly rather than letting InvalidOperation escape: its str() is
            # "[<class 'decimal.ConversionSyntax'>]", which tells someone staring at a
            # confirmation screen nothing about which cell to go and fix.
            try:
                counted = Decimal(raw_count)
            except InvalidOperation:
                raise ValueError(f"'{raw_count}' is not a number") from None
            if counted < 0:
                raise ValueError("counted_qty cannot be negative")
            if line.material_id is not None:
                material = materials.get(line.material_id)
                if material is not None:
                    validate_qty_for_unit(counted, material.unit, "counted_qty")
            elif counted != counted.to_integral_value():
                raise ValueError("counted_qty must be a whole number for finished stock")

            staged.append((line, counted, (row.get("notes") or "").strip() or None))
        except (ValueError, KeyError, InvalidOperation) as e:
            failed.append({"row": i, "error": str(e)})
        except HTTPException as e:
            # validate_qty_for_unit raises HTTPException because it is shared with request
            # handlers — caught separately so a whole-number violation fails just this row.
            failed.append({"row": i, "error": str(e.detail)})

    apply = not dry_run and not (failed and on_error == "fail")
    if apply:
        for line, counted, notes in staged:
            line.counted_qty = counted
            line.notes = notes
            if line.status in (StockTakeLineStatus.pending, StockTakeLineStatus.counted):
                line.status = StockTakeLineStatus.counted
        await session.commit()

    return {"matched": len(staged), "skipped_blank": skipped_blank, "failed": failed, "applied": apply}
