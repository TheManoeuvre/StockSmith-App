"""Regenerate every stored image thumbnail from its retained original.

Thumbnails are generated once at upload time (services/file_storage.py::save_material_image
and ::save_upload) at whatever `_THUMBNAIL_MAX_DIM` was then. Lowering that constant only
affects future uploads — existing `thumb-*.jpg` files keep their old size. This is the one-off
that brings them into line: it reads each original back off disk and rewrites its thumbnail at
the current (or a given) max dimension.

Only touches images that already have a DB row pointing at them — materials with a non-null
`image_path`, and product assets of an image type (main_image / listing_image). The full-res
original is never modified, so this is safe to re-run and to run at a different `--max-dim`
later.

An original that has gone missing from disk is reported and skipped, not treated as fatal.

Dry run by default.

Usage, from backend/ (point the env at the target install first):
    # dev SQLite (uses backend/.env)
    uv run python scripts/regenerate_thumbnails.py                       # dry run
    uv run python scripts/regenerate_thumbnails.py --apply

    # against the packaged app's data (close the app first)
    DATABASE_URL="sqlite+aiosqlite:///C:/Users/<you>/AppData/Local/StockSmith/data/stocksmith.db" \
    ASSET_ROOT="C:/Users/<you>/AppData/Local/StockSmith/assets" \
    SHARED_PASSWORD_HASH=x \
    uv run python scripts/regenerate_thumbnails.py --apply

    # override the size explicitly
    uv run python scripts/regenerate_thumbnails.py --apply --max-dim 256
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Run directly as a file, not as a package — so backend/ has to go on the path before `app`
# resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.db import async_session_factory  # noqa: E402
from app.models.asset import ProductAsset  # noqa: E402
from app.models.material import Material  # noqa: E402
from app.services.file_storage import (  # noqa: E402
    _THUMBNAIL_ASSET_TYPES,
    _THUMBNAIL_MAX_DIM,
    generate_thumbnail,
    resolve_asset_path,
    thumbnail_path_for,
)


async def _collect_targets(session) -> list[tuple[str, str]]:
    """(label, relative_path) for every image with a DB reference."""
    targets: list[tuple[str, str]] = []

    materials = (
        await session.execute(select(Material).where(Material.image_path.isnot(None)))
    ).scalars()
    for m in materials:
        targets.append((f"material {m.id} ({m.name})", m.image_path))

    assets = (
        await session.execute(
            select(ProductAsset).where(ProductAsset.asset_type.in_(_THUMBNAIL_ASSET_TYPES))
        )
    ).scalars()
    for a in assets:
        targets.append((f"product asset {a.id} (product {a.product_id})", a.file_path))

    return targets


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write thumbnails (default: dry run)")
    parser.add_argument(
        "--max-dim",
        type=int,
        default=_THUMBNAIL_MAX_DIM,
        help=f"thumbnail max dimension (default: {_THUMBNAIL_MAX_DIM})",
    )
    args = parser.parse_args()

    async with async_session_factory() as session:
        targets = await _collect_targets(session)

    print(f"{len(targets)} referenced image(s) found. max-dim={args.max_dim}. "
          f"{'APPLYING' if args.apply else 'DRY RUN'}\n")

    regenerated = 0
    missing = 0
    failed = 0

    for label, rel_path in targets:
        original = resolve_asset_path(rel_path)
        thumb = resolve_asset_path(thumbnail_path_for(rel_path).as_posix())

        if not original.exists():
            print(f"  MISSING original, skipped: {label} -> {rel_path}")
            missing += 1
            continue

        try:
            if args.apply:
                thumb.write_bytes(generate_thumbnail(original.read_bytes(), max_dim=args.max_dim))
            regenerated += 1
            print(f"  {'wrote' if args.apply else 'would write'}: {label} -> {thumb.name}")
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
            print(f"  FAILED: {label} -> {rel_path}: {exc}")
            failed += 1

    print(
        f"\nDone. {regenerated} {'regenerated' if args.apply else 'to regenerate'}, "
        f"{missing} missing original(s), {failed} failed."
    )
    if not args.apply and regenerated:
        print("Re-run with --apply to write.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
