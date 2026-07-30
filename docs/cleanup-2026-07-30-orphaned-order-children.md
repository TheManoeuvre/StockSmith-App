# Order-deletion cascade fixed, orphaned children removed — 30 July 2026

Closes the item docs/cleanup-2026-07-28-unpaid-orders.md left open under "Pre-existing
orphans found (not caused by this cleanup, not fixed by it)".

## The bug

`DELETE /orders/{id}` (backend/app/routers/orders.py) deletes the order row and lets the
database clean up after it. `Order.lines` has an ORM `cascade="all, delete-orphan"`, so
`order_lines` went. Four other tables have no ORM relationship at all and relied purely on
the `ON DELETE CASCADE` declared in their schema:

- `allocation_events` (via `order_lines`)
- `order_line_returns` (via `order_lines`)
- `order_kitting_allocations`
- `order_kitting_overrides`

SQLite ships with `PRAGMA foreign_keys = 0` and it is a **per-connection** setting, not
something stored in the database file — so those cascades never fired. Every order deleted
through the UI leaked its audit rows.

## The fix

`enforce_sqlite_foreign_keys` in backend/app/db.py registers a SQLAlchemy `connect`
listener that issues `PRAGMA foreign_keys = ON`. Points worth keeping:

- **A `connect` listener, not a one-off statement.** The pragma resets to its default on
  each new connection, so a pooled connection recycled by SQLAlchemy would come back with
  enforcement off again.
- **Registered on the engine instance, not globally on `Engine`.** Keeps it off engines
  built elsewhere, which is what lets the tests hold an enforced and an unenforced engine
  side by side.
- **Gated on `engine.dialect.name == "sqlite"`.** The app also supports Postgres via
  `DATABASE_URL`; it enforces foreign keys unconditionally and has no such pragma.
- **Not applied to Alembic's engine.** alembic/env.py builds its own engine, deliberately
  left alone: SQLite cannot `ALTER` most constraints, so a table-rebuild migration needs to
  be free to move rows while references are momentarily dangling.
- **Not applied to `bootstrap._seed`'s engine either.** `ensure_seed_data` only writes
  `general_settings`, `margin_fee_config` and `platform_fee_components` — three of the
  eleven tables that declare no foreign keys at all — so wiring it in would only pull
  `app.db`'s module-level engine into bootstrap, which imports `app.config` before
  bootstrap has finished setting the environment it reads.

## Why enabling enforcement was safe

Audited every hard delete in the codebase and every foreign key in the live schema (52
foreign keys: 22 CASCADE, 15 RESTRICT, 15 SET NULL — all with an explicit action, no
implicit `NO ACTION` anywhere).

**The fifteen RESTRICT constraints are never exercised**, because their parents are only
ever soft-deleted: `DELETE /products/{id}`, `/variants/{id}` and `/materials/{id}` all set
`is_active = False` and delete no row. That is now pinned by a test, so turning any of the
three into a real delete fails loudly instead of shredding order history.

The other hard deletes:

| Endpoint | Deletes | Effect of enforcement |
|---|---|---|
| `DELETE /orders/{id}` | `orders` | **The fix** — four child tables now actually cascade |
| `DELETE /shipping-profiles/{id}` | `shipping_profiles` | `SET NULL` now fires on `orders`, `products`, `product_variants` instead of leaving a dangling id |
| `DELETE /purchases/{id}` | `purchases` | None — `Purchase.lines` already has an ORM cascade |
| `DELETE /products/{id}/assets/{id}` | `product_assets` | None — nothing references it |
| `DELETE /platform-fee-components/...` | `platform_fee_components` | None — nothing references it |

The bulk `delete(...)` statements (BOM replacement, kitting overrides) all target tables
nothing else references.

Two `SET NULL` flows were checked specifically because they change from "dangling" to
"really null":

- **`shipping_profiles`** — all three referencing columns are nullable and are only read
  through a relationship or an explicit `session.get`, both of which already handle absence.
  A shipped order's cost is unaffected: it is frozen in `shipping_cost_snapshot`, not read
  back off the profile.
- **`material_adjustments.order_id`** — the material stock-history view already renders
  `order_id != null` before linking to an order, so today a deleted order produces a link
  to a 404 and enforcement turns it into no link. The row itself survives, which is the
  requirement — a consumed-packaging stock movement is permanent.

## Orphans removed

All six rows belonged to order 48 / order line 52, deleted through the UI on 2026-07-24:

| Table | Rows | Contents |
|---|---|---|
| `allocation_events` | 3 | `allocate` 2, `auto_allocate` 1 (`build#27`), `deallocate` 2 (`manual`) |
| `order_line_returns` | 1 | product scope, qty 2, `return_to_stock`, `cancel_before_ship` |
| `order_kitting_allocations` | 2 | materials 18 and 72, **both `reserved_qty=0` and `consumed_qty=0`** |

Those zeroes are the reason this was a safe delete and not a stock repair. The order had
been fully deallocated before it was deleted, so no material reservation was stranded:
materials 18 and 72 both sit at `allocated_qty = 0`, and the cleanup left every
`materials`, `products` and `orders` row untouched. There were no orphaned
`material_adjustments` at all.

## Procedure

backend/scripts/cleanup_orphaned_rows.py, dry-run by default. It finds orphans with
`PRAGMA foreign_key_check` rather than hardcoding tables, refuses to delete an
`order_kitting_allocations` row holding non-zero reserved/consumed qty, takes a timestamped
backup, deletes in one transaction with enforcement on, and rolls back unless
`foreign_key_check` comes back empty and `integrity_check` returns `ok`.

Verified by running it for real against a copy of the live database first: exactly three
tables changed by exactly −3/−2/−1 rows, all 32 other tables byte-identical in row count,
and product stock, material quantities and orders all unchanged.

The script is idempotent and worth keeping — it is also the diagnostic for confirming no
new orphans appear.
