# eBay fee reporting (Digital Signatures)

## What was wrong

Every eBay order in StockSmith showed **Platform fees: Not yet settled**, permanently —
including orders eBay's own Seller Hub reported as complete with funds available for
payout. In the database, `payment_fees`, `payment_net` and `payment_status` were `NULL`
on every eBay order ever imported.

The cause was not settlement timing. eBay was rejecting the request outright:

```
GET https://api.ebay.com/sell/finances/v1/transaction?filter=orderId:{26-14962-77224}
→ 403
{"errors":[{"errorId":215001,"domain":"ACCESS","category":"REQUEST",
  "message":"Missing x-ebay-signature-key header",
  "longMessage":"Missing x-ebay-signature-key header to fulfill the request."}]}
```

eBay requires **Digital Signatures for APIs** — RFC 9421 HTTP Message Signatures — on its
in-scope APIs whenever they're called on behalf of an **EU or UK-domiciled seller**. The
Sell Finances API is in scope, and it is the only place eBay exposes a per-order fee
breakdown. StockSmith was sending unsigned requests, so it never got one.

Two things made this invisible for as long as it was:

1. `EbayAdapter._fetch_transactions` returned `(None, None, None)` on any non-200 with no
   log line. Nothing about the 403 reached `backend.log`.
2. The order page rendered `"Not yet settled"` for a missing fee — which is also exactly
   what a genuinely unsettled order looks like. There was no state the UI could show that
   would have distinguished them.

Order sync itself was never affected: eBay is not currently enforcing signatures on the
Fulfillment API for this seller, so order totals, postage and line items were always
correct. Only the fee number was missing — and because
`orders._compute_net_profit` treats a missing `payment_fees` as zero, **net profit was
overstated by the full fee on every eBay order**.

## Two things that only showed up against the live API

**The Finances API is served from `apiz.ebay.com`, not `api.ebay.com`.** StockSmith was
calling the wrong host all along. This was invisible while the requests were unsigned,
because eBay's proxy validates the signature *before* it routes: an unsigned request to
the wrong host returns the signature error, and only once the signature is correct does
the same request return a bodyless `404`. A wrong host looks exactly like a signing
problem right up until the signing is right.

**`amount` on a SALE transaction is already net of fees.** It is the credit eBay books to
the seller, not the gross the buyer paid — the gross is `totalFeeBasisAmount`. Order
26-14962-77224 came back as `amount` 15.14, `totalFeeBasisAmount` 18.59,
`totalFeeAmount` 3.45, and 18.59 − 3.45 = 15.14. The old code computed
`net = amount − fees`, which would have reported 11.69 against a real credit of 15.14 —
understating the net by the fee on every order, the same size of error as the original
bug in the opposite direction.

## Setting it up

Once, per environment (Sandbox and Production have entirely separate keysets):

**Settings > Integrations > eBay > Fee reporting signature > Set up**

That calls eBay's Key Management API (`POST /developer/key_management/v1/signing_key`
with `signingKeyCipher: ED25519`) using an application token from your existing Client
ID/Secret, and stores the resulting keypair against that credential row.

The private key is **returned exactly once and eBay keeps no copy**. StockSmith stores it
encrypted (`platform_app_credentials.signing_key_private`, same Fernet key as your client
secret). If it's lost, the only recovery is minting a new keypair — which is safe to do,
eBay allows several live keys per keyset, but it is not a restore.

## Backfilling orders already imported

Order sync will not repair existing orders on its own. `_parse_order` only enriches an
order whose `lastModifiedDate` is at or past the sync watermark, so an order imported
while the fee lookup was failing is skipped by exactly the sync that would fix it.

From `backend/`:

```bash
uv run python -m scripts.backfill_ebay_fees
```

That's a dry run — it prints what eBay returns for every eBay order missing a fee figure
and writes nothing. Confirm the numbers match Seller Hub, then:

```bash
uv run python -m scripts.backfill_ebay_fees --apply
```

It writes only `payment_fees`, `payment_net` and `payment_status`. Every other field on
those orders was already correct.

## What is and isn't signed

`_SIGNED_PATH_PREFIXES` in `services/platforms/ebay.py` currently lists `/sell/finances/`
and nothing else. That is deliberate: Fulfillment and Inventory work unsigned for this
seller today, and signing them would put working order sync and quantity pushes behind a
signature implementation that had no way to be verified in advance.

eBay is widening enforcement over time. The signal that a call needs adding to that tuple
is unambiguous — it starts failing with **403 errorId 215001**, and
`_fetch_transactions` now logs exactly that. Add the prefix; the transport handles the
rest.

## Implementation notes

- `services/platforms/ebay_signing.py` builds the signature base and headers. It is pure
  and session-free, so `app/tests/test_ebay_signing.py` pins the base byte for byte and
  verifies the real signature against the public half of the key that produced it.
- The covered components are `("x-ebay-signature-key" "@method" "@path" "@authority")`,
  with `"content-digest"` prepended when there's a body, and `"@signature-params"` always
  last. `@path` excludes the query string — covering it too is a well-travelled way to
  make every filtered call fail while unfiltered ones pass.
- Signed requests are built with `client.build_request` so the `Content-Digest` covers
  `request.content`, the exact bytes httpx puts on the wire, rather than a second
  serialization of the same dict.
- `_fetch_transactions` also had a genuine parsing bug behind the 403: it summed
  `totalFeeBasisAmount`, which is a single `Amount` object (iterating it yielded string
  keys that the `isinstance` guard then discarded, so the sum was always 0) and which is
  in any case the *basis* fees are calculated against — the gross the buyer paid — not
  the fee. It now reads `totalFeeAmount`, falling back to the per-line `marketplaceFees`
  breakdown (`FINAL_VALUE_FEE`, `FINAL_VALUE_FEE_FIXED_PER_ORDER`, `INTERNATIONAL_FEE`,
  `REGULATORY_OPERATING_FEE` — 2.65 + 0.48 + 0.24 + 0.08 = 3.45 on the order above).
- The postage label is a separate `SHIPPING_LABEL` transaction against the same order,
  and it comes *first* in eBay's response. Reading `transactions[0]` would report postage
  as the platform fee; StockSmith tracks postage separately, from the shipping profile.
- `app/tests/test_ebay_signing.py` uses the real captured payload as its fixture rather
  than an invented one, because none of the three properties above is guessable from the
  field names.
