"""eBay error payloads are rendered as plain English for the Settings alert box — the raw
JSON they replaced was unreadable there. Payloads are copied from the live prod log."""

from app.services.platforms.ebay import _describe_ebay_errors

# bulkUpdatePriceQuantity rejecting a SKU in a listing that varies by "Model", which eBay
# stopped allowing as a variation specific in category 185065 (Sept 2026). The per-SKU and
# per-offer responses both come back 400; only the offer one carries errors, and a
# business-policies warning rides along on every response.
_BULK_25002 = {
    "responses": [
        {"statusCode": 400, "sku": "SKU-0023-BUTTON-DIAL"},
        {
            "statusCode": 400,
            "sku": "SKU-0023-BUTTON-DIAL",
            "offerId": "223503396011",
            "errors": [
                {
                    "errorId": 25002,
                    "domain": "API_INVENTORY",
                    "subdomain": "Selling",
                    "category": "REQUEST",
                    "message": "A user error has occurred. Model is not allowed as a variation specific.",
                    "parameters": [
                        {"name": "0", "value": "Invalid variation specific"},
                        {"name": "1", "value": "Model is not allowed as a variation specific."},
                        {"name": "2", "value": "90"},
                        {"name": "3", "value": "1"},
                        {"name": "4", "value": "Model"},
                        {"name": "5", "value": ""},
                    ],
                }
            ],
            "warnings": [
                {
                    "errorId": 25402,
                    "message": "System warning. Seller has opted into business policies.",
                }
            ],
        },
    ]
}


def test_bulk_rejection_reads_as_one_plain_sentence():
    assert _describe_ebay_errors(_BULK_25002) == "Model is not allowed as a variation specific. (eBay error 25002)"


def test_boilerplate_only_message_falls_back_to_prose_parameters():
    payload = {
        "errors": [
            {
                "errorId": 25002,
                "message": "A user error has occurred.",
                "parameters": [
                    {"name": "0", "value": "Model is not allowed as a variation specific"},
                    {"name": "1", "value": "90"},
                ],
            }
        ]
    }
    assert _describe_ebay_errors(payload) == "Model is not allowed as a variation specific. (eBay error 25002)"


def test_duplicate_errors_across_responses_collapse():
    error = {"errorId": 25001, "message": "A system error has occurred. Dependent service failure"}
    payload = {"responses": [{"errors": [error]}, {"errors": [error]}]}
    assert _describe_ebay_errors(payload) == "Dependent service failure. (eBay error 25001)"


def test_nothing_recognisable_returns_none():
    assert _describe_ebay_errors({"responses": [{"statusCode": 400, "sku": "X"}]}) is None
    assert _describe_ebay_errors({}) is None
