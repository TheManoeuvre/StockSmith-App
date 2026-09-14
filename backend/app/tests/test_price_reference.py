"""POST /purchases/price-reference — "what did we last pay for these materials?"

Feeds the new-purchase panel's per-line comparison: it flags a line priced above what the
chosen supplier last charged. Prefers the most recent line from that supplier, falls back
to the most recent from anyone, and omits materials with no priced history.
"""

from datetime import date
from decimal import Decimal

from app.models.material import LegacyMaterialCategory, Material, MaterialUnit
from app.models.purchase import MaterialPurchase, Purchase, PurchaseStatus
from app.models.supplier import Supplier
from app.routers.purchases import price_reference
from app.schemas.purchase import PriceReferenceRequest


async def _material(session, name="PLA") -> Material:
    material = Material(name=name, category=LegacyMaterialCategory.filament, unit=MaterialUnit.g)
    session.add(material)
    await session.flush()
    return material


async def _purchase(session, *, supplier_id, material_id, qty, total_cost, on, ref=None) -> Purchase:
    purchase = Purchase(
        supplier_id=supplier_id,
        supplier_order_number=ref,
        order_date=on,
        status=PurchaseStatus.ordered,
    )
    purchase.lines = [
        MaterialPurchase(material_id=material_id, qty=Decimal(qty), total_cost=Decimal(total_cost))
    ]
    session.add(purchase)
    await session.flush()
    return purchase


async def test_prefers_the_most_recent_line_from_the_asked_supplier(session):
    acme = Supplier(name="Acme")
    other = Supplier(name="Other")
    session.add_all([acme, other])
    await session.flush()
    material = await _material(session)

    await _purchase(
        session, supplier_id=acme.id, material_id=material.id, qty=100, total_cost="50",
        on=date(2026, 1, 1), ref="ACM-1",
    )
    # Newer, but a different supplier — must not win when Acme is asked about.
    await _purchase(
        session, supplier_id=other.id, material_id=material.id, qty=100, total_cost="80",
        on=date(2026, 2, 1),
    )

    entries = await price_reference(
        PriceReferenceRequest(supplier_id=acme.id, material_ids=[material.id]), session
    )

    assert len(entries) == 1
    entry = entries[0]
    assert entry.material_id == material.id
    assert entry.unit_cost == Decimal("0.5")
    assert entry.supplier_id == acme.id
    assert entry.purchase_ref == "ACM-1"
    assert entry.at == date(2026, 1, 1)
    assert entry.same_supplier is True


async def test_falls_back_to_the_most_recent_from_any_supplier(session):
    acme = Supplier(name="Acme")
    other = Supplier(name="Other")
    session.add_all([acme, other])
    await session.flush()
    material = await _material(session)

    await _purchase(
        session, supplier_id=acme.id, material_id=material.id, qty=100, total_cost="50",
        on=date(2026, 1, 1),
    )
    await _purchase(
        session, supplier_id=other.id, material_id=material.id, qty=100, total_cost="80",
        on=date(2026, 2, 1),
    )

    entries = await price_reference(
        PriceReferenceRequest(supplier_id=acme.id, material_ids=[material.id]), session
    )
    # Acme has a line, so that one is chosen and same_supplier holds.
    assert entries[0].supplier_id == acme.id

    # A supplier with no history at all → newest line from anyone, same_supplier False.
    entries = await price_reference(
        PriceReferenceRequest(supplier_id=9999, material_ids=[material.id]), session
    )
    assert len(entries) == 1
    assert entries[0].supplier_id == other.id
    assert entries[0].unit_cost == Decimal("0.8")
    assert entries[0].same_supplier is False


async def test_skips_materials_with_no_priced_history_and_zero_totals(session):
    supplier = Supplier(name="Acme")
    session.add(supplier)
    await session.flush()
    priced = await _material(session, name="PLA")
    never_bought = await _material(session, name="PETG")
    free_only = await _material(session, name="Grommet")

    await _purchase(
        session, supplier_id=supplier.id, material_id=priced.id, qty=10, total_cost="20",
        on=date(2026, 1, 1),
    )
    # total_cost 0 — a line that carries no price, so it must not be reported.
    await _purchase(
        session, supplier_id=supplier.id, material_id=free_only.id, qty=10, total_cost="0",
        on=date(2026, 1, 1),
    )

    entries = await price_reference(
        PriceReferenceRequest(
            supplier_id=supplier.id,
            material_ids=[priced.id, never_bought.id, free_only.id],
        ),
        session,
    )

    assert {e.material_id for e in entries} == {priced.id}


async def test_empty_material_ids_returns_empty(session):
    assert await price_reference(PriceReferenceRequest(material_ids=[]), session) == []
