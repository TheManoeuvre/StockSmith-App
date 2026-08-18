"""ABC classification and the due-for-counting sweep (services/abc.py).

Covers: the three-level tier fallback for materials and products, the separate three-level
cadence fallback, the two baselines being genuinely independent, variants resolving through
their parent product, and which rows compute_due_for_count treats as countable at all —
which is the part most likely to go wrong quietly, since a product with variants and a
bundle look identical from the products table alone.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models.abc_classification import (
    ABCClass,
    ABCScope,
    ABCTierSetting,
    MaterialCategoryABC,
    ProductTypeABC,
)
from app.models.general_settings import GeneralSettings
from app.models.material import Material, MaterialUnit
from app.models.material_category import MaterialCategory
from app.models.product import Product, ProductBundleItem
from app.models.product_type import ProductType
from app.models.variant import ProductVariant
from app.routers.materials import list_materials
from app.routers.products import create_product, get_product, update_product
from app.schemas.abc import CategoryTier, StockCountSettingsUpdate
from app.schemas.product import ProductCreate, ProductRead, ProductUpdate
from app.services import abc
from app.services.material_categories import legacy_value_for
from app.services.abc import (
    _DEFAULT_INTERVAL_DAYS,
    compute_due_for_count,
    due_state,
    load_rules,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


async def _settings(session, **overrides) -> GeneralSettings:
    settings = GeneralSettings(id=1, **overrides)
    session.add(settings)
    await session.flush()
    return settings



async def _category(session, name: str) -> MaterialCategory:
    """The seeded category row of that name — conftest puts the original seven in place.

    Tests key tiers on the row, not on the legacy `materials.category` enum beside it,
    because that is what the app now does: everything a user adds stores 'other' there.
    """
    return (
        await session.execute(select(MaterialCategory).where(MaterialCategory.name == name))
    ).scalar_one()


async def _material(session, name="Resin", category="resin", **kwargs) -> Material:
    row = await _category(session, category)
    m = Material(
        name=name, category=legacy_value_for(category), category_id=row.id, unit=MaterialUnit.ml, **kwargs
    )
    session.add(m)
    await session.flush()
    return m


async def _product(session, name="Keyring", sku=None, **kwargs) -> Product:
    p = Product(name=name, sku=sku or f"SKU-{name}", **kwargs)
    session.add(p)
    await session.flush()
    return p


# --- tier resolution -------------------------------------------------------------------


async def test_material_tier_falls_through_all_three_levels(session):
    await _settings(session, default_material_abc_class=ABCClass.C)
    session.add(MaterialCategoryABC(category_id=(await _category(session, "resin")).id, abc_class=ABCClass.B))
    own = await _material(session, "Own", abc_class=ABCClass.A)
    by_category = await _material(session, "ByCategory")
    by_default = await _material(session, "ByDefault", category="hardware")
    await session.commit()

    rules = await load_rules(session)

    assert (rules.for_material(own).abc_class, rules.for_material(own).class_source) == (ABCClass.A, "item")
    assert (rules.for_material(by_category).abc_class, rules.for_material(by_category).class_source) == (
        ABCClass.B,
        "group",
    )
    assert (rules.for_material(by_default).abc_class, rules.for_material(by_default).class_source) == (
        ABCClass.C,
        "default",
    )


async def test_product_tier_falls_through_all_three_levels(session):
    """The same precedence as materials — the symmetry the brief assumed, asserted."""
    await _settings(session, default_product_abc_class=ABCClass.C)
    ptype = ProductType(name="Coaster")
    session.add(ptype)
    await session.flush()
    session.add(ProductTypeABC(product_type_id=ptype.id, abc_class=ABCClass.B))
    own = await _product(session, "Own", abc_class=ABCClass.A, product_type_id=ptype.id)
    by_type = await _product(session, "ByType", product_type_id=ptype.id)
    by_default = await _product(session, "ByDefault")
    await session.commit()

    rules = await load_rules(session)

    assert rules.for_product(own).abc_class == ABCClass.A
    assert rules.for_product(by_type).abc_class == ABCClass.B
    assert rules.for_product(by_default).abc_class == ABCClass.C


async def test_material_and_product_baselines_are_independent(session):
    """Two baselines, not one shared default — the whole reason ABCScope exists."""
    await _settings(session, default_material_abc_class=ABCClass.A, default_product_abc_class=ABCClass.C)
    material = await _material(session)
    product = await _product(session)
    await session.commit()

    rules = await load_rules(session)

    assert rules.for_material(material).abc_class == ABCClass.A
    assert rules.for_product(product).abc_class == ABCClass.C


async def test_item_override_beats_a_group_tier_that_disagrees(session):
    await _settings(session)
    session.add(MaterialCategoryABC(category_id=(await _category(session, "packaging")).id, abc_class=ABCClass.C))
    material = await _material(session, "Boxes", category="packaging", abc_class=ABCClass.A)
    await session.commit()

    assert (await load_rules(session)).for_material(material).abc_class == ABCClass.A


# --- cadence resolution ----------------------------------------------------------------


async def test_interval_falls_through_item_then_tier_then_default(session):
    await _settings(session, default_material_abc_class=ABCClass.B)
    session.add(ABCTierSetting(scope=ABCScope.material, tier=ABCClass.B, interval_days=45))
    own = await _material(session, "Own", stock_take_interval_days=7)
    by_tier = await _material(session, "ByTier")
    by_code_default = await _material(session, "ByCodeDefault", abc_class=ABCClass.A)
    await session.commit()

    rules = await load_rules(session)

    assert (rules.for_material(own).interval_days, rules.for_material(own).interval_source) == (7, "item")
    assert (rules.for_material(by_tier).interval_days, rules.for_material(by_tier).interval_source) == (
        45,
        "tier",
    )
    # No override row for A, so the shipped constant wins.
    assert rules.for_material(by_code_default).interval_days == _DEFAULT_INTERVAL_DAYS[ABCClass.A]
    assert rules.for_material(by_code_default).interval_source == "default"


async def test_tier_interval_overrides_are_scoped(session):
    """A material-scope override must not leak onto products of the same tier."""
    await _settings(session, default_material_abc_class=ABCClass.C, default_product_abc_class=ABCClass.C)
    session.add(ABCTierSetting(scope=ABCScope.material, tier=ABCClass.C, interval_days=200))
    material = await _material(session)
    product = await _product(session)
    await session.commit()

    rules = await load_rules(session)

    assert rules.for_material(material).interval_days == 200
    assert rules.for_product(product).interval_days == _DEFAULT_INTERVAL_DAYS[ABCClass.C]


async def test_ships_with_working_defaults_on_an_untouched_database(session):
    """No override rows anywhere: everything still resolves to a real cadence rather than
    to nothing, which is the point of not leaving these blank."""
    await _settings(session)
    material = await _material(session)
    await session.commit()

    resolved = (await load_rules(session)).for_material(material)

    assert resolved.abc_class == ABCClass.C
    assert resolved.interval_days == 90


# --- due_state -------------------------------------------------------------------------


def test_never_counted_is_due_but_has_no_overdue_figure():
    state = due_state(None, 30, NOW)
    assert state.is_due is True
    assert state.days_overdue is None
    assert state.next_due_at is None


def test_not_yet_due_and_exactly_due():
    assert due_state(NOW - timedelta(days=29), 30, NOW).is_due is False
    assert due_state(NOW - timedelta(days=30), 30, NOW).is_due is True
    assert due_state(NOW - timedelta(days=30), 30, NOW).days_overdue == 0
    assert due_state(NOW - timedelta(days=44), 30, NOW).days_overdue == 14


def test_naive_stored_datetime_is_treated_as_utc():
    """SQLite doesn't reliably round-trip tzinfo, so a value this app wrote can come back
    naive; comparing it raw would raise instead of just working."""
    naive = (NOW - timedelta(days=40)).replace(tzinfo=None)
    assert due_state(naive, 30, NOW).days_overdue == 10


# --- compute_due_for_count -------------------------------------------------------------


async def test_never_counted_sorts_before_the_most_overdue(session):
    await _settings(session)
    await _material(session, "Never")
    await _material(session, "Ancient", last_stock_take_at=NOW - timedelta(days=900))
    await session.commit()

    due = await compute_due_for_count(session, now=NOW)

    assert [d.name for d in due] == ["Never", "Ancient"]
    assert due[0].days_overdue is None


async def test_recently_counted_item_is_absent(session):
    await _settings(session)
    await _material(session, "Counted", last_stock_take_at=NOW - timedelta(days=5))
    await session.commit()

    assert await compute_due_for_count(session, now=NOW) == []


async def test_inactive_items_are_excluded(session):
    await _settings(session)
    await _material(session, "Retired", is_active=False)
    await _product(session, "Discontinued", is_active=False)
    await session.commit()

    assert await compute_due_for_count(session, now=NOW) == []


async def test_bundle_products_are_excluded(session):
    """A bundle's quantity is derived from its components, so there is nothing to count."""
    await _settings(session)
    component = await _product(session, "Component")
    bundle = await _product(session, "Gift Set", is_bundle=True)
    session.add(ProductBundleItem(bundle_product_id=bundle.id, component_product_id=component.id, qty=1))
    await session.commit()

    due = await compute_due_for_count(session, now=NOW)

    assert [d.name for d in due] == ["Component"]


