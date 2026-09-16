"""ABC classification: resolving an item's tier and count cadence, and deciding what's due.

Two three-level fallbacks, one for the tier and one for the cadence it implies. Both are
resolved most-specific-first and both treat NULL as "inherit", never as "unset":

    tier      = item.abc_class ?? category/type tier ?? shop-wide baseline for this scope
    cadence   = item.stock_take_interval_days ?? tier override for this scope ?? code default

Nothing outside this module should reimplement either order. Every caller that has more
than one item to resolve should load `Rules` once and reuse it — the rule set is four
small queries and then resolution is pure Python, which is what keeps a catalogue-wide
overdue sweep from turning into N+1.

Materials take their middle level from the category rather than from material_types; the
reason is coverage, and it's written up on MaterialCategoryABC.

Scope of "an item" here follows where stock actually lives (see routers/products.py's
active_variant_stock_totals_by_product): a product with active variants is counted as its
variants, a product without them is counted as itself, and bundles are counted as neither
because their quantity is derived from their components rather than held.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.abc_classification import (
    ABCClass,
    ABCScope,
    ABCTierSetting,
    MaterialCategoryABC,
    ProductCategoryABC,
)
from app.models.build import Build
from app.models.material import Material, MaterialAdjustment
from app.models.product import Product
from app.models.purchase import MaterialPurchase, MaterialPurchaseReceipt
from app.models.stock_adjustment import StockAdjustment
from app.models.variant import ProductVariant
from app.schemas.abc import (
    CategoryTier,
    ProductCategoryTier,
    ResolvedClassificationRead,
    StockCountSettingsRead,
    StockCountSettingsUpdate,
    TierInterval,
)
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
    category_tiers: dict[int, ABCClass]
    product_category_tiers: dict[int, ABCClass]
    tier_intervals: dict[tuple[ABCScope, ABCClass], int]
    # Which rows have ever held stock — see ever_stocked_material/ever_stocked_product.
    # Only rows with a stock-moving history entry are listed; a row whose current figure
    # is above zero is stocked by definition and doesn't need to appear here.
    stocked_material_ids: frozenset[int]
    stocked_product_owners: frozenset[tuple[int, int | None]]

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
        # category_id is nullable in the schema but set on every material the app creates;
        # a NULL one simply has no group tier and falls through to the baseline.
        group = self.category_tiers.get(material.category_id) if material.category_id is not None else None
        return self._resolve(
            ABCScope.material,
            material.abc_class,
            group,
            material.stock_take_interval_days,
        )

    def for_product(self, product: Product) -> Resolved:
        """A variant resolves through its parent product — variants hold their own stock
        and their own count date, but not their own tier. Pass the parent here."""
        group = (
            self.product_category_tiers.get(product.product_category_id)
            if product.product_category_id is not None
            else None
        )
        return self._resolve(ABCScope.product, product.abc_class, group, product.stock_take_interval_days)

    def ever_stocked_material(self, material: Material) -> bool:
        """Whether there has ever been anything of this material to count.

        A material that was just created ahead of its first order sits at zero with no
        history, and putting it on the due list would only ever produce a count of
        nothing. One that was received and used down to zero is different: its zero is a
        claim about the shelf, and a claim is exactly what a count checks. The history
        tables tell the two apart; current_qty alone can't.
        """
        return material.current_qty > 0 or material.id in self.stocked_material_ids

    def ever_stocked_product(self, product: Product, variant: ProductVariant | None = None) -> bool:
        """The product-side twin of ever_stocked_material, for whichever row holds the
        stock — the variant when there is one, else the product itself."""
        owner = variant if variant is not None else product
        key = (product.id, variant.id if variant is not None else None)
        return owner.current_stock > 0 or key in self.stocked_product_owners


async def load_rules(session: AsyncSession) -> Rules:
    settings = await get_general_settings(session)
    category_rows = (await session.execute(select(MaterialCategoryABC))).scalars()
    product_category_rows = (await session.execute(select(ProductCategoryABC))).scalars()
    tier_rows = (await session.execute(select(ABCTierSetting))).scalars()
    # Receipts and adjustments are the whole of a material's stock history — they're what
    # recompute_material replays. Builds and adjustments are the product-side equivalent;
    # an order can only ever ship stock one of those put there first.
    stocked_material_ids = (
        await session.execute(
            select(Material.id).where(
                or_(
                    exists().where(MaterialAdjustment.material_id == Material.id),
                    exists().where(
                        MaterialPurchase.material_id == Material.id,
                        MaterialPurchaseReceipt.purchase_line_id == MaterialPurchase.id,
                    ),
                )
            )
        )
    ).scalars()
    stocked_product_owners = (
        await session.execute(
            select(Build.product_id, Build.variant_id).union(
                select(StockAdjustment.product_id, StockAdjustment.variant_id)
            )
        )
    ).all()
    return Rules(
        baselines={
            ABCScope.material: settings.default_material_abc_class,
            ABCScope.product: settings.default_product_abc_class,
        },
        category_tiers={row.category_id: row.abc_class for row in category_rows},
        product_category_tiers={row.product_category_id: row.abc_class for row in product_category_rows},
        tier_intervals={(row.scope, row.tier): row.interval_days for row in tier_rows},
        stocked_material_ids=frozenset(stocked_material_ids),
        stocked_product_owners=frozenset((pid, vid) for pid, vid in stocked_product_owners),
    )


async def read_settings(session: AsyncSession) -> StockCountSettingsRead:
    """The whole configuration, with the shipped defaults filled in where nothing is stored.

    Every tier appears in the intervals lists whether or not it has an override row, each
    flagged with `is_override`. A settings screen has to show a number for all six
    regardless, and computing "what would this be if I don't touch it" belongs here next to
    the defaults rather than being duplicated in the UI.
    """
    rules = await load_rules(session)
    return StockCountSettingsRead(
        default_material_abc_class=rules.baselines[ABCScope.material],
        default_product_abc_class=rules.baselines[ABCScope.product],
        material_tier_intervals=_tier_intervals(rules, ABCScope.material),
        product_tier_intervals=_tier_intervals(rules, ABCScope.product),
        category_tiers=[
            CategoryTier(category_id=category_id, abc_class=abc_class)
            for category_id, abc_class in sorted(rules.category_tiers.items())
        ],
        product_category_tiers=[
            ProductCategoryTier(product_category_id=type_id, abc_class=abc_class)
            for type_id, abc_class in sorted(rules.product_category_tiers.items())
        ],
    )


def _tier_intervals(rules: Rules, scope: ABCScope) -> list[TierInterval]:
    out = []
    for tier in ABCClass:
        override = rules.tier_intervals.get((scope, tier))
        out.append(
            TierInterval(
                tier=tier,
                interval_days=override if override is not None else _DEFAULT_INTERVAL_DAYS[tier],
                is_override=override is not None,
            )
        )
    return out


async def write_settings(session: AsyncSession, payload: StockCountSettingsUpdate) -> StockCountSettingsRead:
    """Replace the configuration wholesale.

    Delete-then-insert for the three sparse tables rather than a diff: they hold at most a
    couple of dozen rows between them, and "absent from the payload means cleared" is the
    only reading under which un-assigning a category's tier is expressible at all.

    An interval equal to the shipped default is still stored when the client marks it an
    override, and dropped when it doesn't. That keeps "I chose 90" distinguishable from "I
    left it alone" — the first should survive a later change to the defaults, the second
    should follow it.
    """
    settings = await get_general_settings(session)
    settings.default_material_abc_class = payload.default_material_abc_class
    settings.default_product_abc_class = payload.default_product_abc_class

    await session.execute(delete(ABCTierSetting))
    await session.execute(delete(MaterialCategoryABC))
    await session.execute(delete(ProductCategoryABC))

    for scope, intervals in (
        (ABCScope.material, payload.material_tier_intervals),
        (ABCScope.product, payload.product_tier_intervals),
    ):
        for entry in intervals:
            if entry.is_override:
                session.add(ABCTierSetting(scope=scope, tier=entry.tier, interval_days=entry.interval_days))

    for category_tier in payload.category_tiers:
        session.add(MaterialCategoryABC(category_id=category_tier.category_id, abc_class=category_tier.abc_class))
    for type_tier in payload.product_category_tiers:
        session.add(ProductCategoryABC(product_category_id=type_tier.product_category_id, abc_class=type_tier.abc_class))

    await session.commit()
    return await read_settings(session)


@dataclass(frozen=True)
class DueState:
    last_stock_take_at: datetime | None
    next_due_at: datetime | None  # None when never counted — nothing to count forward from
    days_overdue: int | None  # None when never counted; 0 on the day it falls due
    is_due: bool


def due_state(
    last_stock_take_at: datetime | None, interval_days: int, now: datetime, *, ever_stocked: bool = True
) -> DueState:
    """Whether an item wants counting, and by how long it's been waiting.

    Never-counted is its own state rather than "infinitely overdue": there is no date to
    measure from, and reporting a made-up number would rank it against genuinely overdue
    items on a scale it isn't on. It's always due, and the caller sorts it first — unless
    it has never held stock either, in which case there is nothing to count yet and it
    waits until something arrives. Once counted, the cadence applies regardless of stock:
    a row that came in and went back to zero is exactly the kind of figure a count is for.
    """
    last = ensure_utc(last_stock_take_at)
    if last is None:
        return DueState(None, None, None, is_due=ever_stocked)
    next_due = last + timedelta(days=interval_days)
    days_overdue = (now - next_due).days
    return DueState(last, next_due, max(days_overdue, 0), is_due=now >= next_due)


def describe(
    resolved: Resolved,
    last_stock_take_at: datetime | None,
    now: datetime | None = None,
    *,
    ever_stocked: bool = True,
):
    """Fold a resolution and a count date into the shape a detail page renders.

    Here rather than in the routers so the two callers (materials, products) can't drift
    on what "due" means, and so the UI never has to reimplement the fallback order in
    TypeScript to work out where a value came from.
    """
    state = due_state(
        last_stock_take_at, resolved.interval_days, now or datetime.now(timezone.utc), ever_stocked=ever_stocked
    )
    return ResolvedClassificationRead(
        abc_class=resolved.abc_class,
        interval_days=resolved.interval_days,
        class_source=resolved.class_source,
        interval_source=resolved.interval_source,
        last_stock_take_at=state.last_stock_take_at,
        next_due_at=state.next_due_at,
        days_overdue=state.days_overdue,
        is_due=state.is_due,
    )


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
        state = due_state(
            material.last_stock_take_at,
            resolved.interval_days,
            now,
            ever_stocked=rules.ever_stocked_material(material),
        )
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
    # Made-to-order products are never held either, so they are never due — and being
    # excluded here takes them off the dashboard's due list too, which reads through this.
    products = list(
        (
            await session.execute(
                select(Product)
                .where(
                    Product.is_active.is_(True),
                    Product.is_bundle.is_(False),
                    Product.made_to_order.is_(False),
                )
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
        owners: list[tuple[ProductVariant | None, str]] = (
            [(v, f"{product.name} — {v.variant_name}") for v in variants] if variants else [(None, product.name)]
        )
        for variant, name in owners:
            last_at = variant.last_stock_take_at if variant is not None else product.last_stock_take_at
            state = due_state(
                last_at,
                resolved.interval_days,
                now,
                ever_stocked=rules.ever_stocked_product(product, variant),
            )
            if state.is_due:
                due.append(
                    DueForCountItem(
                        scope=ABCScope.product,
                        material_id=None,
                        product_id=product.id,
                        variant_id=variant.id if variant is not None else None,
                        name=name,
                        abc_class=resolved.abc_class,
                        interval_days=resolved.interval_days,
                        last_stock_take_at=state.last_stock_take_at,
                        days_overdue=state.days_overdue,
                    )
                )

    due.sort(key=_sort_key)
    return due
