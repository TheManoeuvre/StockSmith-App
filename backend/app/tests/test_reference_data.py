"""Rename, delete and merge for the lookup tables.

The rename tests are the ones that answer the original question — "does renaming Bambu Lab to
Bamboo Lab reach the materials that use it?" — and the answer is that it does so by
construction, because these are foreign keys and the name was never copied anywhere.
"""

import pytest

from app.models.colour import Colour
from app.models.manufacturer import Manufacturer
from app.models.material import Material, MaterialCategory, MaterialUnit
from app.models.material_type import MaterialType
from app.models.purchase import Purchase
from app.models.supplier import Supplier
from app.services import reference_data
from app.services.reference_data import InUseError, NameConflictError, ReferenceDataError


async def _material(session, name: str, **fks) -> Material:
    material = Material(
        name=name,
        category=MaterialCategory.filament,
        unit=MaterialUnit.g,
        current_qty=100,
        **fks,
    )
    session.add(material)
    await session.commit()
    await session.refresh(material)
    return material


async def _manufacturer(session, name: str) -> Manufacturer:
    row = Manufacturer(name=name)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


class TestRenameCascades:
    async def test_renaming_reaches_every_material_that_uses_it(self, session):
        """The question the whole reference-data piece started from.

        No fan-out update anywhere: `materials.manufacturer_id` is a foreign key and the name is
        read back through the relationship, never copied onto the material. Renaming the row is
        the entire operation.
        """
        maker = await _manufacturer(session, "Bambu Lab")
        material = await _material(session, "PLA Black", manufacturer_id=maker.id)

        await reference_data.rename(session, Manufacturer, maker.id, "Bamboo Lab")

        await session.refresh(material, ["manufacturer"])
        assert material.manufacturer_name == "Bamboo Lab"

    async def test_renaming_trims_whitespace(self, session):
        maker = await _manufacturer(session, "Prusa")
        renamed = await reference_data.rename(session, Manufacturer, maker.id, "  Prusa Research  ")
        assert renamed.name == "Prusa Research"

    async def test_an_empty_name_is_refused(self, session):
        maker = await _manufacturer(session, "Prusa")
        with pytest.raises(ReferenceDataError):
            await reference_data.rename(session, Manufacturer, maker.id, "   ")

    async def test_renaming_onto_an_existing_name_is_refused_with_the_clashing_id(self, session):
        """Refused rather than silently merged — but the error carries the other row's id, so the
        UI can offer merging as the obvious next step instead of a dead end."""
        keep = await _manufacturer(session, "Prusa")
        other = await _manufacturer(session, "Bambu Lab")

        with pytest.raises(NameConflictError) as caught:
            await reference_data.rename(session, Manufacturer, other.id, "Prusa")

        assert caught.value.existing_id == keep.id

    async def test_renaming_to_its_own_name_is_a_no_op_not_a_conflict(self, session):
        maker = await _manufacturer(session, "Prusa")
        renamed = await reference_data.rename(session, Manufacturer, maker.id, "Prusa")
        assert renamed.name == "Prusa"

    async def test_other_columns_can_be_set_alongside(self, session):
        maker = await _manufacturer(session, "Prusa")
        renamed = await reference_data.rename(
            session, Manufacturer, maker.id, "Prusa", website_url="https://prusa3d.com"
        )
        assert renamed.website_url == "https://prusa3d.com"

    async def test_a_column_can_be_cleared_by_passing_none(self, session):
        """Passing None means "empty this", not "leave it alone".

        It used to mean the latter, which made a website URL impossible to remove once typed —
        the only way back was editing the database. Whether a field was mentioned at all is
        decided one layer up, by patch_row's exclude_unset.
        """
        maker = await _manufacturer(session, "Prusa")
        await reference_data.rename(session, Manufacturer, maker.id, "Prusa", website_url="https://prusa3d.com")
        cleared = await reference_data.rename(session, Manufacturer, maker.id, "Prusa", website_url=None)
        assert cleared.website_url is None


class TestCaseInsensitiveNames:
    async def test_renaming_a_colour_to_a_different_casing_of_an_existing_one_conflicts(self, session):
        """Colour's find-or-create already folds case, so its conflict check has to as well.

        Without this the two halves disagree: the rename is allowed, "Black" and "black" both
        exist, and the next case-insensitive lookup matches two rows and raises
        MultipleResultsFound — the exact duplication the colours migration was written to undo.
        """
        session.add_all([Colour(name="black"), Colour(name="red")])
        await session.commit()
        red = (await reference_data._find_by_name(session, Colour, "red"))

        with pytest.raises(NameConflictError):
            await reference_data.rename(session, Colour, red.id, "BLACK")

    async def test_exact_matching_still_applies_to_the_other_tables(self, session):
        """Manufacturers were always chosen from a list, so case is a real distinction there
        and folding it would refuse a legitimate rename."""
        await _manufacturer(session, "prusa")
        other = await _manufacturer(session, "Bambu Lab")
        renamed = await reference_data.rename(session, Manufacturer, other.id, "Prusa")
        assert renamed.name == "Prusa"


