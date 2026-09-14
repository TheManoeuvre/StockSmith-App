"""Fill width_px / height_px on product image assets created before those columns existed.

The columns (migration e2b5a1c9d4f7) are populated at upload/import time going forward;
this is the one-off that reads each existing image off disk and records its pixel size.
Only image asset types are touched (main_image / listing_image) — CAD and gcode files have
no dimensions and stay null. Rows that already have both dimensions set are skipped, so this
is safe to re-run. A missing or unreadable original is reported and skipped, not fatal.

Dry run by default.

Usage, from backend/ :
    uv run python scripts/backfill_asset_dimensions.py            # dry run
    uv run python scripts/backfill_asset_dimensions.py --apply

    # against the packaged app's data (close the app first)
    DATABASE_URL="sqlite+aiosqlite:///C:/Users/<you>/AppData/Local/StockSmith/data/stocksmith.db" \
    ASSET_ROOT="C:/Users/<you>/AppData/Local/StockSmith/assets" \
    SHARED_PASSWORD_HASH=x \
    uv run python scripts/backfill_asset_dimensions.py --apply
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import async_session_factory  # noqa: E402
from app.models.asset import ProductAsset  # noqa: E402
from app.services.file_storage import (  # noqa: E402
    _THUMBNAIL_ASSET_TYPES,
    image_dimensions,
    resolve_asset_path,
)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write dimensions (default: dry run)")
    args = parser.parse_args()

    filled = 0
    missing = 0
    failed = 0

    async with async_session_factory() as session:
        assets = (
            await session.execute(
                select(ProductAsset).where(ProductAsset.asset_type.in_(_THUMBNAIL_ASSET_TYPES))
            )
        ).scalars()
        pending = [a for a in assets if a.width_px is None or a.height_px is None]

        print(
            f"{len(pending)} image asset(s) missing dimensions. "
            f"{'APPLYING' if args.apply else 'DRY RUN'}\n"
        )

        for a in pending:
            original = resolve_asset_path(a.file_path)
            if not original.exists():
                print(f"  MISSING original, skipped: asset {a.id} -> {a.file_path}")
                missing += 1
                continue
            dims = image_dimensions(original.read_bytes())
            if dims is None:
                print(f"  UNREADABLE, skipped: asset {a.id} -> {a.file_path}")
                failed += 1
                continue
            print(f"  {'set' if args.apply else 'would set'}: asset {a.id} -> {dims[0]}x{dims[1]}")
            if args.apply:
                a.width_px, a.height_px = dims
            filled += 1

        if args.apply:
            await session.commit()

    print(
        f"\nDone. {filled} {'filled' if args.apply else 'to fill'}, "
        f"{missing} missing original(s), {failed} unreadable."
    )
    if not args.apply and filled:
        print("Re-run with --apply to write.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
