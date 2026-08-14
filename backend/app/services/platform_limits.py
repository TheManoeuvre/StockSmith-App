"""Per-platform field limits, and the "most restrictive active platform wins" resolver.

Marketplaces enforce different caps on the same field — a SKU may be 50 characters on
eBay and 32 on Etsy — so a value StockSmith generates has to satisfy the strictest limit
among the platforms that product actually targets, not the limit of whichever platform
happens to be pushed to first.

Where the numbers live, and why they're split:

  * `_DEFAULT_LIMITS` below is the shipped truth, in code, with a provenance comment on
    every entry. These are facts about a third party, so the comment records how well we
    actually know each one — CONFIRMED against the marketplace's own schema, versus
    inferred from documentation nobody has tested. That distinction is worth more than
    the number itself when one of them turns out to be wrong.

  * A `platform_field_limits` row overrides one entry (added in a later stage). The
    table stores *only* overrides and ships empty, so a corrected default in a new
    release reaches every install. Seeding the table with the defaults instead would
    mean the opposite: whoever edited one field would be frozen on the old values for
    every other field forever (the trap `_ensure_platform_fee_components` already has —
    it returns early if any row exists).

The check functions are deliberately pure and session-free. Everything interesting here
is "does this string fit that number", and keeping it out of the database makes the whole
matrix testable without one.
"""

from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass

from app.models.listing import ListingPlatform


class LimitField(str, enum.Enum):
    """The fields a marketplace caps that StockSmith can actually violate.

    Deliberately not exhaustive: a limit nothing in this app can breach is noise in the
    settings UI and one more number to keep correct for no benefit."""

    sku_max_length = "sku_max_length"
    title_max_length = "title_max_length"
    title_charset = "title_charset"
    description_max_length = "description_max_length"
    variation_attribute_max_count = "variation_attribute_max_count"
    variation_max_count = "variation_max_count"
    attribute_name_max_length = "attribute_name_max_length"
    attribute_value_max_length = "attribute_value_max_length"
    attribute_value_charset = "attribute_value_charset"
    image_max_count = "image_max_count"
    price_decimal_places = "price_decimal_places"
    quantity_max = "quantity_max"


class Severity(str, enum.Enum):
    """Whether a violation stops a listing being created, or merely needs adjusting.

    A blocker is something no transformation fixes: the marketplace rejects the call, or
    "fixing" it would change what is being sold. A warning is something with an obviously
    correct automatic answer that a human should still get to see."""

    blocker = "blocker"
    warning = "warning"


# Every limit that is a maximum *length or count* — i.e. the ones where "most
# restrictive" means "smallest". Charset limits aren't ordered and are handled
# separately (a platform either forbids a character or it doesn't).
_NUMERIC_FIELDS = frozenset(
    {
        LimitField.sku_max_length,
        LimitField.title_max_length,
        LimitField.description_max_length,
        LimitField.variation_attribute_max_count,
        LimitField.variation_max_count,
        LimitField.attribute_name_max_length,
        LimitField.attribute_value_max_length,
        LimitField.image_max_count,
        LimitField.price_decimal_places,
        LimitField.quantity_max,
    }
)

_SEVERITY: dict[LimitField, Severity] = {
    # The SKU is the identity key every sync, push and adoption path hangs off, and the
    # marketplace rejects an over-length one outright.
    LimitField.sku_max_length: Severity.blocker,
    # Nothing can be truncated away here - dropping an attribute changes what is being
    # sold, so there is no automatic fix to offer.
    LimitField.variation_attribute_max_count: Severity.blocker,
    # Same: no subset of variations is a safe automatic choice.
    LimitField.variation_max_count: Severity.blocker,
    LimitField.title_max_length: Severity.warning,
    LimitField.title_charset: Severity.warning,
    LimitField.description_max_length: Severity.warning,
    LimitField.attribute_name_max_length: Severity.warning,
    LimitField.attribute_value_max_length: Severity.warning,
    LimitField.attribute_value_charset: Severity.warning,
    LimitField.image_max_count: Severity.warning,
    LimitField.price_decimal_places: Severity.warning,
    LimitField.quantity_max: Severity.warning,
}

