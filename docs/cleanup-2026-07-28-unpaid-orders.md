# Cleanup record — unpaid Etsy orders removed, 28 July 2026

One-off data-integrity fix accompanying the paid-only import gate. Recorded here rather
than as a soft-delete column because this codebase has no soft-delete pattern and adding
one for a single remediation would be disproportionate.

## Why

Before the gate existed, `order_sync.commit_sync` imported every receipt
`getShopReceipts` returned. `etsy._parse_receipt` read the receipt `status` field only to
detect cancellation and never read `is_paid`, so receipts whose payment had not settled
were imported as ordinary pending orders — and `_reconcile_status` immediately called
`allocation.allocate_order` on them.

Both orders below were Klarna-style instalment payments reporting
`INSTALL_IN_PROGRESS`, and **neither appeared anywhere in the Etsy seller UI**. The seller
confirmed both absences directly; that UI check, not the API's payment-status string, is
the ground truth this cleanup rests on.

## What was NOT removed

`4128127298` reported payment status `POSTED` and was initially a deletion candidate. It
was checked and **kept** — it is visible in the Etsy UI awaiting shipment and is a genuine
paid order.

This is the reason the gate reads the receipt's documented `is_paid` boolean rather than
the Payments endpoint's `status` string. That string is undocumented free-form (Etsy's own
schema says only "most commonly 'settled' or 'authed'"), three values were observed on
this one shop — `SETTLED`, `POSTED`, `INSTALL_IN_PROGRESS` — and two of the three mean
paid. A gate built on it would have deleted a real customer order.

## Orders removed

| Order id | Etsy receipt | Placed | Total | SKU | Allocated at deletion |
|---|---|---|---|---|---|
| 73 | `4128199713` | 2026-07-27 15:40:37 | £18.09 | `SKU-0015-REGULAR` | 0 |
| 78 | `4128119214` | 2026-07-28 04:13:43 | £12.59 | `SKU-0006-Orange` | 0 |

Both held zero allocated units, zero shipped units and zero packaging-material
reservations at the moment of deletion, so **no stock was released by this cleanup** and
no listing-quantity push was triggered.

### Order 78 had reserved stock earlier that morning

Its allocation history, removed with it:

```
2026-07-28 09:02:05  auto_allocate  qty 1  source=build#43
2026-07-28 09:05:57  deallocate     qty 1  source=manual
```

A build allocated one unit of `SKU-0006-Orange` to this unpaid phantom order
automatically, and it had to be released by hand three minutes later. That is
`allocation.auto_allocate_after_build` selecting purely on `Order.status`, which is why
the gate alone was insufficient and the same change added
`Order.pending_marketplace_cancellation.is_(False)` to that query.

## Procedure

1. Confirmed StockSmith was not running (no process, nothing listening on 8000) so the
   background scheduler could not race the edit.
2. Backed up the live database to
   `%LOCALAPPDATA%\StockSmith\data\stocksmith.db.bak-20260728-121445-unpaid-cleanup`.
3. Read-only preflight per order: zero allocated, zero shipped, zero kitting reservations.
   The script aborts rather than deleting if any of those is non-zero.
4. Deleted children explicitly, child-first, with `PRAGMA foreign_keys = ON`:
   `allocation_events` → `order_line_returns` → `order_kitting_allocations` →
   `order_kitting_overrides` → `order_lines` → `orders`.

Explicit child deletes were necessary because **SQLite ships with `foreign_keys = 0`**, so
the `ON DELETE CASCADE` declared on those tables does not fire. A bare
`DELETE FROM orders` would have silently orphaned every child row.

### Verification

`PRAGMA integrity_check` returned `ok`. Orphan counts were compared against the pre-change
backup and were **identical before and after** (3 `allocation_events`, 2
`order_kitting_allocations`, 1 `order_line_returns`) — this cleanup created none.

## Pre-existing orphans found (not caused by this cleanup, not fixed by it)

Those six rows all belong to order 48 / order line 52, deleted on 2026-07-24 through the
app's own `DELETE /orders/{id}`. That endpoint calls `session.delete(order)` and relies on
database-level `CASCADE` for `allocation_events`, `order_line_returns` and the kitting
tables, none of which have an ORM relationship — and with `foreign_keys = 0` that cascade
never happens. Every order deleted through the UI leaks its audit rows.

Tracked separately; not in scope here.

## Deliberately not done

**The sync watermark was not rewound.** `platform_connections.last_orders_synced_at` for
Etsy remains `2026-07-28 09:03:52`.

Rewinding it would make the *currently installed* build — which does not contain the gate
— re-import both receipts on its next poll, and `SKU-0006-Orange` has stock on hand, so
order 78 would immediately re-reserve it via the same build path described above.

Rewind only after a build containing the gate is installed. Until then, these two receipts
return only if Etsy bumps their last-modified time, and if that happens because payment
finally settled, re-importing them is the correct outcome.
