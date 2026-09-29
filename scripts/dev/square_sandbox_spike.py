"""Square sandbox spike: create a test custom-item order, pay it, read it back.

Throwaway investigation script (see docs/plan-square-integration.md). Not part of the app.
Writes everything Square returns to square_spike_output.json so we can inspect real field names.

Usage (PowerShell):
    pip install httpx
    $env:SQUARE_SANDBOX_TOKEN = "<sandbox access token>"
    python scripts/dev/square_sandbox_spike.py
"""

import json
import os
import sys
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
results: dict = {}


def call(method: str, path: str, body: dict | None = None, allow_error: bool = False) -> dict:
    response = httpx.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=30)
    data = response.json()
    if response.status_code >= 400:
        if allow_error:
            return {"_error": True, "_status": response.status_code, **data}
        sys.exit(f"{method} {path} failed ({response.status_code}):\n{json.dumps(data, indent=2)}")
    return data


# 1. Which seller location and currency does this sandbox account use?
location = call("GET", "/locations")["locations"][0]
results["location"] = location
location_id, currency = location["id"], location.get("currency", "GBP")
print(f"Location: {location['name']} ({location_id}), currency {currency}")

# 2. Create an order: a custom patch (customisation in the line-item note), a delivery line,
#    and a pickup fulfilment. This mirrors what you'd enter at the counter.
pickup_at = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
order_body = {
    "idempotency_key": str(uuid.uuid4()),
    "order": {
        "location_id": location_id,
        "line_items": [
            {
                "name": "Leather patch (custom)",
                "quantity": "1",
                "note": "Text on patch: HINETT'S HOME",
                "base_price_money": {"amount": 1500, "currency": currency},
            },
            {
                "name": "Delivery",
                "quantity": "1",
                "base_price_money": {"amount": 350, "currency": currency},
            },
        ],
        "fulfillments": [
            {
                "type": "PICKUP",
                "state": "PROPOSED",
                "pickup_details": {
                    "recipient": {"display_name": "Test Customer"},
                    "schedule_type": "SCHEDULED",
                    "pickup_at": pickup_at,
                },
            }
        ],
    },
}
order = call("POST", "/orders", order_body)["order"]
results["created_order"] = order
print(f"Created order {order['id']}, total {order['total_money']}")

# 3. Pay it with Square's sandbox test card so the order arrives "paid in full".
payment = call(
    "POST",
    "/payments",
    {
        "idempotency_key": str(uuid.uuid4()),
        "source_id": "cnon:card-nonce-ok",
        "location_id": location_id,
        "order_id": order["id"],
        "amount_money": order["total_money"],
    },
)["payment"]
results["payment"] = payment
print(f"Payment {payment['id']} status {payment['status']}")

# 4. A second order using a SHIPMENT fulfilment instead of PICKUP, paid the same way, so we
#    can see the delivery-address shape (the pickup order above never exercised this).
shipment_order_body = {
    "idempotency_key": str(uuid.uuid4()),
    "order": {
        "location_id": location_id,
        "line_items": [
            {
                "name": "Leather patch (custom)",
                "quantity": "1",
                "note": "Text on patch: HINETT'S HOME",
                "base_price_money": {"amount": 1500, "currency": currency},
            },
            {
                "name": "Delivery",
                "quantity": "1",
                "base_price_money": {"amount": 350, "currency": currency},
            },
        ],
        "fulfillments": [
            {
                "type": "SHIPMENT",
                "state": "PROPOSED",
                "shipment_details": {
                    "recipient": {
                        "display_name": "Test Customer",
                        "address": {
                            "address_line_1": "1 Test Street",
                            "locality": "London",
                            "postal_code": "E1 6AN",
                            "country": "GB",
                        },
                    },
                },
            }
        ],
    },
}
shipment_order = call("POST", "/orders", shipment_order_body)["order"]
results["shipment_order_created"] = shipment_order
print(f"Created shipment order {shipment_order['id']}, total {shipment_order['total_money']}")

shipment_payment = call(
    "POST",
    "/payments",
    {
        "idempotency_key": str(uuid.uuid4()),
        "source_id": "cnon:card-nonce-ok",
        "location_id": location_id,
        "order_id": shipment_order["id"],
        "amount_money": shipment_order["total_money"],
    },
)["payment"]
results["shipment_order_payment"] = shipment_payment
print(f"Shipment order payment {shipment_payment['id']} status {shipment_payment['status']}")

shipment_order_after_payment = call("GET", f"/orders/{shipment_order['id']}")["order"]
results["shipment_order_after_payment"] = shipment_order_after_payment

