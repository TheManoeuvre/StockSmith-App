"""ABC classification: resolving an item's tier and count cadence, and deciding what's due.

Two three-level fallbacks, one for the tier and one for the cadence it implies. Both are
resolved most-specific-first and both treat NULL as "inherit", never as "unset":

    tier      = item.abc_class ?? category/type tier ?? shop-wide baseline for this scope
    cadence   = item.stock_take_interval_days ?? tier override for this scope ?? code default

Nothing outside this module should reimplement either order. Every caller that has more
than one item to resolve should load `Rules` once and reuse it — the rule set is four
small queries and then resolution is pure Python, which is what keeps a catalogue-wide
overdue sweep from turning into N+1.

Materials take their middle level from the `category` enum rather than from
material_types; the reason is coverage, and it's written up on MaterialCategoryABC.

Scope of "an item" here follows where stock actually lives (see routers/products.py's
active_variant_stock_totals_by_product): a product with active variants is counted as its
variants, a product without them is counted as itself, and bundles are counted as neither
because their quantity is derived from their components rather than held.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.abc_classification import ABCClass, ABCScope, ABCTierSetting, ProductTypeABC
from app.models.material import Material, MaterialCategory, MaterialCategoryABC
from app.models.product import Product
from app.models.variant import ProductVariant
from app.services.general_settings import get_general_settings
from app.services.platforms.base import ensure_utc

# Shipped cadences, in days. Deliberately code constants rather than rows seeded by a
# migration: seeded values fork from these the moment they're written, so improving one
# later would reach only fresh installs. abc_tier_settings holds overrides and nothing
# else — see ABCTierSetting.
_DEFAULT_INTERVAL_DAYS: dict[ABCClass, int] = {
    ABCClass.A: 30,
    ABCClass.B: 60,
    ABCClass.C: 90,
}


@dataclass(frozen=True)
class Resolved:
    """An item's effective tier and cadence, plus where each came from.

    The two `*_source` fields exist for the UI: "C, inherited from Packaging" is
    actionable in a way a bare "C" isn't, because it tells you which level to go and edit.
    """

    abc_class: ABCClass
    interval_days: int
    class_source: str  # "item" | "group" | "default"
    interval_source: str  # "item" | "tier" | "default"


@dataclass(frozen=True)
class Rules:
    """Everything needed to resolve any item, loaded once.

    Frozen and I/O-free by construction: once built, resolution can't quietly issue
    another query per item.
    """

    baselines: dict[ABCScope, ABCClass]
    category_tiers: dict[MaterialCategory, ABCClass]
    product_type_tiers: dict[int, ABCClass]
    tier_intervals: dict[tuple[ABCScope, ABCClass], int]

    def _resolve(
        self,
        scope: ABCScope,
        own_class: ABCClass | None,
        group_class: ABCClass | None,
        own_interval: int | None,
    ) -> Resolved:
        if own_class is not None:
            abc_class, class_source = own_class, "item"
        elif group_class is not None:
            abc_class, class_source = group_class, "group"
        else:
            abc_class, class_source = self.baselines[scope], "default"

        if own_interval is not None:
            interval, interval_source = own_interval, "item"
        elif (override := self.tier_intervals.get((scope, abc_class))) is not None:
            interval, interval_source = override, "tier"
        else:
            interval, interval_source = _DEFAULT_INTERVAL_DAYS[abc_class], "default"

        return Resolved(abc_class, interval, class_source, interval_source)

    def for_material(self, material: Material) -> Resolved:
        return self._resolve(
            ABCScope.material,
            material.abc_class,
            self.category_tiers.get(material.category),
            material.stock_take_interval_days,
        )

    def for_product(self, product: Product) -> Resolved:
        """A variant resolves through its parent product — variants hold their own stock
        and their own count date, but not their own tier. Pass the parent here."""
        group = (
            self.product_type_tiers.get(product.product_type_id)
            if product.product_type_id is not None
            else None
        )
        return self._resolve(ABCScope.product, product.abc_class, group, product.stock_take_interval_days)


async def load_rules(session: AsyncSession) -> Rules:
    settings = await get_general_settings(session)
    category_rows = (await session.execute(select(MaterialCategoryABC))).scalars()
    product_type_rows = (await session.execute(select(ProductTypeABC))).scalars()
    tier_rows = (await session.execute(select(ABCTierSetting))).scalars()
    return Rules(
        baselines={
            ABCScope.material: settings.default_material_abc_class,
            ABCScope.product: settings.default_product_abc_class,
        },
        category_tiers={row.category: row.abc_class for row in category_rows},
        product_type_tiers={row.product_type_id: row.abc_class for row in product_type_rows},
        tier_intervals={(row.scope, row.tier): row.interval_days for row in tier_rows},
    )


@dataclass(frozen=True)
class DueState:
    last_stock_take_at: datetime | None
    next_due_at: datetime | None  # None when never counted — nothing to count forward from
    days_overdue: int | None  # None when never counted; 0 on the day it falls due
    is_due: bool


def due_state(last_stock_take_at: datetime | None, interval_days: int, now: datetime) -> DueState:
    """Whether an item wants counting, and by how long it's been waiting.

    Never-counted is its own state rather than "infinitely overdue": there is no date to
    measure from, and reporting a made-up number would rank it against genuinely overdue
    items on a scale it isn't on. It's always due, and the caller sorts it first.
    """
    last = ensure_utc(last_stock_take_at)
    if last is None:
        return DueState(None, None, None, is_due=True)
    next_due = last + timedelta(days=interval_days)
    days_overdue = (now - next_due).days
    return DueState(last, next_due, max(days_overdue, 0), is_due=now >= next_due)


@dataclass(frozen=True)
class DueForCountItem:
    scope: ABCScope
    material_id: int | None
    product_id: int | None
    variant_id: int | None
    name: str
    abc_class: ABCClass
    interval_days: int
    last_stock_take_at: datetime | None
    days_overdue: int | None


def _sort_key(item: DueForCountItem) -> tuple:
    # Never-counted first (nothing is more overdue than never), then longest-waiting, then
    # name so the order is stable between calls rather than dependent on row order.
    return (item.days_overdue is not None, -(item.days_overdue or 0), item.name.lower())


async def compute_due_for_count(session: AsyncSession, now: datetime | None = None) -> list[DueForCountItem]:
    """Every active item whose cadence says it's due, most overdue first.

    `now` is injectable so tests can age an item without sleeping.
    """
    now = now or datetime.now(timezone.utc)
    rules = await load_rules(session)
    due: list[DueForCountItem] = []

    materials = (
        await session.execute(select(Material).where(Material.is_active.is_(True)).order_by(Material.name))
    ).scalars()
    for material in materials:
        resolved = rules.for_material(material)
        state = due_state(material.last_stock_take_at, resolved.interval_days, now)
        if state.is_due:
            due.append(
                DueForCountItem(
                    scope=ABCScope.material,
                    material_id=material.id,
                    product_id=None,
                    variant_id=None,
                    name=material.name,
                    abc_class=resolved.abc_class,
                    interval_days=resolved.interval_days,
                    last_stock_take_at=state.last_stock_take_at,
                    days_overdue=state.days_overdue,
                )
            )

    # Bundles hold no stock of their own (see ProductBundleItem) so there is nothing to
    # count for them; their ready_to_ship follows from whatever their components have.
    products = list(
        (
            await session.execute(
                select(Product)
                .where(Product.is_active.is_(True), Product.is_bundle.is_(False))
                .order_by(Product.name)
            )
        ).scalars()
    )
    variants_by_product: dict[int, list[ProductVariant]] = {}
    if products:
        variant_rows = (
            await session.execute(
                select(ProductVariant).where(
                    ProductVariant.is_active.is_(True),
                    ProductVariant.product_id.in_([p.id for p in products]),
                )
            )
        ).scalars()
        for variant in variant_rows:
            variants_by_product.setdefault(variant.product_id, []).append(variant)

    for product in products:
        resolved = rules.for_product(product)
        variants = variants_by_product.get(product.id, [])
        # One line per stock-holding row: the variants when there are any, else the
        # product itself. A product with active variants never accumulates its own
        # current_stock, so counting it as well would be counting a number nothing writes.
        owners: list[tuple[int | None, str, datetime | None]] = (
            [(v.id, f"{product.name} — {v.variant_name}", v.last_stock_take_at) for v in variants]
            if variants
            else [(None, product.name, product.last_stock_take_at)]
        )
        for variant_id, name, last_at in owners:
            state = due_state(last_at, resolved.interval_days, now)
            if state.is_due:
                due.append(
                    DueForCountItem(
                        scope=ABCScope.product,
                        material_id=None,
                        product_id=product.id,
                        variant_id=variant_id,
                        name=name,
                        abc_class=resolved.abc_class,
                        interval_days=resolved.interval_days,
                        last_stock_take_at=state.last_stock_take_at,
                        days_overdue=state.days_overdue,
                    )
                )

    due.sort(key=_sort_key)
    return due
