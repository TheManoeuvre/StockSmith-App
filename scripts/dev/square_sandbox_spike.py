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


def call(method: str, path: str, body: dict | None = None) -> dict:
    response = httpx.request(method, f"{BASE}{path}", headers=HEADERS, json=body, timeout=30)
    data = response.json()
    if response.status_code >= 400:
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

# 6. Read everything back the way StockSmith's sync would: SearchOrders, newest first.
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