# 5. A third order that we then cancel, to see the CANCELED shape. No payment — cancelling an
#    unpaid order is the simplest way to see the state transition.
cancel_order_body = {
    "idempotency_key": str(uuid.uuid4()),
    "order": {
        "location_id": location_id,
        "line_items": [
            {
                "name": "Leather patch (custom)",
                "quantity": "1",
                "note": "Text on patch: CANCEL ME",
                "base_price_money": {"amount": 1500, "currency": currency},
            },
        ],
        "fulfillments": [
            {
                "type": "PICKUP",
                "state": "PROPOSED",
                "pickup_details": {
                    "recipient": {"display_name": "Test Customer"},
                    "schedule_type": "SCHEDULED",
                    "pickup_at": pickup_at,
                },
            }
        ],
    },
}
order_to_cancel = call("POST", "/orders", cancel_order_body)["order"]
results["order_to_cancel_created"] = order_to_cancel
print(f"Created order to cancel {order_to_cancel['id']}")

cancelled_order = call(
    "PUT",
    f"/orders/{order_to_cancel['id']}",
    {
        "idempotency_key": str(uuid.uuid4()),
        "order": {
            "location_id": location_id,
            "version": order_to_cancel["version"],
            "state": "CANCELED",
            # Square requires every fulfilment to be in a terminal state before the order
            # itself can be CANCELED — update the existing fulfilment by its uid.
            "fulfillments": [
                {
                    "uid": order_to_cancel["fulfillments"][0]["uid"],
                    "state": "CANCELED",
                }
            ],
        },
    },
)["order"]
results["cancelled_order"] = cancelled_order
print(f"Order {cancelled_order['id']} state after cancel: {cancelled_order['state']}")

# 7. A catalogue item with a SKU, and an order line that references it via
#    catalog_object_id — SquareAdapter resolves SKUs this way since an order line item
#    never carries its own `sku` field directly. Not exercised by anything above (those
#    orders used ad-hoc line items with no catalog object at all).
catalog_body = {
    "idempotency_key": str(uuid.uuid4()),
    "object": {
        "type": "ITEM",
        "id": "#leather-patch",
        "item_data": {
            "name": "Leather Patch (Catalogue)",
            "variations": [
                {
                    "type": "ITEM_VARIATION",
                    "id": "#leather-patch-regular",
                    "item_variation_data": {
                        "item_id": "#leather-patch",
                        "name": "Regular",
                        "sku": "PATCH-REG-01",
                        "pricing_type": "FIXED_PRICING",
                        "price_money": {"amount": 1500, "currency": currency},
                    },
                }
            ],
        },
    },
}
catalog_result = call("POST", "/catalog/object", catalog_body)
results["catalog_object_created"] = catalog_result
variation_id = next(
    m["object_id"] for m in catalog_result.get("id_mappings", []) if m["client_object_id"] == "#leather-patch-regular"
)
print(f"Created catalogue item, variation id {variation_id}")

catalog_batch_retrieve = call("POST", "/catalog/batch-retrieve", {"object_ids": [variation_id]})
results["catalog_batch_retrieve"] = catalog_batch_retrieve
print(f"Batch-retrieved {len(catalog_batch_retrieve.get('objects', []))} catalog object(s)")

catalog_order_body = {
    "idempotency_key": str(uuid.uuid4()),
    "order": {
        "location_id": location_id,
        "line_items": [
            {
                "catalog_object_id": variation_id,
                "quantity": "1",
                "note": "Text on patch: CATALOGUE TEST",
            }
        ],
    },
}
catalog_order = call("POST", "/orders", catalog_order_body)["order"]
results["catalog_order_created"] = catalog_order
print(f"Created catalogue-linked order {catalog_order['id']}")

# 8. A paid order that we then try to cancel — to see whether Square allows cancelling a
#    paid order directly or insists on a refund first, and what net_amount_due_money/state
#    look like afterward. SquareAdapter currently guesses "a cancelled order with a tender
#    recorded means PaymentState.reversed" — untested until now.
paid_to_cancel_body = {
    "idempotency_key": str(uuid.uuid4()),
    "order": {
        "location_id": location_id,
        "line_items": [
            {
                "name": "Leather patch (custom)",
                "quantity": "1",
                "note": "Text on patch: PAID THEN CANCELLED",
                "base_price_money": {"amount": 1500, "currency": currency},
            },
        ],
        "fulfillments": [
            {
                "type": "PICKUP",
                "state": "PROPOSED",
                "pickup_details": {
                    "recipient": {"display_name": "Test Customer"},
                    "schedule_type": "SCHEDULED",
                    "pickup_at": pickup_at,
                },
            }
        ],
    },
}
paid_to_cancel = call("POST", "/orders", paid_to_cancel_body)["order"]
results["paid_to_cancel_created"] = paid_to_cancel
print(f"Created order to pay then cancel: {paid_to_cancel['id']}")