# Human labels for messages. Kept here rather than on the frontend so the backend can
# produce a complete sentence — the house style (see variants._collision_messages) is
# that an error names the offending thing and the fix, not just a field code.
_FIELD_LABELS: dict[LimitField, str] = {
    LimitField.sku_max_length: "SKU",
    LimitField.title_max_length: "Title",
    LimitField.title_charset: "Title",
    LimitField.description_max_length: "Description",
    LimitField.variation_attribute_max_count: "Variation attributes",
    LimitField.variation_max_count: "Variations",
    LimitField.attribute_name_max_length: "Attribute name",
    LimitField.attribute_value_max_length: "Attribute value",
    LimitField.attribute_value_charset: "Attribute value",
    LimitField.image_max_count: "Images",
    LimitField.price_decimal_places: "Price",
    LimitField.quantity_max: "Quantity",
}


_DEFAULT_LIMITS: dict[ListingPlatform, dict[LimitField, int | str]] = {
    ListingPlatform.etsy: {
        # UNPUBLISHED. Etsy's OpenAPI spec declares ListingInventoryProduct.sku as a
        # bare string with no maxLength, and updateListingInventory types its request
        # `products` as an untyped object[] — so there is no authoritative number to
        # read. 32 is the conservative choice and is empirically proven accepted: the
        # live shop carries 32-character SKUs in state=active. Treat as a lower bound,
        # not a confirmed ceiling; raise it via an override if a longer one is accepted.
        LimitField.sku_max_length: 32,
        # From Etsy's seller documentation. Not in the OpenAPI schema.
        LimitField.title_max_length: 140,
        # CONFIRMED - Etsy's spec gives this as /[^\p{L}\p{Nd}\p{P}\p{Sm}\p{Zs}TM(c)(R)]/u,
        # expressed here as Unicode general categories rather than a character class
        # because Python's `re` has no \p{} support, and the obvious ASCII approximation
        # is wrong in a way that matters: an em dash is \p{Pd}, so Etsy accepts it, but a
        # hand-written class omits it and the report fills with false positives on
        # perfectly valid titles. The % : & + characters carry an additional once-each
        # rule that check_charset applies separately.
        LimitField.title_charset: "allow:L,Nd,P,Sm,Zs:™©®",
        LimitField.description_max_length: 13000,
        # CONFIRMED via updateListingInventory's max_variations_supported parameter,
        # which documents 2 as today's behaviour and 3 as opt-in. Etsy has announced
        # third-variation GA but it requires shop-side enrolment in developer mode, so
        # this ships at 2 and is raised by an override once the shop is enrolled.
        LimitField.variation_attribute_max_count: 2,
        LimitField.variation_max_count: 100,
        LimitField.attribute_name_max_length: 45,
        LimitField.attribute_value_max_length: 45,
        # CONFIRMED - ListingInventoryProduct.property_values in Etsy's spec states
        # "parenthesis characters ( and ) are not allowed". A deny rule rather than an
        # allow rule because that is exactly what Etsy documents: everything else is fine.
        LimitField.attribute_value_charset: "deny:[()]",
        # CONFIRMED — createDraftListing's image_ids says "up to 20 images".
        LimitField.image_max_count: 20,
        LimitField.price_decimal_places: 2,
        LimitField.quantity_max: 999,
    },
    ListingPlatform.ebay: {
        # From eBay's Inventory API documentation. UNVERIFIED against a live account —
        # the same caveat every other eBay surface in this codebase carries.
        LimitField.sku_max_length: 50,
        LimitField.title_max_length: 80,
        LimitField.description_max_length: 500000,
        LimitField.variation_attribute_max_count: 5,
        LimitField.variation_max_count: 250,
        LimitField.attribute_name_max_length: 65,
        LimitField.attribute_value_max_length: 65,
        LimitField.image_max_count: 24,
        LimitField.price_decimal_places: 2,
        LimitField.quantity_max: 999999999,
    },
    # Shopify is in ListingPlatform but has no adapter, no OAuth scopes and no draft
    # path. Giving it limits would make it silently participate in "most restrictive
    # wins" for products it can never be listed on. It gets a row when it gets an
    # adapter.
}

# Etsy allows these four in a title, but only once each.
_ETSY_ONCE_ONLY = ("%", ":", "&", "+")


@dataclass(frozen=True)
class Limit:
    """One resolved limit, carrying which platform imposed it.

    The platform is not decoration. Every message this feeds has to name the culprit —
    "32 characters (Etsy's limit)" tells the user which store to exclude or which value
    to shorten; a bare "32 characters" leaves them guessing."""

    field: LimitField
    value: int | str
    platform: ListingPlatform
    is_override: bool = False

    @property
    def int_value(self) -> int:
        if not isinstance(self.value, int):
            raise TypeError(f"{self.field.value} is not a numeric limit")
        return self.value