async def test_a_product_with_active_variants_is_counted_as_its_variants(session):
    """Stock lives on the variant row when there is one, so the product itself must not
    also appear — that number is never written."""
    await _settings(session)
    product = await _product(session, "Keyring")
    session.add_all(
        [
            ProductVariant(product_id=product.id, variant_name="Red"),
            ProductVariant(product_id=product.id, variant_name="Blue"),
        ]
    )
    await session.commit()

    due = await compute_due_for_count(session, now=NOW)

    assert [d.name for d in due] == ["Keyring — Blue", "Keyring — Red"]
    assert all(d.variant_id is not None and d.product_id == product.id for d in due)


async def test_an_inactive_variant_does_not_stand_in_for_the_product(session):
    """With no *active* variants the product holds its own stock again, so it is the row
    that wants counting."""
    await _settings(session)
    product = await _product(session, "Keyring")
    session.add(ProductVariant(product_id=product.id, variant_name="Discontinued", is_active=False))
    await session.commit()

    due = await compute_due_for_count(session, now=NOW)

    assert [(d.name, d.variant_id) for d in due] == [("Keyring", None)]


async def test_variants_inherit_the_products_tier_and_cadence_but_own_their_dates(session):
    await _settings(session, default_product_abc_class=ABCClass.C)
    product = await _product(session, "Keyring", abc_class=ABCClass.A, stock_take_interval_days=10)
    session.add_all(
        [
            ProductVariant(product_id=product.id, variant_name="Stale", last_stock_take_at=NOW - timedelta(days=40)),
            ProductVariant(product_id=product.id, variant_name="Fresh", last_stock_take_at=NOW - timedelta(days=2)),
        ]
    )
    await session.commit()

    due = await compute_due_for_count(session, now=NOW)

    assert [d.name for d in due] == ["Keyring — Stale"]
    assert due[0].abc_class == ABCClass.A
    assert due[0].interval_days == 10
    assert due[0].days_overdue == 30


