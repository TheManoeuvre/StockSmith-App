"""Cleans up the fallout of the auto-created "4x6 Direct Thermal Label" material shipped
in 0.3.3 (services/kitting.attach_default_shipping_label, since removed).

That logic get-or-created a Material row by exact name match on every new product. On any
store where no material happened to already have that exact name, it silently created a
brand-new, never-purchased material (current_qty stuck at 0 forever, since nothing ever
recorded a purchase or adjustment against it) and attached it to every new product's
kitting BOM at qty 1 — which is why kitting/packaging capacity showed 0 for products that
should have had real stock via the user's own, differently-named label material.

0.3.3+ replaces that mechanism with a user-configured "Default kitting BOM" (Settings >
General) that only ever references materials the user has explicitly picked, so this
can't recur — see services/kitting.apply_default_kitting_bom.

This script finds the ghost material (name = "4x6 Direct Thermal Label", never purchased
or adjusted, so current_qty is definitely 0 rather than a coincidence) and every
ProductKittingMaterial line that points at it, reports what it found, and — with --apply
— removes both the kitting-BOM lines and the ghost material itself.

Refuses to touch a matching material that has ANY purchase or adjustment history — that
would mean a real material with this exact name that the user has actually been using, not
a ghost, and this script has no business deleting it.

Dry run by default.

Usage, from backend/:
    uv run python scripts/cleanup_ghost_default_label.py                # dry run
    uv run python scripts/cleanup_ghost_default_label.py --apply
    uv run python scripts/cleanup_ghost_default_label.py --db path\to.db --apply
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_GHOST_MATERIAL_NAME = "4x6 Direct Thermal Label"


def default_db_path() -> Path:
    """Mirrors app/bootstrap.py's data-dir resolution — the packaged app's live database."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "StockSmith" if base else Path.home() / ".stocksmith"
    return root / "data" / "stocksmith.db"


def find_ghost_materials(con: sqlite3.Connection) -> list[dict]:
    """Every material named exactly `_GHOST_MATERIAL_NAME` with zero purchase/adjustment
    history — a real, user-created material with the same name (if one exists) will
    always have been touched by at least the CSV import or manual-create flow that gave
    it its current_qty, so this is a safe, conservative match for "definitely a ghost"."""
    rows = con.execute(
        """
        SELECT m.id, m.current_qty
        FROM materials m
        WHERE m.name = ?
          AND NOT EXISTS (SELECT 1 FROM material_purchases mp WHERE mp.material_id = m.id)
          AND NOT EXISTS (SELECT 1 FROM material_adjustments ma WHERE ma.material_id = m.id)
        """,
        (_GHOST_MATERIAL_NAME,),
    ).fetchall()
    return [{"id": row[0], "current_qty": row[1]} for row in rows]


def find_kitting_lines(con: sqlite3.Connection, material_id: int) -> list[dict]:
    rows = con.execute(
        """
        SELECT pkm.id, pkm.product_id, p.name, p.sku
        FROM product_kitting_materials pkm
        JOIN products p ON p.id = pkm.product_id
        WHERE pkm.material_id = ?
        """,
        (material_id,),
    ).fetchall()
    return [{"line_id": row[0], "product_id": row[1], "product_name": row[2], "product_sku": row[3]} for row in rows]


def find_other_references(con: sqlite3.Connection, material_id: int) -> list[str]:
    """Anything else pointing at this material that would block a clean delete —
    reported so a --apply run never has to guess why it failed partway through."""
    blockers = []
    for table, label in [
        ("product_variant_kitting_materials", "variant kitting overrides"),
        ("order_kitting_overrides", "order kitting overrides"),
        ("order_kitting_allocations", "order kitting allocation ledger rows"),
        ("default_kitting_materials", "the new default kitting BOM"),
    ]:
        count = con.execute(f"SELECT COUNT(*) FROM {table} WHERE material_id = ?", (material_id,)).fetchone()[0]
        if count:
            blockers.append(f"{count} row(s) in {label}")
    return blockers


def backup(db: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = db.with_name(f"{db.name}.bak-{stamp}-ghost-label-cleanup")
    shutil.copy2(db, dest)
    return dest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=None, help="database file (default: the packaged app's)")
    parser.add_argument("--apply", action="store_true", help="actually delete (default is a dry run)")
    args = parser.parse_args()

    db = args.db or default_db_path()
    if not db.exists():
        print(f"No such database: {db}", file=sys.stderr)
        return 2

    print(f"Database: {db}")
    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        ghosts = find_ghost_materials(con)
        if not ghosts:
            print(f"No ghost '{_GHOST_MATERIAL_NAME}' material found — nothing to do.")
            return 0

        total_lines = 0
        blocked = False
        for ghost in ghosts:
            lines = find_kitting_lines(con, ghost["id"])
            blockers = find_other_references(con, ghost["id"])
            print(f"\nGhost material id={ghost['id']} (current_qty={ghost['current_qty']}):")
            print(f"  Attached to {len(lines)} product(s):")
            for line in lines:
                print(f"    - {line['product_name']!r} (sku={line['product_sku']}, kitting line id={line['line_id']})")
            if blockers:
                print(f"  BLOCKED — also referenced by: {', '.join(blockers)}")
                blocked = True
            ghost["lines"] = lines
            ghost["blocked"] = bool(blockers)
            total_lines += len(lines)

        if blocked:
            print(
                "\nOne or more ghost materials are referenced somewhere this script doesn't clean up automatically "
                "(e.g. an order that actually reserved/consumed it) — resolve those manually first; nothing was "
                "changed."
            )
            return 1

        if not args.apply:
            print(f"\nDry run — {len(ghosts)} ghost material(s), {total_lines} kitting-BOM line(s) to remove.")
            print("Re-run with --apply to delete them.")
            return 0

        dest = backup(db)
        print(f"\nBacked up to {dest}")

        with con:
            for ghost in ghosts:
                con.execute("DELETE FROM product_kitting_materials WHERE material_id = ?", (ghost["id"],))
                con.execute("DELETE FROM materials WHERE id = ?", (ghost["id"],))
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"integrity_check returned {integrity!r} — rolling back")
        print(f"Removed {len(ghosts)} ghost material(s) and {total_lines} kitting-BOM line(s).")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