@dataclass(frozen=True)
class Violation:
    field: LimitField
    severity: Severity
    current_value: str
    limit: Limit
    message: str
    # Set only where a mechanical fix exists and is unambiguous. None means "a human has
    # to decide" — deliberately not a truncation the caller might apply blindly.
    suggested_value: str | None = None
    current_length: int | None = None

    @property
    def imposed_by(self) -> ListingPlatform:
        return self.limit.platform


# A whole resolved matrix: every platform's limits, defaults with any stored overrides
# already applied. Passed around explicitly so the check functions stay pure and a caller
# decides once whether overrides are in play, rather than each check reaching for a
# session.
LimitTable = dict[ListingPlatform, dict[LimitField, Limit]]


def default_limits(platform: ListingPlatform, table: LimitTable | None = None) -> dict[LimitField, Limit]:
    """One platform's limits, defaults unless an override table is supplied.

    A platform with no entry (Shopify today) resolves to an empty dict rather than
    raising: it simply imposes no constraints, which is the correct behaviour for a
    platform nothing can be listed on."""
    if table is not None:
        return table.get(platform, {})
    return {
        field: Limit(field=field, value=value, platform=platform)
        for field, value in _DEFAULT_LIMITS.get(platform, {}).items()
    }


def default_limit_table() -> LimitTable:
    return {platform: default_limits(platform) for platform in _DEFAULT_LIMITS}


# Overrides change rarely and are read on every compatibility scan, so they are cached for
# the process. invalidate_limits_cache() is called from the override CRUD for the same
# reason routers/platforms.py invalidates the adapter cache after a credential write: a
# changed limit has to take effect on the very next request, not after a restart.
_cached_table: LimitTable | None = None


def invalidate_limits_cache() -> None:
    global _cached_table
    _cached_table = None


async def load_limit_table(session) -> LimitTable:
    """The shipped defaults with any stored overrides applied on top.

    An override for a platform that has no defaults at all is ignored rather than creating
    a lone limit out of nothing — a half-specified platform would constrain values while
    being unable to list them."""
    global _cached_table
    if _cached_table is not None:
        return _cached_table

    from sqlalchemy import select

    from app.models.platform_limits import PlatformFieldLimit

    table = default_limit_table()
    rows = (await session.execute(select(PlatformFieldLimit))).scalars()
    for row in rows:
        platform_limits = table.get(row.platform)
        if platform_limits is None:
            continue
        value = row.int_value if row.int_value is not None else row.text_value
        if value is None:
            continue
        platform_limits[row.field_key] = Limit(
            field=row.field_key, value=value, platform=row.platform, is_override=True
        )
    _cached_table = table
    return table


def supported_platforms() -> list[ListingPlatform]:
    """Platforms that actually have a limits row — i.e. the ones a compatibility report
    can say anything meaningful about."""
    return list(_DEFAULT_LIMITS)


def resolve_effective_limits(
    platforms: set[ListingPlatform] | list[ListingPlatform], table: LimitTable | None = None
) -> dict[LimitField, Limit]:
    """The strictest limit for each field across the given platforms.

    "Strictest" means smallest for a maximum, and for a charset it means every platform's
    rule applies — a character forbidden anywhere is forbidden, since the same value has
    to be pushed to all of them. Charsets therefore don't collapse to one winner; the
    first platform to declare one holds the entry and _check_charset consults each
    platform's rule in turn via `charset_rules`.

    An empty platform set yields no limits, which is the honest answer: with nothing
    targeted there is nothing to conform to."""
    effective: dict[LimitField, Limit] = {}
    for platform in sorted(platforms, key=lambda p: p.value):
        for field, limit in default_limits(platform, table).items():
            current = effective.get(field)
            if current is None:
                effective[field] = limit
            elif field in _NUMERIC_FIELDS and limit.int_value < current.int_value:
                effective[field] = limit
    return effective


def charset_rules(
    platforms: set[ListingPlatform] | list[ListingPlatform],
    field: LimitField,
    table: LimitTable | None = None,
) -> list[Limit]:
    """Every platform's rule for a charset field, since these don't reduce to one winner.

    A character Etsy forbids and eBay allows is still unusable on a value pushed to both,
    so all rules apply simultaneously and each needs to be reported against the platform
    that owns it."""
    rules = []
    for platform in sorted(platforms, key=lambda p: p.value):
        limit = default_limits(platform, table).get(field)
        if limit is not None:
            rules.append(limit)
    return rules


