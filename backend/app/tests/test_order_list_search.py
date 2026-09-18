"""list_orders' `q` is the orders list's search box: a substring match on the order number,
notes, and the SKUs / product names / variant names of its lines, ignoring case and
symbols on both sides. It composes with status_filter and the search-hit count is what
paginates."""

from app.models.order import Order, OrderLine, OrderStatus
from app.models.product import Product
from app.models.variant import ProductVariant
from app.routers.orders import list_orders


async def _ids(session, q: str, **kw) -> list[int]:
    page = await list_orders(q=q, limit=50, offset=0, session=session, **kw)
    assert page.total == len(page.items)
    return sorted(o.id for o in page.items)


async def _seed(session):
    planter = Product(name="Hex Planter", sku="HEX")
    vase = Product(name="Ridge Vase", sku="VASE")
    session.add_all([planter, vase])
    await session.flush()
    red = ProductVariant(product_id=planter.id, variant_name="Red", sku_suffix="RD", current_stock=0)
    session.add(red)
    await session.flush()

    etsy = Order(status=OrderStatus.pending, external_order_id="7777777", notes="Gift wrap please")
    manual = Order(status=OrderStatus.shipped, notes="Collected in person")
    unmapped = Order(status=OrderStatus.pending, external_order_id="8888888")
    session.add_all([etsy, manual, unmapped])
    await session.flush()
    session.add_all(
        [
            OrderLine(order_id=etsy.id, product_id=planter.id, variant_id=red.id, ordered_qty=1, sku="HEX-RD"),
            OrderLine(order_id=manual.id, product_id=vase.id, ordered_qty=1),
            # A marketplace line whose SKU never matched the catalog — the raw SKU is all it has.
            OrderLine(order_id=unmapped.id, ordered_qty=1, sku="MYSTERY-XX", needs_mapping=True),
        ]
    )
    await session.commit()
    return etsy.id, manual.id, unmapped.id


async def test_search_matches_order_number_with_or_without_hash(session):
    etsy, manual, _ = await _seed(session)
    assert await _ids(session, "77777") == [etsy]
    assert await _ids(session, "#7777777") == [etsy]
    # A manual order has no external id; its own id is the number the list shows for it.
    assert await _ids(session, f"#{manual}") == [manual]


async def test_search_matches_notes_case_insensitively(session):
    etsy, manual, _ = await _seed(session)
    assert await _ids(session, "GIFT WRAP") == [etsy]
    assert await _ids(session, "collected") == [manual]


async def test_search_matches_line_product_name_sku_and_variant(session):
    etsy, manual, unmapped = await _seed(session)
    assert await _ids(session, "hex planter") == [etsy]
    assert await _ids(session, "ridge") == [manual]
    # Product SKU, line SKU, variant name and variant SKU suffix all count.
    assert await _ids(session, "vase") == [manual]
    assert await _ids(session, "hex-rd") == [etsy]
    assert await _ids(session, "red") == [etsy]
    # An unmapped line has no product to match on, only its raw marketplace SKU.
    assert await _ids(session, "mystery") == [unmapped]


async def test_search_ignores_symbols_on_both_sides(session):
    etsy, manual, _ = await _seed(session)
    # Typed without the hyphen, or with extra ones, still hits the stored "HEX-RD".
    assert await _ids(session, "hexrd") == [etsy]
    assert await _ids(session, "h-e-x r.d") == [etsy]
    # And a stored value with symbols matches a term typed without them.
    assert await _ids(session, "giftwrap") == [etsy]
    assert await _ids(session, "collected/in/person") == [manual]


async def test_search_composes_with_status_filter_and_ignores_blank(session):
    etsy, manual, unmapped = await _seed(session)
    # Every order has an "e" somewhere; the status filter still narrows.
    assert await _ids(session, "e", status_filter="shipped") == [manual]
    everything = sorted([etsy, manual, unmapped])
    assert await _ids(session, "   ") == everything
    # A term that's nothing but symbols has nothing to match on, so it doesn't filter.
    assert await _ids(session, "#-/") == everything
    assert await _ids(session, "no such thing") == []