class TestUsageCounts:
    async def test_counts_across_every_referencing_table(self, session):
        """Suppliers are reachable from two directions. A count that only looked at materials
        would let you delete one that a purchase still points at."""
        supplier = Supplier(name="Filamentive")
        session.add(supplier)
        await session.commit()
        await session.refresh(supplier)

        await _material(session, "PLA", default_supplier_id=supplier.id)
        await _material(session, "PETG", default_supplier_id=supplier.id)
        session.add(Purchase(supplier_id=supplier.id))
        await session.commit()

        counts = await reference_data.usage_counts(session, Supplier)
        assert counts[supplier.id] == 3

    async def test_unused_entries_are_absent_rather_than_zero(self, session):
        maker = await _manufacturer(session, "Unused")
        assert (await reference_data.usage_counts(session, Manufacturer)).get(maker.id) is None

    async def test_describe_usage_reads_as_a_sentence(self, session):
        supplier = Supplier(name="Filamentive")
        session.add(supplier)
        await session.commit()
        await session.refresh(supplier)
        await _material(session, "PLA", default_supplier_id=supplier.id)
        session.add(Purchase(supplier_id=supplier.id))
        await session.commit()

        assert await reference_data.describe_usage(session, Supplier, supplier.id) == "1 material and 1 purchase"

    async def test_describe_usage_pluralises(self, session):
        maker = await _manufacturer(session, "Bambu Lab")
        await _material(session, "PLA", manufacturer_id=maker.id)
        await _material(session, "PETG", manufacturer_id=maker.id)

        assert await reference_data.describe_usage(session, Manufacturer, maker.id) == "2 materials"


class TestDelete:
    async def test_deletes_an_unused_entry(self, session):
        maker = await _manufacturer(session, "Typo Lba")
        await reference_data.delete_if_unused(session, Manufacturer, maker.id)
        assert await session.get(Manufacturer, maker.id) is None

    async def test_refuses_when_still_referenced(self, session):
        """Not leaning on ON DELETE SET NULL, which every one of these FKs has. Letting the
        database do it would blank the manufacturer on a dozen materials — data loss that looks
        exactly like success."""
        maker = await _manufacturer(session, "Bambu Lab")
        material = await _material(session, "PLA", manufacturer_id=maker.id)

        with pytest.raises(InUseError, match="1 material"):
            await reference_data.delete_if_unused(session, Manufacturer, maker.id)

        await session.refresh(material)
        assert material.manufacturer_id == maker.id


class TestMerge:
    async def test_repoints_every_referencing_table_then_deletes_the_source(self, session):
        """The cure for what find-or-create accumulates: a CSV import spelling it two ways
        leaves two rows meaning one thing."""
        keep = Supplier(name="Filamentive")
        dupe = Supplier(name="filamentive")
        session.add_all([keep, dupe])
        await session.commit()
        await session.refresh(keep)
        await session.refresh(dupe)

        material = await _material(session, "PLA", default_supplier_id=dupe.id)
        purchase = Purchase(supplier_id=dupe.id)
        session.add(purchase)
        await session.commit()

        target = await reference_data.merge(session, Supplier, dupe.id, keep.id)

        assert target.id == keep.id
        assert await session.get(Supplier, dupe.id) is None
        await session.refresh(material)
        await session.refresh(purchase)
        assert material.default_supplier_id == keep.id
        assert purchase.supplier_id == keep.id

    async def test_merging_into_itself_is_refused(self, session):
        maker = await _manufacturer(session, "Prusa")
        with pytest.raises(ReferenceDataError, match="into itself"):
            await reference_data.merge(session, Manufacturer, maker.id, maker.id)

    async def test_merging_a_missing_entry_is_refused(self, session):
        maker = await _manufacturer(session, "Prusa")
        with pytest.raises(ReferenceDataError):
            await reference_data.merge(session, Manufacturer, 9999, maker.id)

    async def test_the_merged_entry_can_then_be_renamed_and_deleted(self, session):
        """End to end, the flow the UI offers: rename collides, so merge instead, and the
        survivor keeps the usage."""
        keep = await _manufacturer(session, "Bambu Lab")
        dupe = await _manufacturer(session, "bambu lab")
        await _material(session, "PLA", manufacturer_id=dupe.id)

        with pytest.raises(NameConflictError) as caught:
            await reference_data.rename(session, Manufacturer, dupe.id, "Bambu Lab")

        await reference_data.merge(session, Manufacturer, dupe.id, caught.value.existing_id)

        counts = await reference_data.usage_counts(session, Manufacturer)
        assert counts[keep.id] == 1


class TestMaterialTypes:
    async def test_the_same_operations_work_for_material_types(self, session):
        kind = MaterialType(name="Filment")
        session.add(kind)
        await session.commit()
        await session.refresh(kind)
        material = await _material(session, "PLA", material_type_id=kind.id)

        await reference_data.rename(session, MaterialType, kind.id, "Filament")
        await session.refresh(material, ["material_type"])
        assert material.material_type_name == "Filament"

        with pytest.raises(InUseError):
            await reference_data.delete_if_unused(session, MaterialType, kind.id)
