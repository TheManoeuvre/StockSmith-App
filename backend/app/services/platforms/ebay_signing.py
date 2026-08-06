"""RFC 9421 HTTP Message Signatures, in the exact shape eBay's "Digital Signatures for
APIs" requires.

eBay enforces signed requests on its in-scope APIs when they're called on behalf of an
EU/UK-domiciled seller. StockSmith hit this on the Sell Finances API, which is the only
source of a marketplace fee breakdown: every getTransactions call came back

    403  errorId 215001  "Missing x-ebay-signature-key header to fulfill the request."

so every eBay order imported with payment_fees NULL and the order page showed "Not yet
settled" forever. The Fulfillment API is not being enforced for this seller today, which
is why order sync itself never broke — see _SIGNED_PATH_PREFIXES in ebay.py for why the
signing scope is deliberately narrow rather than "everything".

Four headers go on a signed request:

    x-ebay-signature-key   the JWE-wrapped public key from the Key Management API
    Content-Digest         SHA-256 of the body — only when there IS a body
    Signature-Input        which components are covered, and when it was signed
    Signature              Ed25519 over the signature base built from those components

The signature base is newline-joined `"name": value` lines, in the same order the
covered-component list names them, with "@signature-params" always last and NO trailing
newline. Getting any of that wrong produces eBay's 215120 rather than a useful message,
so the construction is kept in one pure function that build_signature_headers's own unit
tests can pin against eBay's published example.

Deliberately dependency-light and session-free: this knows nothing about connections,
credentials or HTTP clients, only how to turn (key, method, url, body) into headers.
"""

import base64
import hashlib
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_der_private_key, load_pem_private_key

from app.services.platforms.errors import PlatformSyncError

# RFC 9421 lets a message carry several signatures, each under its own label. eBay's
# examples all use sig1 and there is only ever one, so it's a constant.
_SIGNATURE_LABEL = "sig1"


class EbaySigningKeyMissing(PlatformSyncError):
    """No signing keypair is stored for this eBay environment.

    A distinct type because it is not a transport failure and retrying will never fix
    it — the operator has to mint a key (Settings > Integrations > eBay). Callers that
    can degrade gracefully catch this specifically; see EbayAdapter._fetch_transactions,
    which logs and returns no fee breakdown rather than failing the whole order sync over
    a missing fee number.
    """


@dataclass(frozen=True)
class EbaySigningKey:
    """One Ed25519 keypair as eBay's createSigningKey returned it.

    `private_key` is stored exactly as eBay sent it — base64 PKCS#8 DER, no PEM armour.
    eBay does not keep a copy: it is returned once, by createSigningKey, and never again
    by any endpoint. Losing it means minting a new keypair, so it is persisted encrypted
    (PlatformAppCredential.signing_key_private) rather than held in memory.
    """

    jwe: str
    private_key: str


def _load_private_key(raw: str) -> Ed25519PrivateKey:
    """eBay documents the returned privateKey as bare base64 PKCS#8 DER, but PEM-armoured
    values turn up in the wild (some of eBay's own SDK samples re-emit it that way, and an
    operator pasting a key by hand is likely to include the armour). Accept both rather
    than fail on a key that is perfectly valid."""
    text = raw.strip()
    try:
        if "-----BEGIN" in text:
            key = load_pem_private_key(text.encode("ascii"), password=None)
        else:
            key = load_der_private_key(base64.b64decode("".join(text.split())), password=None)
    except Exception as e:  # binascii, ValueError, UnsupportedAlgorithm — all mean the same thing here
        raise PlatformSyncError(
            "Stored eBay signing key could not be read — mint a new one in Settings > Integrations"
        ) from e
    if not isinstance(key, Ed25519PrivateKey):
        raise PlatformSyncError(
            f"Stored eBay signing key is {type(key).__name__}, not Ed25519 — mint a new one with "
            "signingKeyCipher ED25519"
        )
    return key


def content_digest(body: bytes) -> str:
    """RFC 9530 Content-Digest, the only algorithm eBay accepts. The colons are part of
    the wire format (a byte-sequence literal), not decoration."""
    return f"sha-256=:{base64.b64encode(hashlib.sha256(body).digest()).decode('ascii')}:"


def build_signature_headers(
    key: EbaySigningKey, method: str, url: str, body: bytes | None = None, created: int | None = None
) -> dict[str, str]:
    """The four (or three, bodyless) headers eBay requires on an in-scope request.

    `body` must be the exact bytes that will go on the wire — the digest covers them
    literally, so re-serializing the same dict a second time is a real risk of mismatch.
    Callers build the httpx.Request first and pass request.content (see
    EbayAdapter._request_once).

    `created` is exposed only so tests can pin a timestamp; production always signs with
    now. eBay rejects a signature whose created is too far from its own clock, so this is
    not a value worth caching alongside anything.
    """
    parts = urlsplit(url)

    # Order matters twice over: these tuples drive both the covered-component list and the
    # signature base lines, and RFC 9421 requires the two agree. content-digest leads when
    # present, matching eBay's published example.
    components: list[tuple[str, str]] = []
    headers: dict[str, str] = {}
    if body:
        digest = content_digest(body)
        headers["Content-Digest"] = digest
        components.append(("content-digest", digest))
    components.append(("x-ebay-signature-key", key.jwe))
    components.append(("@method", method.upper()))
    # @path is the path alone. eBay does NOT cover @query, so a filtered getTransactions
    # signs the same base as an unfiltered one — signing the query string instead is a
    # well-travelled way to get 215120 on every parameterised call.
    components.append(("@path", parts.path or "/"))
    components.append(("@authority", parts.netloc))

    covered = " ".join(f'"{name}"' for name, _ in components)
    signature_params = f"({covered});created={created if created is not None else int(time.time())}"

    lines = [f'"{name}": {value}' for name, value in components]
    lines.append(f'"@signature-params": {signature_params}')
    signature = _load_private_key(key.private_key).sign("\n".join(lines).encode("utf-8"))

    headers["x-ebay-signature-key"] = key.jwe
    headers["Signature-Input"] = f"{_SIGNATURE_LABEL}={signature_params}"
    headers["Signature"] = f"{_SIGNATURE_LABEL}=:{base64.b64encode(signature).decode('ascii')}:"
    return headers
