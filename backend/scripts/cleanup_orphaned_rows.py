"""Delete rows orphaned by order deletions that ran before foreign-key enforcement.

SQLite ships with `PRAGMA foreign_keys = 0` and the app never turned it on, so the
`ON DELETE CASCADE` declared on `allocation_events`, `order_line_returns`,
`order_kitting_allocations` and `order_kitting_overrides` never fired — every order
deleted through `DELETE /orders/{id}` left its audit rows behind. `app/db.py` now enables
the pragma per connection, which stops new leaks but says nothing about rows already
orphaned. This script is the one-off remediation for those.

`PRAGMA foreign_key_check` is the authority on what is orphaned: it walks every row of
every table against every declared foreign key and reports the offending rowids, so this
script never has to hardcode which tables to sweep or guess at parent ids.

Dry run by default. `--apply` takes a timestamped backup first, then deletes inside a
single transaction with enforcement on (so any cascade from an orphan's own children
fires), and re-runs `foreign_key_check` plus `integrity_check` before committing.

Refuses to delete an `order_kitting_allocations` row holding non-zero reserved_qty or
consumed_qty: those are packaging reservations that were never released, so the material's
own `allocated_qty`/`current_qty` would need recomputing too, and a bare delete would
quietly bake in the discrepancy. Same read-only preflight discipline as the 2026-07-28
unpaid-order cleanup (docs/cleanup-2026-07-28-unpaid-orders.md).

Usage, from backend/:
    uv run python scripts/cleanup_orphaned_rows.py                 # dry run, default db
    uv run python scripts/cleanup_orphaned_rows.py --apply
    uv run python scripts/cleanup_orphaned_rows.py --db path\to.db --apply
"""

import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_MAX_PASSES = 5


def default_db_path() -> Path:
    """Mirrors app/bootstrap.py's data-dir resolution — the packaged app's live database."""
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "StockSmith" if base else Path.home() / ".stocksmith"
    return root / "data" / "stocksmith.db"


def find_orphans(con: sqlite3.Connection) -> list[dict]:
    """One entry per foreign_key_check violation, enriched with the column and value that
    dangles so the report is readable without cross-referencing pragmas by hand."""
    orphans = []
    for child, rowid, parent, fk_id in con.execute("PRAGMA foreign_key_check").fetchall():
        fks = con.execute(f'PRAGMA foreign_key_list("{child}")').fetchall()
        column = next((fk[3] for fk in fks if fk[0] == fk_id), "?")
        value = None
        if rowid is not None:
            row = con.execute(f'SELECT "{column}" FROM "{child}" WHERE rowid = ?', (rowid,)).fetchone()
            value = row[0] if row else None
        orphans.append(
            {"table": child, "rowid": rowid, "column": column, "value": value, "missing_parent": parent}
        )
    return orphans


def preflight(con: sqlite3.Connection, orphans: list[dict]) -> list[str]:
    """Read-only. Returns human-readable reasons the cleanup must not proceed."""
    problems = []

    for o in orphans:
        if o["rowid"] is None:
            problems.append(
                f"{o['table']} has a violation with no rowid (WITHOUT ROWID table) — needs a hand-written delete"
            )
        if o["table"] == "order_kitting_allocations":
            row = con.execute(
                "SELECT reserved_qty, consumed_qty FROM order_kitting_allocations WHERE rowid = ?", (o["rowid"],)
            ).fetchone()
            if row and (row[0] or row[1]):
                problems.append(
                    f"order_kitting_allocations rowid={o['rowid']} holds reserved_qty={row[0]} "
                    f"consumed_qty={row[1]} — unreleased packaging, needs a stock recompute not a delete"
                )
    return problems


def report(orphans: list[dict]) -> None:
    if not orphans:
        print("No orphaned rows — PRAGMA foreign_key_check is clean.")
        return
    print(f"{len(orphans)} orphaned row(s):")
    by_table: dict[str, list[dict]] = {}
    for o in orphans:
        by_table.setdefault(o["table"], []).append(o)
    for table in sorted(by_table):
        print(f"  {table}:")
        for o in by_table[table]:
            print(
                f"    rowid={o['rowid']}  {o['column']}={o['value']} -> no such row in {o['missing_parent']}"
            )


def backup(db: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = db.with_name(f"{db.name}.bak-{stamp}-orphan-cleanup")
    shutil.copy2(db, dest)
    return dest


def delete_orphans(con: sqlite3.Connection) -> int:
    """Deletes every foreign_key_check violation, re-checking between passes so that rows
    orphaned by an orphan's removal (or revealed by a cascade) are caught too."""
    total = 0
    for _ in range(_MAX_PASSES):
        orphans = find_orphans(con)
        if not orphans:
            return total
        for o in orphans:
            con.execute(f'DELETE FROM "{o["table"]}" WHERE rowid = ?', (o["rowid"],))
            total += 1
    remaining = len(find_orphans(con))
    if remaining:
        raise RuntimeError(f"still {remaining} orphan(s) after {_MAX_PASSES} passes — aborting")
    return total


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
        orphans = find_orphans(con)
        report(orphans)
        if not orphans:
            return 0

        problems = preflight(con, orphans)
        if problems:
            print("\nPreflight failed, nothing deleted:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print("\nPreflight passed: every orphan is a zero-quantity audit row.")

        if not args.apply:
            print("Dry run — re-run with --apply to delete.")
            return 0

        dest = backup(db)
        print(f"Backed up to {dest}")

        with con:
            deleted = delete_orphans(con)
            still = find_orphans(con)
            if still:
                raise RuntimeError(f"{len(still)} orphan(s) remain after delete — rolling back")
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"integrity_check returned {integrity!r} — rolling back")
        print(f"Deleted {deleted} orphaned row(s). foreign_key_check clean, integrity_check ok.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