def _describe(limit: Limit) -> str:
    """"Etsy's limit of 32" — the platform is named because every message built from this
    has to tell the user which store to act on, and the value is stated once."""
    return f"{limit.platform.value.capitalize()}'s limit of {limit.value}"


def check_length(field: LimitField, value: str | None, effective: dict[LimitField, Limit]) -> Violation | None:
    """Checks one string against a max-length limit. None and empty pass — an absent
    value is a completeness question, not a conformance one, and the draft-readiness
    check owns that distinction."""
    limit = effective.get(field)
    if limit is None or not value:
        return None
    length = len(value)
    if length <= limit.int_value:
        return None
    label = _FIELD_LABELS[field]
    return Violation(
        field=field,
        severity=_SEVERITY[field],
        current_value=value,
        current_length=length,
        limit=limit,
        message=f"{label} is {length} characters, over {_describe(limit)}.",
        suggested_value=_truncate(value, limit.int_value) if _SEVERITY[field] == Severity.warning else None,
    )


def check_count(field: LimitField, count: int, label_noun: str, effective: dict[LimitField, Limit]) -> Violation | None:
    limit = effective.get(field)
    if limit is None or count <= limit.int_value:
        return None
    return Violation(
        field=field,
        severity=_SEVERITY[field],
        current_value=str(count),
        current_length=count,
        limit=limit,
        message=f"{count} {label_noun}, over {_describe(limit)}.",
    )


def _parse_charset(spec: str) -> tuple[str, str]:
    """Splits a stored charset rule into (kind, payload).

    Two forms, both plain strings so a platform_field_limits row can override either:

      allow:<categories>:<extras>  every character must be in one of the listed Unicode
                                   general categories (prefix match, so "L" covers
                                   Lu/Ll/Lt/Lm/Lo) or in the literal extras.
      deny:<regex>                 characters matching the regex are rejected; anything
                                   else is fine.

    Allow-by-category exists because marketplaces describe these rules in Unicode
    property terms and Python's re cannot. Hand-expanding one into ASCII silently drops
    whole categories of legitimate character.
    """
    kind, _, payload = spec.partition(":")
    return kind, payload


def _charset_offenders(spec: str, value: str) -> list[str]:
    kind, payload = _parse_charset(spec)
    if kind == "deny":
        return sorted(set(re.findall(payload, value)))
    if kind == "allow":
        categories, _, extras = payload.partition(":")
        allowed = tuple(c for c in categories.split(",") if c)
        return sorted(
            {
                char
                for char in value
                if char not in extras and not unicodedata.category(char).startswith(allowed)
            }
        )
    raise ValueError(f"Unrecognised charset rule: {spec!r}")


def _charset_strip(spec: str, value: str) -> str:
    offenders = set(_charset_offenders(spec, value))
    return "".join(char for char in value if char not in offenders).strip()


def check_charset(
    field: LimitField,
    value: str | None,
    platforms: set[ListingPlatform] | list[ListingPlatform],
    table: LimitTable | None = None,
) -> Violation | None:
    """Checks a value against every targeted platform's character rule.

    Takes the platform set rather than resolved limits because charsets don't reduce to a
    single winner - see resolve_effective_limits."""
    if not value:
        return None
    for limit in charset_rules(platforms, field, table):
        spec = str(limit.value)
        bad = _charset_offenders(spec, value)
        if bad:
            label = _FIELD_LABELS[field]
            shown = " ".join(repr(c) for c in bad[:5])
            return Violation(
                field=field,
                severity=_SEVERITY[field],
                current_value=value,
                limit=limit,
                message=(
                    f"{label} contains {len(bad)} character(s) {limit.platform.value.capitalize()} "
                    f"does not accept: {shown}."
                ),
                suggested_value=_charset_strip(spec, value),
            )
        if field == LimitField.title_charset and limit.platform == ListingPlatform.etsy:
            overused = [c for c in _ETSY_ONCE_ONLY if value.count(c) > 1]
            if overused:
                shown = " ".join(repr(c) for c in overused)
                return Violation(
                    field=field,
                    severity=_SEVERITY[field],
                    current_value=value,
                    limit=limit,
                    message=(
                        f"Title uses {shown} more than once. Etsy allows each of "
                        "% : & + only once."
                    ),
                )
    return None