paid_to_cancel_payment = call(
    "POST",
    "/payments",
    {
        "idempotency_key": str(uuid.uuid4()),
        "source_id": "cnon:card-nonce-ok",
        "location_id": location_id,
        "order_id": paid_to_cancel["id"],
        "amount_money": paid_to_cancel["total_money"],
    },
)["payment"]
results["paid_to_cancel_payment"] = paid_to_cancel_payment
print(f"Paid order {paid_to_cancel['id']}, payment status {paid_to_cancel_payment['status']}")

paid_to_cancel_after_payment = call("GET", f"/orders/{paid_to_cancel['id']}")["order"]

cancel_attempt = call(
    "PUT",
    f"/orders/{paid_to_cancel['id']}",
    {
        "idempotency_key": str(uuid.uuid4()),
        "order": {
            "location_id": location_id,
            "version": paid_to_cancel_after_payment["version"],
            "state": "CANCELED",
            "fulfillments": [
                {"uid": paid_to_cancel_after_payment["fulfillments"][0]["uid"], "state": "CANCELED"}
            ],
        },
    },
    allow_error=True,
)

if cancel_attempt.get("_error"):
    results["cancel_paid_order_direct_rejected"] = cancel_attempt
    print(f"Cancelling a PAID order directly was rejected ({cancel_attempt['_status']}) — refunding first, then retrying")

    refund = call(
        "POST",
        "/refunds",
        {
            "idempotency_key": str(uuid.uuid4()),
            "payment_id": paid_to_cancel_payment["id"],
            "amount_money": paid_to_cancel_payment["amount_money"],
            "reason": "Sandbox spike test refund",
        },
    )["refund"]
    results["refund"] = refund
    print(f"Refund {refund['id']} status {refund['status']}")

    paid_to_cancel_after_refund = call("GET", f"/orders/{paid_to_cancel['id']}")["order"]
    results["paid_to_cancel_after_refund"] = paid_to_cancel_after_refund
    print(
        f"Order after refund (retry-cancel skipped — Square already told us this is rejected) "
        f"-> state {paid_to_cancel_after_refund['state']}, "
        f"net_amount_due {paid_to_cancel_after_refund.get('net_amount_due_money')}, "
        f"refunded_amount_money {paid_to_cancel_after_refund.get('refunded_money')}, "
        f"tenders present: {bool(paid_to_cancel_after_refund.get('tenders'))}, "
        f"refunds present: {bool(paid_to_cancel_after_refund.get('refunds'))}"
    )

    # Also check the payment object itself — the refund may show up there
    # (refunded_money/status) even though the order can never become CANCELED.
    paid_to_cancel_payment_after_refund = call("GET", f"/payments/{paid_to_cancel_payment['id']}")["payment"]
    results["paid_to_cancel_payment_after_refund"] = paid_to_cancel_payment_after_refund
    print(
        f"Payment after refund -> status {paid_to_cancel_payment_after_refund.get('status')}, "
        f"refunded_money {paid_to_cancel_payment_after_refund.get('refunded_money_money') or paid_to_cancel_payment_after_refund.get('refunded_money')}"
    )
else:
    cancelled_paid_order = cancel_attempt["order"]
    results["cancelled_paid_order_direct"] = cancelled_paid_order
    print(
        f"Cancelled a PAID order directly, no refund needed -> state {cancelled_paid_order['state']}, "
        f"net_amount_due {cancelled_paid_order.get('net_amount_due_money')}, "
        f"tenders present: {bool(cancelled_paid_order.get('tenders'))}"
    )

# 9. Read everything back the way StockSmith's sync would: SearchOrders, newest first.
found = call(
    "POST",
    "/orders/search",
    {
        "location_ids": [location_id],
        "limit": 10,
        "query": {"sort": {"sort_field": "UPDATED_AT", "sort_order": "DESC"}},
    },
)
results["search_orders"] = found
print(f"SearchOrders returned {len(found.get('orders', []))} order(s)")

with open("square_spike_output.json", "w") as handle:
    json.dump(results, handle, indent=2)
print("Full responses saved to square_spike_output.json")
