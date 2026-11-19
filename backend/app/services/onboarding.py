"""The setup checklist: where a new install stands against docs/plan-onboarding.md §2.

Every step is worked out from real data, never ticked by hand — a step reading "done" means the
thing exists, so the checklist can't drift from the truth. Titles, wording and links live in the
frontend; this only says which steps exist, in what order, and whether each is done.

Steps are listed in the order they're best done in (docs/help/getting-started.md follows the same
order). Optional steps don't hold the checklist open on their own once dismissed, but they do show
until done, because "connect a store" and "keep a backup elsewhere" are the two that matter most
after the basics and are the easiest to forget.
"""

from dataclasses import dataclass

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.backup_settings import BackupSettings
from app.models.kitting import DefaultKittingMaterial, ProductKittingMaterial
from app.models.material import Material
from app.models.order import Order
from app.models.platform_connection import PlatformConnection
from app.models.product import ProductMaterial
from app.services.general_settings import get_general_settings


@dataclass(frozen=True)
class SetupStep:
    key: str
    done: bool
    optional: bool = False


async def _exists(session: AsyncSession, stmt) -> bool:
    return bool(await session.scalar(select(exists(stmt))))


async def setup_steps(session: AsyncSession) -> list[SetupStep]:
    settings = await get_general_settings(session)
    backup = await session.get(BackupSettings, 1)
    connections = (await session.scalars(select(PlatformConnection))).all()

    has_materials = await _exists(session, select(Material.id))
    has_stock = await _exists(session, select(Material.id).where(Material.current_qty > 0))
    has_kitting = await _exists(session, select(DefaultKittingMaterial.material_id)) or await _exists(
        session, select(ProductKittingMaterial.product_id)
    )
    has_bom = await _exists(session, select(ProductMaterial.product_id))
    has_orders = bool(await session.scalar(select(func.count()).select_from(Order)))

    return [
        SetupStep("business_type", settings.business_type is not None),
        SetupStep("materials", has_materials),
        SetupStep("opening_stock", has_stock),
        SetupStep("kitting", has_kitting, optional=True),
        SetupStep("products", has_bom),
        SetupStep("orders", has_orders or any(c.is_connected for c in connections), optional=True),
        SetupStep("offsite_backup", bool(backup and backup.secondary_dir), optional=True),
    ]


async def set_dismissed(session: AsyncSession, dismissed: bool) -> None:
    settings = await get_general_settings(session)
    settings.setup_checklist_dismissed = dismissed
    await session.commit()
