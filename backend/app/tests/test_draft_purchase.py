"""POST /materials/{id}/draft-purchase — turns a low-stock alert into a pending PO in
one click, then the frontend navigates to that PO's edit page.

Regression: the endpoint returned its Purchase through PurchaseRead, whose line schema
carries `receipts` plus the `received_qty`/`outstanding_qty` properties that read them.
The query eager-loaded `lines` but not `lines.receipts`, so serialising the response
lazy-loaded on the async session and raised MissingGreenlet — a 500 on every click.
"""

from decimal import Decimal

import pytest

from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.supplier import Supplier
from app.routers.materials import create_draft_purchase
from app.schemas.material import DraftPurchaseCreate
from app.schemas.purchase import PurchaseRead


async def test_draft_purchase_is_serialisable(session):
    supplier = Supplier(name="Acme Filaments")
    session.add(supplier)
    await session.flush()

    material = Material(
        name="PLA",
        category=LegacyMaterialCategory.filament,
        unit=MaterialUnit.g,
        default_supplier_id=supplier.id,
        typical_reorder_qty=Decimal("2.5"),
    )
    session.add(material)
    await session.flush()

    purchase = await create_draft_purchase(material.id, DraftPurchaseCreate(), session)

    # The router returns an ORM object; FastAPI then runs it through PurchaseRead. Doing
    # that here is what would trip MissingGreenlet if `receipts` were not eager-loaded.
    body = PurchaseRead.model_validate(purchase)

    assert body.supplier_id == supplier.id
    assert len(body.lines) == 1
    line = body.lines[0]
    assert line.material_id == material.id
    assert line.qty == Decimal("2.5")  # falls back to typical_reorder_qty
    assert line.receipts == []
    assert line.outstanding_qty == Decimal("2.5")


async def test_draft_purchase_qty_defaults_to_one_without_reorder_hint(session):
    material = Material(name="Grommet", category=LegacyMaterialCategory.other, unit=MaterialUnit.each)
    session.add(material)
    await session.flush()

    purchase = await create_draft_purchase(material.id, DraftPurchaseCreate(), session)
    body = PurchaseRead.model_validate(purchase)

    assert body.supplier_id is None
    assert body.lines[0].qty == Decimal("1")


async def test_draft_purchase_honours_explicit_qty(session):
    material = Material(
        name="Grommet",
        category=LegacyMaterialCategory.other,
        unit=MaterialUnit.each,
        typical_reorder_qty=Decimal("2"),
    )
    session.add(material)
    await session.flush()

    purchase = await create_draft_purchase(
        material.id, DraftPurchaseCreate(qty=Decimal("7")), session
    )

    assert PurchaseRead.model_validate(purchase).lines[0].qty == Decimal("7")


async def test_draft_purchase_404_for_unknown_material(session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await create_draft_purchase(999999, DraftPurchaseCreate(), session)
    assert exc.value.status_code == 404
