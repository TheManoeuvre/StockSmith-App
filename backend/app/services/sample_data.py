"""The sample workspace's contents: a small, made-up candle maker, about three months in.

Built through the same functions the app's own screens call — creating materials, receiving a
purchase, recording builds, taking and sending orders — rather than by writing rows directly, so
every derived figure (stock, average cost, allocations, buildability, margins) is exactly what the
app would have computed itself. That's also why it's generated rather than shipped as a file: it
is rebuilt against whatever the schema is in this release.

Chosen to show as much of StockSmith as possible in one small business: units in grams,
millilitres and "each"; substitutable materials (two waxes); swaps and quantity overrides on
variants; per-order packaging; a failed pour; a purchase still on its way; orders shipped,
awaiting, and one short of stock. The business, products and buyers are invented.

Only ever run against the sample database — see services/workspace.py.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.general_settings import GeneralSettings
from app.models.material import Material, MaterialAdjustmentMode
from app.models.material_type import MaterialType
from app.models.order import ManualOrderChannel, Order
from app.models.product import Product
from app.models.shipping_profile import ShippingProfile
from app.models.supplier import Supplier
from app.models.variant import ProductVariant
from app.schemas.build import BuildCreate
from app.schemas.material import MaterialCreate
from app.schemas.order import OrderCreate, OrderLineInput
from app.schemas.product import BomLine, ProductCreate
from app.schemas.purchase import PurchaseCreate, PurchaseLineInput
from app.schemas.variant import VariantBomLine, VariantCreate
from app.services import business_presets
from app.services import locations as locs
from app.services import placements
from app.services.kitting import replace_default_kitting_bom
from app.services.placement_hooks import ItemRef
from app.services.costing import create_adjustment
from app.services.variant_boms import apply_variant_bom_overrides

SAMPLE_BUSINESS = "Ember & Fig Candle Co. (sample)"

# name, category, unit, type, colour, reorder at, opening qty, total cost of the opening purchase
_MATERIALS: list[tuple[str, str, str, str | None, str | None, str, str, str]] = [
    ("Soy container wax", "wax", "g", "Soy wax", None, "4000", "20000", "96.00"),
    ("Rapeseed wax", "wax", "g", "Rapeseed wax", None, "2000", "10000", "52.00"),
    ("Fig & cassis fragrance oil", "fragrance", "ml", None, None, "250", "1000", "38.00"),
    ("Sea salt & sage fragrance oil", "fragrance", "ml", None, None, "250", "1000", "38.00"),
    ("Lavender essential oil", "fragrance", "ml", None, None, "100", "500", "27.50"),
    ("Cotton wick, small", "wicks", "each", None, None, "50", "300", "18.00"),
    ("Cotton wick, large", "wicks", "each", None, None, "50", "200", "16.00"),
    ("Amber jar 20cl", "vessels", "each", None, "Amber", "24", "96", "105.60"),
    ("Amber jar 30cl", "vessels", "each", None, "Amber", "24", "72", "100.80"),
    ("Travel tin 100ml", "vessels", "each", None, "Silver", "24", "100", "55.00"),
    ("Product label", "labels", "each", None, None, "100", "500", "22.50"),
    ("Mailer box", "packaging", "each", None, None, "30", "150", "63.00"),
    ("Thank-you card", "packaging", "each", None, None, "50", "250", "12.50"),
]

_BUYERS = [
    "A. Patel", "B. Okafor", "C. Nguyen", "D. Murphy", "E. Rossi", "F. Haddad", "G. Kowalski",
    "H. Tanaka", "I. Svensson", "J. O'Brien", "K. Mensah", "L. García",
]


async def _ids(session: AsyncSession) -> dict[str, int]:
    return {m.name: m.id for m in (await session.scalars(select(Material))).all()}


async def _place_materials(session: AsyncSession, ids: dict[str, int]) -> dict[str, int]:
    """Gives the sample a storage layout and puts the opening stock in it.

    Most items sit in a single location, so the builds and shipped orders that follow visibly
    take their stock off the shelf by themselves. The amber jars are split between two places
    with a little left unassigned, and the product labels are deliberately left unplaced, so the
    Locations page has every kind of state to show.
    """
    workshop = await locs.create_location(session, "Workshop", None)
    stockroom = await locs.create_location(session, "Stockroom", None)
    loft = await locs.create_location(session, "Loft", None)
    wax_shelf = await locs.create_location(session, "Wax shelf", workshop.id)
    oils_rack = await locs.create_location(session, "Oils rack", workshop.id)
    jar_store = await locs.create_location(session, "Jar store", workshop.id)
    packing = await locs.create_location(session, "Packing bench", workshop.id)
    bins = await locs.generate_range(session, oils_rack.id, "Bin {1-3}")  # the range generator at work
    shelves = await locs.generate_range(session, stockroom.id, "Shelf {A-B}")
    bin1, bin2, bin3 = bins
    shelf_a, shelf_b = shelves

    layout: list[tuple[str, int, int]] = [
        ("Soy container wax", wax_shelf.id, 20000),
        ("Rapeseed wax", wax_shelf.id, 10000),
        ("Fig & cassis fragrance oil", bin1.id, 1000),
        ("Sea salt & sage fragrance oil", bin2.id, 1000),
        ("Lavender essential oil", bin3.id, 500),
        ("Cotton wick, small", shelf_a.id, 300),
        ("Cotton wick, large", shelf_a.id, 200),
        ("Amber jar 20cl", jar_store.id, 96),
        ("Travel tin 100ml", jar_store.id, 100),
        ("Mailer box", shelf_b.id, 150),
        ("Thank-you card", shelf_b.id, 250),
        # Amber jar 30cl is split below; Product label is left unplaced on purpose.
    ]
    for name, location_id, qty in layout:
        await placements.place(session, ItemRef(material_id=ids[name]), location_id, Decimal(qty))
    jars30 = ItemRef(material_id=ids["Amber jar 30cl"])
    await placements.place(session, jars30, jar_store.id, Decimal(40))
    await placements.place(session, jars30, loft.id, Decimal(20))  # 12 of the 72 stay unassigned
    return {"packing": packing.id}


async def _place_finished_goods(
    session: AsyncSession, loc: dict[str, int], candle_id: int, variants: dict[str, int], tin_id: int, melt_id: int
) -> None:
    """Puts the freshly built candles on the packing bench, so shipping an order takes them off."""
    for variant_id in variants.values():
        stock = (await session.get(ProductVariant, variant_id)).current_stock
        if stock > 0:
            await placements.place(
                session, ItemRef(product_id=candle_id, variant_id=variant_id), loc["packing"], Decimal(stock)
            )
    for product_id in (tin_id, melt_id):
        stock = (await session.get(Product, product_id)).current_stock
        if stock > 0:
            await placements.place(session, ItemRef(product_id=product_id), loc["packing"], Decimal(stock))


async def build_sample(session: AsyncSession) -> None:
    # Local imports: these modules are routers, and importing them at module load would make
    # services depend on the web layer for anyone importing this file.
    from app.routers import builds, materials, orders, products, purchases

    now = datetime.now(timezone.utc)

    await business_presets.apply_preset(session, "candles_soap")
    settings = await session.get(GeneralSettings, 1)
    settings.setup_checklist_dismissed = True  # nothing to set up in a sample

    supplier = Supplier(name="Northfield Candle Supplies (sample)", website_url=None)
    session.add(supplier)
    await session.commit()

    types = {t.name: t.id for t in (await session.scalars(select(MaterialType))).all()}
    for name, category, unit, type_name, colour, reorder, _, _ in _MATERIALS:
        await materials.create_material(
            MaterialCreate(
                name=name,
                category=category,
                unit=unit,
                reorder_threshold=Decimal(reorder),
                material_type_id=types.get(type_name) if type_name else None,
                colour=colour,
                default_supplier_id=supplier.id,
            ),
            session,
        )
    ids = await _ids(session)

    # Opening stock arrives the way it really would: one big supplier order, received.
    opening = await purchases.create_purchase(
        PurchaseCreate(
            supplier_id=supplier.id,
            supplier_order_number="NCS-1001",
            order_date=(now - timedelta(days=100)).date(),
            delivery_cost=Decimal("9.95"),
            lines=[
                PurchaseLineInput(material_id=ids[name], qty=Decimal(qty), total_cost=Decimal(cost))
                for name, _, _, _, _, _, qty, cost in _MATERIALS
            ],
        ),
        session,
    )
    await purchases.receive_purchase(opening.id, session)
    loc = await _place_materials(session, ids)

    # Usual packaging first, so every product created below starts with it.
    await replace_default_kitting_bom(
        session,
        [(ids["Mailer box"], Decimal(1)), (ids["Thank-you card"], Decimal(1))],
    )
    await session.commit()

    postage = {p.name: p for p in (await session.scalars(select(ShippingProfile))).all()}
    small_parcel = next((p for n, p in postage.items() if "Small Parcel" in n), None)
    large_letter = next((p for n, p in postage.items() if "Large Letter" in n), None)

    candle = await products.create_product(
        ProductCreate(
            name="Container candle",
            sku="EF-CC",
            description="Hand-poured soy candle in an amber jar.",
            sale_price=Decimal("18.00"),
            shipping_profile_id=small_parcel.id if small_parcel else None,
        ),
        session,
    )
    await products.replace_bom(
        candle.id,
        [
            BomLine(material_id=ids["Soy container wax"], qty_required=Decimal(180)),
            BomLine(material_id=ids["Fig & cassis fragrance oil"], qty_required=Decimal(18)),
            BomLine(material_id=ids["Cotton wick, small"], qty_required=Decimal(1)),
            BomLine(material_id=ids["Amber jar 20cl"], qty_required=Decimal(1)),
            BomLine(material_id=ids["Product label"], qty_required=Decimal(1)),
        ],
        session,
    )
    candle_row = await session.get(Product, candle.id)
    candle_row.variant_attribute1_name = "Scent"
    candle_row.variant_attribute2_name = "Size"
    await session.commit()

    fig, sea = ids["Fig & cassis fragrance oil"], ids["Sea salt & sage fragrance oil"]
    large_lines = [
        VariantBomLine(material_id=ids["Soy container wax"], qty_required=Decimal(270)),
        VariantBomLine(
            material_id=ids["Cotton wick, large"], qty_required=Decimal(1), replaces_material_id=ids["Cotton wick, small"]
        ),
        VariantBomLine(material_id=ids["Amber jar 30cl"], qty_required=Decimal(1), replaces_material_id=ids["Amber jar 20cl"]),
    ]
    variants: dict[str, int] = {}
    for scent, size, suffix, price, overrides in [
        ("Fig & cassis", "20cl", "FIG-20", "18.00", []),
        ("Fig & cassis", "30cl", "FIG-30", "26.00", [*large_lines, VariantBomLine(material_id=fig, qty_required=Decimal(27))]),
        (
            "Sea salt & sage",
            "20cl",
            "SEA-20",
            "18.00",
            [VariantBomLine(material_id=sea, qty_required=Decimal(18), replaces_material_id=fig)],
        ),
        (
            "Sea salt & sage",
            "30cl",
            "SEA-30",
            "26.00",
            [*large_lines, VariantBomLine(material_id=sea, qty_required=Decimal(27), replaces_material_id=fig)],
        ),
    ]:
        created = await products.create_variant(
            candle.id, VariantCreate(variant_name=f"{scent} / {size}", sku_suffix=suffix, on_platform_conflict="proceed"), session
        )
        variant = await session.get(ProductVariant, created.id)
        variant.attribute1_value, variant.attribute2_value = scent, size
        variant.sale_price = Decimal(price)
        await session.commit()
        if overrides:
            await apply_variant_bom_overrides(session, variant, overrides)
        variants[f"{scent} / {size}"] = variant.id

    tin = await products.create_product(
        ProductCreate(
            name="Travel tin candle",
            sku="EF-TT",
            description="Lavender candle in a lidded travel tin.",
            sale_price=Decimal("9.00"),
            shipping_profile_id=large_letter.id if large_letter else None,
        ),
        session,
    )
    await products.replace_bom(
        tin.id,
        [
            BomLine(material_id=ids["Rapeseed wax"], qty_required=Decimal(90)),
            BomLine(material_id=ids["Lavender essential oil"], qty_required=Decimal(9)),
            BomLine(material_id=ids["Cotton wick, small"], qty_required=Decimal(1)),
            BomLine(material_id=ids["Travel tin 100ml"], qty_required=Decimal(1)),
            BomLine(material_id=ids["Product label"], qty_required=Decimal(1)),
        ],
        session,
    )

    melt = await products.create_product(
        ProductCreate(
            name="Wax melt snap bar",
            sku="EF-WM",
            description="Six-cube fig & cassis wax melt.",
            sale_price=Decimal("6.00"),
            shipping_profile_id=large_letter.id if large_letter else None,
        ),
        session,
    )
    await products.replace_bom(
        melt.id,
        [
            BomLine(material_id=ids["Soy container wax"], qty_required=Decimal(80)),
            BomLine(material_id=ids["Fig & cassis fragrance oil"], qty_required=Decimal(8)),
            BomLine(material_id=ids["Product label"], qty_required=Decimal(1)),
        ],
        session,
    )

    # Pouring days. One batch of tins had a failed pour: the wax and oil are gone either way.
    for variant_name, qty in [("Fig & cassis / 20cl", 12), ("Fig & cassis / 30cl", 8), ("Sea salt & sage / 20cl", 10), ("Sea salt & sage / 30cl", 2)]:
        await builds.record_build(BuildCreate(product_id=candle.id, variant_id=variants[variant_name], qty_built=qty), session)
    await builds.record_build(BuildCreate(product_id=tin.id, qty_built=14, qty_failed=2), session)
    await builds.record_build(BuildCreate(product_id=melt.id, qty_built=20), session)
    await _place_finished_goods(session, loc, candle.id, variants, tin.id, melt.id)

    # Orders over the last three months. Most are sent; the newest few are waiting, and the
    # last asks for more large sea-salt candles than are on the shelf — so the Dashboard has
    # something to build.
    catalogue = [
        (candle.id, variants["Fig & cassis / 20cl"], "18.00"),
        (candle.id, variants["Fig & cassis / 30cl"], "26.00"),
        (candle.id, variants["Sea salt & sage / 20cl"], "18.00"),
        (tin.id, None, "9.00"),
        (melt.id, None, "6.00"),
    ]
    plan: list[tuple[int, list[tuple[int, int | None, str, int]], bool]] = []
    for i in range(22):
        days_ago = 88 - i * 4
        first = catalogue[i % len(catalogue)]
        lines = [(*first, 1 + (i % 3 == 0))]
        if i % 4 == 1:
            second = catalogue[(i + 2) % len(catalogue)]
            lines.append((*second, 1))
        plan.append((days_ago, lines, days_ago > 3))
    plan.append((1, [(candle.id, variants["Sea salt & sage / 30cl"], "26.00", 3)], False))

    for n, (days_ago, lines, shipped) in enumerate(plan):
        channel = [ManualOrderChannel.etsy, ManualOrderChannel.manual, ManualOrderChannel.etsy, ManualOrderChannel.ebay][n % 4]
        created = await orders.create_order(
            OrderCreate(
                buyer_name=_BUYERS[n % len(_BUYERS)],
                currency="GBP",
                manual_channel=channel,
                lines=[
                    OrderLineInput(product_id=p, variant_id=v, ordered_qty=q, unit_price=Decimal(price))
                    for p, v, price, q in lines
                ],
            ),
            session,
        )
        if shipped:
            await orders.ship_order_endpoint(created.id, session)
        placed = now - timedelta(days=days_ago, hours=n % 7)
        await session.execute(
            update(Order)
            .where(Order.id == created.id)
            .values(
                order_placed_at=placed,
                shipped_at=placed + timedelta(days=1) if shipped else None,
            )
        )
        await session.commit()

    # Restocking wax, still on its way.
    await purchases.create_purchase(
        PurchaseCreate(
            supplier_id=supplier.id,
            supplier_order_number="NCS-1042",
            order_date=(now - timedelta(days=2)).date(),
            expected_arrival_date=date.today() + timedelta(days=3),
            lines=[
                PurchaseLineInput(material_id=ids["Soy container wax"], qty=Decimal(10000), total_cost=Decimal("49.00")),
                PurchaseLineInput(material_id=ids["Amber jar 30cl"], qty=Decimal(48), total_cost=Decimal("67.20")),
            ],
        ),
        session,
    )

    # A small counting discrepancy, as a real shelf would have.
    await create_adjustment(session, ids["Product label"], MaterialAdjustmentMode.adjust, Decimal(-6), "Spoiled in the printer")
    await session.commit()

    await _sample_work_flow(session, candle.id)


async def _sample_work_flow(session: AsyncSession, candle_id: int) -> None:
    """Switch work flow on with a route in the shop's own words: prepare the jar, pour (Make), let it
    cure, then ship. The pending order for large sea-salt candles then shows up on the Work page as
    something to prepare and pour. Done last, so the builds above predate the feature and none of that
    stock is waiting to cure — see GeneralSettings.work_flow_enabled_at."""
    from app.models.work import WorkStageKind
    from app.services import work_routes
    from app.services.work_routes import StepInput

    prepare = await work_routes.create_stage(
        session, name="Prepare jar", station="Bench", per_order=False, wait_minutes=None, typical_minutes=10
    )
    cure = await work_routes.create_stage(
        session, name="Cure", station="Shelf", per_order=False, wait_minutes=2880, typical_minutes=None
    )
    make = await work_routes._first_stage_of_kind(session, WorkStageKind.make)
    ship = await work_routes._first_stage_of_kind(session, WorkStageKind.ship)
    route = await work_routes.create_route(
        session,
        name="Container candle",
        steps=[StepInput(stage_id=s.id) for s in (prepare, make, cure, ship)],
    )
    candle = await session.get(Product, candle_id)
    candle.work_route_id = route.id
    await session.commit()
    await work_routes.set_work_flow_enabled(session, True)