# --- response serialization ------------------------------------------------------------


async def test_creating_and_updating_a_product_returns_a_serializable_read(session, session_factory):
    """ProductRead exposes product_type_name, which reads through a relationship. If the
    handler hands back a product without it loaded, serialization raises MissingGreenlet
    instead of lazy-loading — a failure that only shows up over HTTP, which is how it got
    past a green suite the first time.

    Each handler runs on its own fresh session, the way a request does. That detail is the
    test: reusing the session that created the ProductType lets the lazy load be answered
    from the identity map with no IO, so the bug hides and the test passes either way.
    """
    await _settings(session)
    ptype = ProductType(name="Coaster")
    session.add(ptype)
    await session.commit()
    await session.refresh(ptype)

    async with session_factory() as fresh:
        created = await create_product(ProductCreate(name="Oak", sku="OAK-1", product_type_id=ptype.id), fresh)
        assert ProductRead.model_validate(created).product_type_name == "Coaster"
        product_id = created.id

    async with session_factory() as fresh:
        updated = await update_product(product_id, ProductUpdate(name="Oak Coaster"), fresh)
        assert ProductRead.model_validate(updated).product_type_name == "Coaster"

    async with session_factory() as fresh:
        read = await get_product(product_id, fresh)
        assert read.product_type_name == "Coaster"
        assert read.classification is not None


