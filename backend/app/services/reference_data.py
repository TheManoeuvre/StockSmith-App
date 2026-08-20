"""Rename, delete and merge for the small lookup tables — manufacturers, suppliers, material
types, and anything else shaped like them.

One generic module rather than the same forty lines copied into three routers, which is what the
three near-identical route files these replace had already become.

Worth knowing why renaming needs no special machinery: these are real foreign keys
(`materials.manufacturer_id`, `purchases.supplier_id`, `materials.material_type_id`), and the
name is only ever read back through the relationship — never copied onto the referencing row.
So changing `manufacturers.name` changes it everywhere it appears, by construction. There is no
fan-out update to write and no data to migrate. The gap was only ever that no endpoint existed
to change it.
"""

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.models.base import Base
from app.models.manufacturer import Manufacturer
from app.models.material import Material
from app.models.colour import Colour
from app.models.material_category import MaterialCategory
from app.models.material_type import MaterialType
from app.models.order import Order
from app.models.product import Product
from app.models.product_category import ProductCategory
from app.models.purchase import Purchase
from app.models.shipping_profile import ShippingProfile
from app.models.supplier import Supplier
from app.models.variant import ProductVariant


class ReferenceDataError(RuntimeError):
    """Refused. The message is user-facing."""


class NameConflictError(ReferenceDataError):
    """Another row already has this name. Carries the winner's id so the caller can offer a
    merge instead of just saying no."""

    def __init__(self, message: str, existing_id: int):
        super().__init__(message)
        self.existing_id = existing_id


class InUseError(ReferenceDataError):
    """Still referenced, so deleting would silently blank those references."""


@dataclass(frozen=True)
class Reference:
    """One foreign key pointing at a reference table, and what to call the things holding it."""

    column: InstrumentedAttribute
    noun_singular: str
    noun_plural: str
    # Repointing this reference would rewrite history rather than tidy up a duplicate. Orders are
    # the case: an order shipped under a profile is a record of what happened, not a preference
    # that can be restated. A merge touching one of these is refused outright.
    historical: bool = False


# Every FK into each reference table. Keep this current when a new referencing column is added —
# a missing entry means usage counts under-report and `delete_if_unused` deletes something that
# was in use. Most of these FKs are ON DELETE SET NULL, so the database accepts that happily and
# it fails silently rather than loudly. Material.category_id is the exception: it is RESTRICT,
# because a material with no category isn't a state the app models, so there the database
# refuses instead.
REFERENCES: dict[type[Base], Sequence[Reference]] = {
    Manufacturer: (Reference(Material.manufacturer_id, "material", "materials"),),
    Supplier: (
        Reference(Material.default_supplier_id, "material", "materials"),
        Reference(Purchase.supplier_id, "purchase", "purchases"),
    ),
    MaterialType: (Reference(Material.material_type_id, "material", "materials"),),
    # product_category_abc.product_category_id and material_category_abc.category_id are second
    # FKs into product_categories and material_categories, and are deliberately not listed.
    # Each holds that row's ABC tier, which is an attribute of the type or category rather
    # than a use of it — counting them here would refuse to delete an unused one purely
    # because someone had once set its tier. Both FKs are ON DELETE CASCADE, so the
    # assignment goes with the row rather than being left dangling.
    ProductCategory: (Reference(Product.product_category_id, "product", "products"),),
    MaterialCategory: (Reference(Material.category_id, "material", "materials"),),
    Colour: (Reference(Material.colour_id, "material", "materials"),),
    ShippingProfile: (
        Reference(Product.shipping_profile_id, "product", "products"),
        Reference(ProductVariant.shipping_profile_id, "variant", "variants"),
        Reference(Order.shipping_profile_id, "order", "orders", historical=True),
    ),
}


def _references_for(model: type[Base]) -> Sequence[Reference]:
    try:
        return REFERENCES[model]
    except KeyError as exc:  # pragma: no cover — a programming error, not a user-facing one
        raise ReferenceDataError(f"No reference map registered for {model.__name__}") from exc


async def usage_counts(session: AsyncSession, model: type[Base]) -> dict[int, int]:
    """How many rows point at each entry, keyed by id.

    One grouped aggregate per referencing table — two or three queries over tables that hold a
    few dozen rows. Always computed rather than hidden behind a `?with_usage=` flag: the count
    is what makes the delete button's disabled state explainable, so every list needs it.
    """
    totals: dict[int, int] = {}
    for reference in _references_for(model):
        result = await session.execute(
            select(reference.column, func.count())
            .where(reference.column.is_not(None))
            .group_by(reference.column)
        )
        for row_id, count in result:
            totals[row_id] = totals.get(row_id, 0) + count
    return totals


