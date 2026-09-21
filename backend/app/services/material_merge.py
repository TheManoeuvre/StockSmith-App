"""Merging one material into another — the fix for the duplicates a CSV or URL import
leaves behind ("2x4 Brick Red" and "Brick 2x4 Red" that are one spool).

Shaped like services/reference_data.merge, and separate from it for the reason that
module's docstring gives: that merge is only safe because no unique constraint spans any
of its foreign keys. Materials are the opposite case. A product's BOM has one line per
material, a kitting list has one row per material, an order reserves one allocation per
material — so repointing every reference from A to B collides wherever a row already
exists for B. Each such table gets a collision rule here, and the rule is nearly always
"add the quantities": two BOM lines A×2 and B×3 on one product mean the product needs 5
of the merged material.

Stock and cost need no arithmetic of their own. `current_qty` and `avg_unit_cost` are
derived by replaying the purchase receipts and adjustments (services/costing), so once
those rows point at B, `recompute_material(B)` interleaves both histories by date and
yields the right quantity and a genuinely weighted average. `allocated_qty` is the one
figure the replay does not cover, and is summed by hand.

The source row is deleted, not deactivated. Everything that pointed at it now points at
the target, so there is nothing left for it to be the history of.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.base import Base
from app.models.build import BuildFailedConsumption
from app.models.kitting import (
    DefaultKittingMaterial,
    OrderKittingAllocation,
    OrderKittingOverride,
    ProductKittingMaterial,
    ProductVariantKittingMaterial,
)
from app.models.material import Material, MaterialAdjustment
from app.models.material_substitute import MaterialSubstitute, MaterialSubstituteUsage
from app.models.notification import NotificationAlertState
from app.models.order_parcel import OrderReplacementParcelItem
from app.models.order_return import OrderLineReturn
from app.models.product import ProductMaterial
from app.models.purchase import MaterialPurchase
from app.models.stock_take import StockTake, StockTakeLine, StockTakeStatus
from app.models.variant import ProductVariantMaterial
from app.services import file_storage
from app.services.costing import recompute_material
from app.services.pricing import check_and_snapshot_for_materials
from app.services.reference_data import InUseError, ReferenceDataError


@dataclass(frozen=True)
class _Rule:
    """How one referencing table is repointed.

    `key` names the other columns that, with the material column, form the table's unique
    key; a source row whose key already exists for the target is folded into that row by
    adding `sums` and deleting the source. No key means no unique constraint and a plain
    UPDATE.
    """

    model: type[Base]
    column: InstrumentedAttribute
    label: str
    key: tuple[str, ...] = ()
    sums: tuple[str, ...] = ()
    # A second material column on the same table (substitutions). Repointed too, after
    # which a row pointing at the target on both sides is a self-reference and is dropped.
    paired_column: InstrumentedAttribute | None = None


_RULES: tuple[_Rule, ...] = (
    _Rule(ProductMaterial, ProductMaterial.material_id, "product BOM lines", ("product_id",), ("qty_required",)),
    _Rule(
        ProductKittingMaterial,
        ProductKittingMaterial.material_id,
        "product kitting lines",
        ("product_id",),
        ("qty_required",),
    ),
    _Rule(DefaultKittingMaterial, DefaultKittingMaterial.material_id, "default kitting lines", (), ("qty_required",)),
    _Rule(
        ProductVariantMaterial,
        ProductVariantMaterial.material_id,
        "variant BOM overrides",
        ("variant_id", "replaces_material_id"),
        ("qty_required",),
        paired_column=ProductVariantMaterial.replaces_material_id,
    ),
    _Rule(
        ProductVariantKittingMaterial,
        ProductVariantKittingMaterial.material_id,
        "variant kitting overrides",
        ("variant_id",),
        ("qty_required",),
        paired_column=ProductVariantKittingMaterial.replaces_material_id,
    ),
    _Rule(
        OrderKittingOverride,
        OrderKittingOverride.material_id,
        "order kitting overrides",
        ("order_id",),
        ("qty_required",),
        paired_column=OrderKittingOverride.replaces_material_id,
    ),
    _Rule(
        OrderKittingAllocation,
        OrderKittingAllocation.material_id,
        "order kitting allocations",
        ("order_id",),
        ("reserved_qty", "consumed_qty"),
    ),
    _Rule(MaterialPurchase, MaterialPurchase.material_id, "purchase lines"),
    _Rule(MaterialAdjustment, MaterialAdjustment.material_id, "stock adjustments"),
    _Rule(
        MaterialSubstitute,
        MaterialSubstitute.material_id,
        "substitute rules",
        ("substitute_material_id",),
        paired_column=MaterialSubstitute.substitute_material_id,
    ),
    _Rule(
        MaterialSubstituteUsage,
        MaterialSubstituteUsage.material_id,
        "substitution records",
        paired_column=MaterialSubstituteUsage.substitute_material_id,
    ),
    _Rule(
        BuildFailedConsumption,
        BuildFailedConsumption.material_id,
        "failed-build consumptions",
        ("build_id",),
        ("qty_consumed",),
    ),
    _Rule(StockTakeLine, StockTakeLine.material_id, "stock take lines", ("stock_take_id",), ("expected_qty", "counted_qty")),
    _Rule(OrderLineReturn, OrderLineReturn.material_id, "return decisions"),
    _Rule(OrderReplacementParcelItem, OrderReplacementParcelItem.material_id, "replacement parcel items"),
)

# Scalar columns the target inherits from the source when it has none of its own.
_FILL_IF_EMPTY = (
    "barcode",
    "product_url",
    "default_supplier_id",
    "manufacturer_id",
    "colour_id",
    "material_type_id",
    "typical_reorder_qty",
    "abc_class",
    "stock_take_interval_days",
)


@dataclass
class TableEffect:
    label: str
    repointed: int
    summed: int  # rows folded into an existing target row, or dropped as now self-referential


@dataclass
class MaterialMergePlan:
    source: Material
    target: Material
    effects: list[TableEffect]
    combined_qty: Decimal
    blockers: list[str] = field(default_factory=list)


async def _load_pair(session: AsyncSession, source_id: int, target_id: int) -> tuple[Material, Material]:
    if source_id == target_id:
        raise ReferenceDataError("Cannot merge a material into itself.")
    source = await session.get(Material, source_id)
    target = await session.get(Material, target_id)
    if source is None or target is None:
        raise ReferenceDataError("Material not found.")
    if source.unit != target.unit:
        raise ReferenceDataError(
            f'"{source.name}" is counted in {source.unit.value} and "{target.name}" in {target.unit.value} — '
            "quantities in different units cannot be added together."
        )
    if not target.is_active:
        raise ReferenceDataError(f'"{target.name}" is deactivated — reactivate it before merging into it.')
    return source, target


async def _rows_pointing_at(session: AsyncSession, rule: _Rule, material_id: int) -> list:
    condition = rule.column == material_id
    if rule.paired_column is not None:
        condition = condition | (rule.paired_column == material_id)
    return list((await session.execute(select(rule.model).where(condition))).scalars())


def _identity(row, rule: _Rule) -> tuple:
    """The row's position under its table's unique key — what a repointed row would
    collide on."""
    return (getattr(row, rule.column.key),) + tuple(getattr(row, k) for k in rule.key)


def _add(row, attr: str, value) -> None:
    if value is None:
        return
    current = getattr(row, attr)
    setattr(row, attr, (Decimal(current) if current is not None else Decimal(0)) + Decimal(value))


async def _fold(session: AsyncSession, rule: _Rule, source_id: int, target_id: int, *, apply: bool) -> TableEffect:
    """Repoints one table's rows from source to target, folding a row into an existing
    target row wherever the unique key would otherwise collide. With apply=False only
    counts."""
    source_rows = await _rows_pointing_at(session, rule, source_id)
    if not source_rows:
        return TableEffect(rule.label, 0, 0)
    collidable = bool(rule.key) or bool(rule.sums)
    taken = {_identity(r, rule): r for r in await _rows_pointing_at(session, rule, target_id)} if collidable else {}

    repointed = summed = 0
    for row in source_rows:
        # What the row becomes once both material columns are repointed.
        after = {rule.column.key: getattr(row, rule.column.key)}
        if rule.paired_column is not None:
            after[rule.paired_column.key] = getattr(row, rule.paired_column.key)
        for col, value in after.items():
            if value == source_id:
                after[col] = target_id
        identity = (after[rule.column.key],) + tuple(after.get(k, getattr(row, k)) for k in rule.key)

        existing = taken.get(identity) if collidable else None
        if existing is not None and existing is not row:
            summed += 1
            if apply:
                for attr in rule.sums:
                    _add(existing, attr, getattr(row, attr))
                await session.delete(row)
            continue

        # A substitution that now names the target on both sides says nothing and would
        # violate the table's CHECK the moment it flushed; usage records have no CHECK and
        # stay as history.
        self_reference = (
            rule.paired_column is not None
            and rule.model is not MaterialSubstituteUsage
            and len(set(after.values())) == 1
        )
        if self_reference:
            summed += 1
            if apply:
                await session.delete(row)
            continue

        repointed += 1
        if apply:
            for col, value in after.items():
                setattr(row, col, value)
        if collidable:
            taken[identity] = row

    if apply:
        await session.flush()
    return TableEffect(rule.label, repointed, summed)


async def _open_take_blocker(session: AsyncSession, source: Material, target: Material) -> str | None:
    result = await session.execute(
        select(StockTakeLine.material_id)
        .join(StockTake, StockTake.id == StockTakeLine.stock_take_id)
        .where(StockTake.status == StockTakeStatus.open, StockTakeLine.material_id.in_([source.id, target.id]))
    )
    if result.first() is not None:
        return "One of these materials is on an open stock take. Approve or cancel the count before merging."
    return None


async def plan_merge(session: AsyncSession, source_id: int, target_id: int) -> MaterialMergePlan:
    """What merging `source_id` into `target_id` would touch. Reads only."""
    source, target = await _load_pair(session, source_id, target_id)
    effects = [await _fold(session, rule, source.id, target.id, apply=False) for rule in _RULES]
    blockers = [b for b in [await _open_take_blocker(session, source, target)] if b]
    return MaterialMergePlan(
        source=source,
        target=target,
        effects=[e for e in effects if e.repointed or e.summed],
        combined_qty=Decimal(source.current_qty) + Decimal(target.current_qty),
        blockers=blockers,
    )


async def merge(session: AsyncSession, source_id: int, target_id: int) -> Material:
    """Repoint everything from `source_id` to `target_id`, fold the stock and cost
    histories together, and delete the source."""
    plan = await plan_merge(session, source_id, target_id)
    if plan.blockers:
        raise InUseError(" ".join(plan.blockers))
    source, target = plan.source, plan.target

    for rule in _RULES:
        await _fold(session, rule, source.id, target.id, apply=True)

    # Alert state is keyed by stringified id, not a FK; the target's own state stands.
    await session.execute(delete(NotificationAlertState).where(NotificationAlertState.entity_key == str(source.id)))

    for attr in _FILL_IF_EMPTY:
        if getattr(target, attr) is None and getattr(source, attr) is not None:
            setattr(target, attr, getattr(source, attr))

    source_image = source.image_path
    if source_image and not target.image_path:
        target.image_path = source_image
        target.image_original_filename = source.image_original_filename
        source_image = None  # moved, not to be deleted

    # The histories now both point at the target; one replay gives the combined quantity
    # and the properly weighted cost. allocated_qty is not part of the replay.
    await recompute_material(session, target.id)
    target.allocated_qty = Decimal(target.allocated_qty) + Decimal(source.allocated_qty)
    if target.last_stock_take_at is None or (
        source.last_stock_take_at is not None and source.last_stock_take_at > target.last_stock_take_at
    ):
        # The merged stock was last verified when the later of the two counts happened —
        # arguably never, but "never" would drag it straight onto the overdue list.
        target.last_stock_take_at = source.last_stock_take_at or target.last_stock_take_at
        target.last_stock_take_id = source.last_stock_take_id if source.last_stock_take_at else target.last_stock_take_id
    source.last_stock_take_id = None

    await session.delete(source)
    await session.flush()
    await check_and_snapshot_for_materials(session, {target.id})
    await session.commit()

    if source_image:
        file_storage.delete_asset_file(source_image)

    await session.refresh(target)
    return target