async def test_material_read_carries_the_resolved_classification(session):
    await _settings(session, default_material_abc_class=ABCClass.C)
    session.add(MaterialCategoryABC(category_id=(await _category(session, "resin")).id, abc_class=ABCClass.A))
    await _material(session, "Grey Resin")
    await session.commit()

    reads = await list_materials(session=session)

    assert [(r.name, r.classification.abc_class, r.classification.class_source) for r in reads] == [
        ("Grey Resin", ABCClass.A, "group")
    ]


# --- settings round-trip ---------------------------------------------------------------


async def test_settings_report_every_tier_including_the_unstored_ones(session):
    """A settings screen has to show a number for all six tier/scope pairs, whether or not
    one is stored — is_override is what tells the two apart."""
    await _settings(session)
    await session.commit()

    read = await abc.read_settings(session)

    assert [t.tier for t in read.material_tier_intervals] == [ABCClass.A, ABCClass.B, ABCClass.C]
    assert [t.interval_days for t in read.material_tier_intervals] == [30, 60, 90]
    assert all(t.is_override is False for t in read.material_tier_intervals)


async def test_writing_settings_stores_only_the_flagged_overrides(session):
    """An interval left alone must not be written back as an override, or a later change
    to the shipped defaults would never reach it."""
    await _settings(session)
    await session.commit()
    read = await abc.read_settings(session)
    read.material_tier_intervals[0].interval_days = 14
    read.material_tier_intervals[0].is_override = True

    written = await abc.write_settings(session, StockCountSettingsUpdate(**read.model_dump()))

    stored = (await session.execute(select(ABCTierSetting))).scalars().all()
    assert [(s.scope, s.tier, s.interval_days) for s in stored] == [(ABCScope.material, ABCClass.A, 14)]
    assert written.material_tier_intervals[0].is_override is True
    assert written.material_tier_intervals[1].is_override is False


async def test_writing_settings_clears_assignments_absent_from_the_payload(session):
    """Replace-in-full, not patch — otherwise un-assigning a category's tier would be
    inexpressible."""
    await _settings(session)
    session.add(MaterialCategoryABC(category_id=(await _category(session, "resin")).id, abc_class=ABCClass.A))
    await session.commit()
    read = await abc.read_settings(session)
    assert len(read.category_tiers) == 1
    read.category_tiers = []

    written = await abc.write_settings(session, StockCountSettingsUpdate(**read.model_dump()))

    assert written.category_tiers == []
    material = await _material(session)
    await session.commit()
    assert (await load_rules(session)).for_material(material).class_source == "default"


async def test_written_settings_take_effect_on_the_next_resolution(session):
    await _settings(session)
    material = await _material(session, category="packaging")
    await session.commit()
    read = await abc.read_settings(session)
    read.default_material_abc_class = ABCClass.B
    read.category_tiers = [CategoryTier(category_id=(await _category(session, "packaging")).id, abc_class=ABCClass.A)]

    await abc.write_settings(session, StockCountSettingsUpdate(**read.model_dump()))

    resolved = (await load_rules(session)).for_material(material)
    assert resolved.abc_class == ABCClass.A
    assert resolved.interval_days == _DEFAULT_INTERVAL_DAYS[ABCClass.A]


async def test_materials_and_products_appear_together_ranked_by_lateness(session):
    await _settings(session)
    await _material(session, "Resin", last_stock_take_at=NOW - timedelta(days=100))
    await _product(session, "Keyring", last_stock_take_at=NOW - timedelta(days=300))
    await session.commit()

    due = await compute_due_for_count(session, now=NOW)

    assert [(d.scope, d.name) for d in due] == [
        (ABCScope.product, "Keyring"),
        (ABCScope.material, "Resin"),
    ]