async def describe_usage(session: AsyncSession, model: type[Base], row_id: int) -> str:
    """"3 materials and 1 purchase" — for telling someone why a delete was refused."""
    parts: list[str] = []
    for reference in _references_for(model):
        count = (
            await session.execute(select(func.count()).where(reference.column == row_id))
        ).scalar_one()
        if count:
            parts.append(f"{count} {reference.noun_singular if count == 1 else reference.noun_plural}")
    if not parts:
        return "nothing"
    if len(parts) == 1:
        return parts[0]
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


# Tables whose names are matched without regard to case. The rule follows the data, not the
# table: these are the ones whose `find-or-create` is already case-insensitive, so an
# exact-match conflict check here would let a rename create the very duplicate that
# `find-or-create` refuses to. "Black" and "black" would then both resolve on the next
# case-insensitive lookup and raise MultipleResultsFound.
_CASE_INSENSITIVE_NAMES: frozenset[type[Base]] = frozenset({Colour, MaterialCategory})


async def _find_by_name(session: AsyncSession, model: type[Base], name: str):
    column = func.lower(model.name) if model in _CASE_INSENSITIVE_NAMES else model.name
    target = name.lower() if model in _CASE_INSENSITIVE_NAMES else name
    return (await session.execute(select(model).where(column == target))).scalar_one_or_none()


async def get_or_404(session: AsyncSession, model: type[Base], row_id: int):
    row = await session.get(model, row_id)
    if row is None:
        raise ReferenceDataError(f"No {model.__name__.lower()} with id {row_id}.")
    return row


async def rename(session: AsyncSession, model: type[Base], row_id: int, name: str, **fields):
    """Rename, and set any other plain columns passed as keyword arguments.

    Cascades everywhere for free — see the module docstring.
    """
    row = await get_or_404(session, model, row_id)
    name = name.strip()
    if not name:
        raise ReferenceDataError("Name cannot be empty.")

    if name != row.name:
        clash = await _find_by_name(session, model, name)
        if clash is not None and clash.id != row_id:
            raise NameConflictError(f'Another entry is already called "{name}".', existing_id=clash.id)
    row.name = name

    # Set unconditionally. `None` is a real value here — it is how a website URL or a hex code
    # gets cleared — so it cannot double as "the caller didn't mention this field". That
    # distinction is `patch_row`'s job, which passes only the keys actually present in the
    # request body (see _reference_crud.patch_row). Skipping None here instead made those
    # fields impossible to empty once set, and would silently swallow a boolean set to False.
    for key, value in fields.items():
        setattr(row, key, value)

    await session.commit()
    await session.refresh(row)
    return row


async def delete_if_unused(session: AsyncSession, model: type[Base], row_id: int) -> None:
    """Delete, but only when nothing points at it.

    Deliberately not leaning on `ON DELETE SET NULL`, which every one of these FKs has. Letting
    the database do it would quietly blank the manufacturer on a dozen materials — data loss
    that looks like success. The cascade stays as a backstop for rows deleted by other means.
    """
    row = await get_or_404(session, model, row_id)
    usage = await describe_usage(session, model, row_id)
    if usage != "nothing":
        raise InUseError(
            f'"{row.name}" is still used by {usage}. Merge it into another entry, or change '
            f"those records first."
        )
    await session.delete(row)
    await session.commit()


async def merge(session: AsyncSession, model: type[Base], source_id: int, target_id: int):
    """Repoint everything from one entry to another, then delete the source.

    The fix for the duplicates that `find-or-create` accumulates — a CSV import spelling it
    "Bambu Lab" once and "Bambu lab" the next time leaves two rows that mean one thing.

    Safe to do in bulk: no unique constraint spans any of these foreign keys, so a material that
    somehow referenced both would simply end up referencing the target twice over, which is not
    a state the schema forbids or the app can observe.
    """
    if source_id == target_id:
        raise ReferenceDataError("Cannot merge an entry into itself.")

    source = await get_or_404(session, model, source_id)
    target = await get_or_404(session, model, target_id)

    references = _references_for(model)

    for reference in references:
        if not reference.historical:
            continue
        count = (
            await session.execute(select(func.count()).where(reference.column == source_id))
        ).scalar_one()
        if count:
            raise InUseError(
                f'"{source.name}" is used by {count} '
                f"{reference.noun_singular if count == 1 else reference.noun_plural}, which record what "
                f"actually happened. Merging would rewrite them — archive it instead."
            )

    for reference in references:
        await session.execute(
            update(reference.column.parent.class_)
            .where(reference.column == source_id)
            .values({reference.column.key: target_id})
        )

    await session.delete(source)
    await session.commit()
    await session.refresh(target)
    return target
