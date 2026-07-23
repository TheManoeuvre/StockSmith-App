"""One-time cutover: copy real data from the live Postgres dev DB into a freshly
alembic-migrated SQLite file.

This is only for this one-time personal migration (Postgres -> SQLite) while there's
real business data to carry over. Fresh installs for other users start with an empty
SQLite DB created by `alembic upgrade head` + app.seed.ensure_seed_data — they never
touch Postgres at all.

Usage:
    1. Make sure the target SQLite file is already migrated to head:
       DATABASE_URL="sqlite+aiosqlite:///./dev.db" uv run alembic upgrade head

    2. Run this script (plain postgresql:// URL, not +asyncpg):
       uv run python scripts/migrate_pg_to_sqlite.py \
           --pg-url postgresql://stocksmith:stocksmith@localhost:5432/stocksmith \
           --sqlite-path ./dev.db

Foreign keys are disabled on the SQLite side during the copy (row order across tables
doesn't matter), then re-checked with PRAGMA foreign_key_check afterward. Row counts are
printed per table so a source/destination mismatch is obvious immediately.
"""

import argparse
import asyncio
import sqlite3
from datetime import date, datetime
from decimal import Decimal

import asyncpg

# Order is cosmetic only (FK enforcement is off during the copy) — parents first for
# readability when reading the printed report.
TABLES = [
    "general_settings",
    "margin_fee_config",
    "platform_fee_components",
    "manufacturers",
    "suppliers",
    "material_types",
    "materials",
    "material_purchases",
    "purchases",
    "material_adjustments",
    "shipping_profiles",
    "products",
    "product_variants",
    "product_materials",
    "product_variant_materials",
    "product_kitting_materials",
    "product_variant_kitting_materials",
    "product_bundle_items",
    "product_assets",
    "product_price_snapshots",
    "builds",
    "stock_adjustments",
    "listings",
    "sku_aliases",
    "platform_connections",
    "platform_sync_runs",
    "orders",
    "order_lines",
    "order_kitting_allocations",
    "order_kitting_overrides",
    "allocation_events",
]


def _convert(value: object) -> object:
    """Match the on-disk representation SQLAlchemy's sqlite dialect would produce for
    these Python types, so later ORM reads via the app decode them correctly."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


async def _copy_table(pg_conn: asyncpg.Connection, sqlite_conn: sqlite3.Connection, table: str) -> tuple[int, int]:
    rows = await pg_conn.fetch(f"SELECT * FROM {table}")
    if not rows:
        return 0, 0
    columns = list(rows[0].keys())
    col_list = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
    values = [[_convert(row[col]) for col in columns] for row in rows]
    sqlite_conn.executemany(sql, values)
    (sqlite_count,) = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return len(rows), sqlite_count


async def main(pg_url: str, sqlite_path: str) -> None:
    pg_conn = await asyncpg.connect(pg_url)
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.execute("PRAGMA foreign_keys=OFF")

    mismatches: list[str] = []
    print(f"{'table':<38} {'pg_rows':>8} {'sqlite_rows':>12}")
    try:
        for table in TABLES:
            pg_count, sqlite_count = await _copy_table(pg_conn, sqlite_conn, table)
            if pg_count != sqlite_count:
                mismatches.append(table)
            flag = "" if pg_count == sqlite_count else "  <-- MISMATCH"
            print(f"{table:<38} {pg_count:>8} {sqlite_count:>12}{flag}")
        sqlite_conn.commit()
    finally:
        await pg_conn.close()

    fk_violations = sqlite_conn.execute("PRAGMA foreign_key_check").fetchall()
    sqlite_conn.close()

    print()
    if mismatches:
        print(f"WARNING: row count mismatch in: {', '.join(mismatches)}")
    if fk_violations:
        print(f"WARNING: {len(fk_violations)} foreign key violations:")
        for v in fk_violations:
            print(f"  {v}")
    if not mismatches and not fk_violations:
        print("Row counts match and no foreign key violations — cutover looks clean.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pg-url", required=True, help="postgresql://user:pass@host:port/dbname (plain, not +asyncpg)")
    parser.add_argument("--sqlite-path", required=True, help="Path to the target SQLite file (must already be migrated to head)")
    args = parser.parse_args()
    asyncio.run(main(args.pg_url, args.sqlite_path))