def _truncate(value: str, max_length: int) -> str:
    """Truncates at a word boundary where one is reasonably close, otherwise hard-cuts.

    No ellipsis: Etsy's title charset permits it, but a trailing "…" on a listing title
    reads as a rendering bug to a buyer rather than as deliberate brevity."""
    if len(value) <= max_length:
        return value
    cut = value[:max_length].rstrip()
    space = cut.rfind(" ")
    # Only honour a word boundary in the last quarter — otherwise a single long token
    # would strand most of the budget unused.
    if space > max_length * 0.75:
        cut = cut[:space].rstrip()
    return cut


@dataclass(frozen=True)
class UnitViolations:
    """Violations for one checkable unit — the product itself when it has no active
    variants, otherwise one per active variant. Mirrors listing_sync._unit_checks so the
    compatibility report and the sync check agree on what a "unit" is."""

    variant_id: int | None
    variant_name: str | None
    sku: str | None
    violations: list[Violation]


def check_product(
    product,
    active_variants: list,
    effective: dict[LimitField, Limit],
    platforms: set[ListingPlatform] | list[ListingPlatform],
    *,
    image_count: int = 0,
    table: LimitTable | None = None,
) -> tuple[list[Violation], list[UnitViolations]]:
    """Checks one product and its variants against the resolved limits.

    Pure: takes already-loaded ORM objects and returns findings, touching no session. The
    caller loads products and variants in two queries and calls this per product, which
    keeps a whole-catalogue scan free of N+1 (the same shape as listing_sync.tracked_skus).

    Returns (product-level violations, per-unit violations). The split matters because a
    product-level breach — too many variation attributes, a title over the cap — blocks
    the whole listing, while a unit-level one (an over-length SKU on one variant) is
    specific to that variant and is where the user has to go to fix it.
    """
    from app.services.variants import compute_full_sku

    product_violations: list[Violation] = []

    for violation in (
        check_length(LimitField.title_max_length, product.name, effective),
        check_charset(LimitField.title_charset, product.name, platforms, table),
        check_length(LimitField.description_max_length, product.description, effective),
    ):
        if violation is not None:
            product_violations.append(violation)

    attribute_names = [
        name
        for name in (
            product.variant_attribute1_name,
            product.variant_attribute2_name,
            product.variant_attribute3_name,
        )
        if name and name.strip()
    ]
    counted = check_count(
        LimitField.variation_attribute_max_count,
        len(attribute_names),
        "variation attributes",
        effective,
    )
    if counted is not None:
        product_violations.append(counted)

    for name in attribute_names:
        violation = check_length(LimitField.attribute_name_max_length, name, effective)
        if violation is not None:
            product_violations.append(violation)

    if active_variants:
        counted = check_count(
            LimitField.variation_max_count, len(active_variants), "variations", effective
        )
        if counted is not None:
            product_violations.append(counted)

    counted = check_count(LimitField.image_max_count, image_count, "images", effective)
    if counted is not None:
        product_violations.append(counted)

    units: list[UnitViolations] = []
    if not active_variants:
        units.append(
            UnitViolations(
                variant_id=None,
                variant_name=None,
                sku=product.sku,
                violations=_check_unit(
                    product.sku, [], product.current_stock, effective, platforms, table
                ),
            )
        )
    else:
        for variant in active_variants:
            sku = compute_full_sku(product.sku, variant.sku_suffix)
            values = [
                value
                for value in (variant.attribute1_value, variant.attribute2_value, variant.attribute3_value)
                if value and value.strip()
            ]
            units.append(
                UnitViolations(
                    variant_id=variant.id,
                    variant_name=variant.variant_name,
                    sku=sku,
                    violations=_check_unit(
                        sku, values, variant.current_stock, effective, platforms, table
                    ),
                )
            )

    return product_violations, units


def _check_unit(
    sku: str | None,
    attribute_values: list[str],
    stock: int | None,
    effective: dict[LimitField, Limit],
    platforms: set[ListingPlatform] | list[ListingPlatform],
    table: LimitTable | None = None,
) -> list[Violation]:
    violations: list[Violation] = []

    violation = check_length(LimitField.sku_max_length, sku, effective)
    if violation is not None:
        violations.append(violation)

    for value in attribute_values:
        for found in (
            check_length(LimitField.attribute_value_max_length, value, effective),
            check_charset(LimitField.attribute_value_charset, value, platforms, table),
        ):
            if found is not None:
                violations.append(found)

    if stock is not None:
        counted = check_count(LimitField.quantity_max, stock, "in stock", effective)
        if counted is not None:
            violations.append(counted)

    return violations
