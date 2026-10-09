"""Square sandbox spike: catalogue push + inventory behaviour StockSmith would rely on.

Throwaway investigation script, companion to square_sandbox_spike.py. Not part of the app.
Answers four open risks from the catalogue-sync spike before anything is built:

  R1  Can an item be created archived (hidden) and later un-archived, and is it still
      sellable / indexable while archived?
  R2  Does a PHYSICAL_COUNT backdated before a sale have that sale re-applied on top
      (count = physical - sold), and does a count dated "now" wipe the sale out? Is the
      24h backdating limit real?
  R4  Does Square enforce SKU uniqueness, and what is the SKU length limit?
  R7  Can a paid itemised order drive stock negative, and what does sold_out report?

Everything it creates is prefixed "CATSPIKE" so it is easy to spot. Items used by paid
orders are archived rather than deleted at the end, so an order sync that later
batch-retrieves their catalog_object_id still resolves a SKU. Probe-only items (R4) are
deleted.

NOTE: the paid test orders are real sandbox orders at the first location — a StockSmith
install connected to this sandbox will import them on its next sync.

Usage (PowerShell):
    $env:SQUARE_SANDBOX_TOKEN = "<sandbox access token>"
    uv run --with httpx python scripts/dev/square_catalogue_spike.py
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

BASE = "https://connect.squareupsandbox.com/v2"
TOKEN = os.environ.get("SQUARE_SANDBOX_TOKEN")
if not TOKEN:
    sys.exit("Set SQUARE_SANDBOX_TOKEN first (Square Developer Console > your app > Sandbox > Credentials).")

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
    "Square-Version": "2024-10-17",
}
RUN = uuid.uuid4().hex[:6].upper()
results: dict = {"run": RUN}
verdicts: list[str] = []
to_delete: list[str] = []
to_archive: list[str] = []


def call(method: str, path: str, body: dict | None = None, allow_error: bool = False) -> dict:
    response = httpx.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=30)
    data = response.json() if response.content else {}
    if response.status_code >= 400:
        if allow_error:
            return {"_error": True, "_status": response.status_code, **data}
        sys.exit(f"{method} {path} failed ({response.status_code}):\n{json.dumps(data, indent=2)}")
    return data


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def verdict(label: str, ok: bool | None, detail: str) -> None:
    tag = {True: "PASS", False: "FAIL", None: "INFO"}[ok]
    line = f"[{tag}] {label}: {detail}"
    verdicts.append(line)
    print(line)


def create_item(key: str, *, sku: str, archived: bool = False, price: int = 1000, track: bool = True) -> tuple[str, str, dict]:
    """One item with one variation. Returns (item_id, variation_id, raw response)."""
    body = {
        "idempotency_key": str(uuid.uuid4()),
        "object": {
            "type": "ITEM",
            "id": f"#{key}",
            "item_data": {
                "name": f"CATSPIKE {RUN} {key}",
                "is_archived": archived,
                "variations": [
                    {
                        "type": "ITEM_VARIATION",
                        "id": f"#{key}-v",
                        "item_variation_data": {
                            "name": "Regular",
                            "sku": sku,
                            "pricing_type": "FIXED_PRICING",
                            "price_money": {"amount": price, "currency": currency},
                            "track_inventory": track,
                        },
                    }
                ],
            },
        },
    }
    res = call("POST", "/catalog/object", body)
    ids = {m["client_object_id"]: m["object_id"] for m in res.get("id_mappings", [])}
    return ids[f"#{key}"], ids[f"#{key}-v"], res


def physical_count(variation_id: str, qty: int, occurred_at: datetime, allow_error: bool = False) -> dict:
    return call(
        "POST",
        "/inventory/changes/batch-create",
        {
            "idempotency_key": str(uuid.uuid4()),
            "ignore_unchanged_counts": False,
            "changes": [
                {
                    "type": "PHYSICAL_COUNT",
                    "physical_count": {
                        "catalog_object_id": variation_id,
                        "location_id": location_id,
                        "state": "IN_STOCK",
                        "quantity": str(qty),
                        "occurred_at": iso(occurred_at),
                    },
                }
            ],
        },
        allow_error=allow_error,
    )


def in_stock(variation_id: str) -> str | None:
    res = call(
        "POST",
        "/inventory/counts/batch-retrieve",
        {"catalog_object_ids": [variation_id], "location_ids": [location_id], "states": ["IN_STOCK"]},
    )
    counts = res.get("counts", [])
    return counts[0]["quantity"] if counts else None


def settle_read(variation_id: str, expect: str | None = None, tries: int = 6) -> str | None:
    """Inventory counts are eventually consistent after a sale — poll briefly."""
    value = None
    for _ in range(tries):
        value = in_stock(variation_id)
        if expect is None or value == expect:
            return value
        time.sleep(2)
    return value


def sell(variation_id: str, qty: int) -> dict:
    """An itemised order for `qty` of the variation, paid in full with the sandbox test card."""
    order = call(
        "POST",
        "/orders",
        {
            "idempotency_key": str(uuid.uuid4()),
            "order": {
                "location_id": location_id,
                "reference_id": f"CATSPIKE-{RUN}",
                "line_items": [{"catalog_object_id": variation_id, "quantity": str(qty)}],
            },
        },
    )["order"]
    payment = call(
        "POST",
        "/payments",
        {
            "idempotency_key": str(uuid.uuid4()),
            "source_id": "cnon:card-nonce-ok",
            "order_id": order["id"],
            "amount_money": order["total_money"],
            "location_id": location_id,
            "autocomplete": True,
        },
        allow_error=True,
    )
    return {"order": order, "payment": payment}


location = call("GET", "/locations")["locations"][0]
location_id, currency = location["id"], location.get("currency", "GBP")
print(f"Run {RUN} — location {location['name']} ({location_id}), currency {currency}\n")

# ---------------------------------------------------------------- R1 archived creation
print("== R1: create archived, then un-archive")
r1_item, r1_var, r1_created = create_item("r1", sku=f"CATSPIKE-{RUN}-R1", archived=True)
results["r1_created"] = r1_created
to_archive.append(r1_item)
fetched = call("GET", f"/catalog/object/{r1_item}")["object"]
results["r1_fetched"] = fetched
verdict("R1 create archived", fetched["item_data"].get("is_archived") is True,
        f"is_archived={fetched['item_data'].get('is_archived')} on read-back")

# The search index lags writes — searching immediately returned nothing in either state
# on the first run (2026-09-30), which looked like "archived items are unsearchable".
time.sleep(10)
search_default = call("POST", "/catalog/search-catalog-items", {"text_filter": f"CATSPIKE {RUN} r1"})
search_all = call(
    "POST", "/catalog/search-catalog-items",
    {"text_filter": f"CATSPIKE {RUN} r1", "archived_state": "ARCHIVED_STATE_ALL"},
)
results["r1_search_default"], results["r1_search_all"] = search_default, search_all
verdict("R1 index visibility", None,
        f"SearchCatalogItems default returns {len(search_default.get('items', []))} item(s); "
        f"with ARCHIVED_STATE_ALL returns {len(search_all.get('items', []))} (may still be 0 if the index lags)")
list_catalog = call("GET", "/catalog/list?types=ITEM")
listed = [o for o in list_catalog.get("objects", []) if o["id"] == r1_item]
verdict("R1 ListCatalog includes archived", None, f"{'yes' if listed else 'no'} (first page only)")

inv_archived = physical_count(r1_var, 4, datetime.now(timezone.utc), allow_error=True)
results["r1_inventory_on_archived"] = inv_archived
verdict("R1 set stock while archived", not inv_archived.get("_error"),
        "accepted" if not inv_archived.get("_error") else json.dumps(inv_archived.get("errors")))

sale_archived = sell(r1_var, 1)
results["r1_sale_while_archived"] = sale_archived
verdict("R1 API can sell archived item", None,
        "payment " + ("rejected: " + json.dumps(sale_archived["payment"].get("errors"))
                      if sale_archived["payment"].get("_error") else "accepted"))

fetched["item_data"]["is_archived"] = False
unarchived = call("POST", "/catalog/object", {"idempotency_key": str(uuid.uuid4()), "object": fetched})
results["r1_unarchived"] = unarchived
verdict("R1 un-archive via upsert", unarchived["catalog_object"]["item_data"].get("is_archived") in (False, None),
        f"is_archived now {unarchived['catalog_object']['item_data'].get('is_archived')}")

# Square's version check is per field: a stale version is only rejected when a field it
# changes was modified since. Resending unchanged content with an old version is accepted,
# so the probe must change a field that has also changed concurrently.
latest = unarchived["catalog_object"]
stale_version = latest["version"]
latest["item_data"]["name"] = f"CATSPIKE {RUN} r1 renamed-A"
results["r1_renamed"] = call("POST", "/catalog/object", {"idempotency_key": str(uuid.uuid4()), "object": latest})
latest["item_data"]["name"] = f"CATSPIKE {RUN} r1 renamed-B"
latest["version"] = stale_version
stale = call("POST", "/catalog/object", {"idempotency_key": str(uuid.uuid4()), "object": latest}, allow_error=True)
results["r1_stale_version_upsert"] = stale
verdict("R1 stale version rejected", bool(stale.get("_error")),
        json.dumps(stale.get("errors")) if stale.get("_error") else "accepted (no optimistic concurrency!)")
latest.pop("version")
no_version = call("POST", "/catalog/object", {"idempotency_key": str(uuid.uuid4()), "object": latest}, allow_error=True)
results["r1_no_version_upsert"] = no_version
verdict("R1 missing version rejected", bool(no_version.get("_error")),
        json.dumps(no_version.get("errors")) if no_version.get("_error") else "accepted (last write wins)")

# ---------------------------------------------------------------- R2 backdated counts
print("\n== R2: backdated physical count vs a sale")
r2_item, r2_var, results["r2_created"] = create_item("r2", sku=f"CATSPIKE-{RUN}-R2")
to_archive.append(r2_item)
now = datetime.now(timezone.utc)
physical_count(r2_var, 10, now - timedelta(hours=3))
verdict("R2 baseline count 10 @ -3h", None, f"IN_STOCK={settle_read(r2_var, '10')}")

results["r2_sale"] = sell(r2_var, 2)
after_sale = settle_read(r2_var, "8")
verdict("R2 paid API order decrements stock", after_sale == "8", f"IN_STOCK={after_sale} (expected 8)")

physical_count(r2_var, 10, now - timedelta(hours=1))
backdated = settle_read(r2_var, "8")
verdict("R2 count 10 backdated before sale -> sale re-applied", backdated == "8",
        f"IN_STOCK={backdated} (8 = sale stacked on top; 10 = sale lost)")

physical_count(r2_var, 10, datetime.now(timezone.utc))
current = settle_read(r2_var, "10")
verdict("R2 count 10 dated now overwrites the sale", current == "10",
        f"IN_STOCK={current} (confirms the naive-push race)")

too_old = physical_count(r2_var, 5, datetime.now(timezone.utc) - timedelta(hours=25), allow_error=True)
results["r2_count_25h_old"] = too_old
verdict("R2 count >24h old rejected", bool(too_old.get("_error")),
        json.dumps(too_old.get("errors")) if too_old.get("_error") else "accepted — limit not enforced")
edge = physical_count(r2_var, 7, datetime.now(timezone.utc) - timedelta(hours=23, minutes=50), allow_error=True)
results["r2_count_23h50_old"] = edge
verdict("R2 count 23h50m old accepted", not edge.get("_error"),
        "accepted" if not edge.get("_error") else json.dumps(edge.get("errors")))

changes = call(
    "POST", "/inventory/changes/batch-retrieve",
    {"catalog_object_ids": [r2_var], "location_ids": [location_id]},
)
results["r2_change_history"] = changes
print("   history (occurred_at, type, qty):")
for c in changes.get("changes", []):
    body = c.get("physical_count") or c.get("adjustment") or {}
    print(f"     {body.get('occurred_at')}  {c['type']:<15} {body.get('quantity')}")

# ---------------------------------------------------------------- R4 SKU uniqueness / length
print("\n== R4: duplicate SKUs and SKU length")
dup_sku = f"CATSPIKE-{RUN}-DUP"
a_item, a_var, _ = create_item("r4a", sku=dup_sku)
b = call(
    "POST", "/catalog/object",
    {
        "idempotency_key": str(uuid.uuid4()),
        "object": {
            "type": "ITEM", "id": "#r4b",
            "item_data": {"name": f"CATSPIKE {RUN} r4b", "variations": [{
                "type": "ITEM_VARIATION", "id": "#r4b-v",
                "item_variation_data": {"name": "Regular", "sku": dup_sku, "pricing_type": "FIXED_PRICING",
                                        "price_money": {"amount": 1000, "currency": currency}},
            }]},
        },
    },
    allow_error=True,
)
results["r4_duplicate_create"] = b
to_delete.append(a_item)
if not b.get("_error"):
    to_delete.append(next(m["object_id"] for m in b["id_mappings"] if m["client_object_id"] == "#r4b"))
verdict("R4 duplicate SKU accepted", None,
        "accepted — Square does NOT enforce uniqueness" if not b.get("_error")
        else "rejected: " + json.dumps(b.get("errors")))
time.sleep(2)  # search index lag
dup_search = call(
    "POST", "/catalog/search",
    {"object_types": ["ITEM_VARIATION"], "query": {"exact_query": {"attribute_name": "sku", "attribute_value": dup_sku}}},
)
results["r4_duplicate_search"] = dup_search
verdict("R4 exact SKU search", None, f"returns {len(dup_search.get('objects', []))} variation(s) for the duplicated SKU")

length_results = {}
for n in (32, 64, 128, 255, 256, 512, 1024, 4096):
    res = call(
        "POST", "/catalog/object",
        {
            "idempotency_key": str(uuid.uuid4()),
            "object": {
                "type": "ITEM", "id": f"#len{n}",
                "item_data": {"name": f"CATSPIKE {RUN} len{n}", "variations": [{
                    "type": "ITEM_VARIATION", "id": f"#len{n}-v",
                    "item_variation_data": {"name": "Regular", "sku": "S" * n, "pricing_type": "FIXED_PRICING",
                                            "price_money": {"amount": 100, "currency": currency}},
                }]},
            },
        },
        allow_error=True,
    )
    if res.get("_error"):
        length_results[n] = "rejected: " + (res.get("errors") or [{}])[0].get("detail", "?")
    else:
        stored = res["catalog_object"]["item_data"]["variations"][0]["item_variation_data"].get("sku", "")
        length_results[n] = f"accepted, stored {len(stored)} chars"
        to_delete.append(next(m["object_id"] for m in res["id_mappings"] if m["client_object_id"] == f"#len{n}"))
results["r4_sku_lengths"] = length_results
for n, outcome in length_results.items():
    verdict(f"R4 SKU length {n}", None, outcome)

# ---------------------------------------------------------------- R7 negative stock / sold_out
print("\n== R7: overselling")
r7_item, r7_var, results["r7_created"] = create_item("r7", sku=f"CATSPIKE-{RUN}-R7")
to_archive.append(r7_item)
physical_count(r7_var, 1, datetime.now(timezone.utc))
settle_read(r7_var, "1")
oversell = sell(r7_var, 3)
results["r7_oversell"] = oversell
paid = not oversell["payment"].get("_error")
verdict("R7 API sale of 3 with 1 in stock", None,
        "payment accepted" if paid else "payment rejected: " + json.dumps(oversell["payment"].get("errors")))
negative = settle_read(r7_var, "-2")
verdict("R7 stock goes negative", negative is not None and negative.startswith("-"), f"IN_STOCK={negative}")
r7_obj = call("GET", f"/catalog/object/{r7_var}")["object"]
results["r7_variation_after"] = r7_obj
overrides = r7_obj["item_variation_data"].get("location_overrides", [])
verdict("R7 sold_out flag", None, f"location_overrides={json.dumps(overrides) or '[]'}")

# ---------------------------------------------------------------- cleanup
print("\n== cleanup")
for item_id in to_archive:
    obj = call("GET", f"/catalog/object/{item_id}")["object"]
    obj["item_data"]["is_archived"] = True
    call("POST", "/catalog/object", {"idempotency_key": str(uuid.uuid4()), "object": obj}, allow_error=True)
if to_delete:
    call("POST", "/catalog/batch-delete", {"object_ids": to_delete}, allow_error=True)
print(f"archived {len(to_archive)} item(s) used by orders, deleted {len(to_delete)} probe item(s)")

results["verdicts"] = verdicts
with open("square_catalogue_spike_output.json", "w") as handle:
    json.dump(results, handle, indent=2)
print("\nFull responses saved to square_catalogue_spike_output.json")
