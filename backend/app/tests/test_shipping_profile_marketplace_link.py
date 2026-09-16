"""Linking a shipping profile to Etsy / eBay and importing the buyer price (Stage 1 of
docs/plan-shipping-profile-marketplace-link.md).

The buyer-charged postage counts as revenue in product margin, so a wrong figure moves every
product's margin. These pin the three things that make it right: the marketplace payload is
read the way the plan says (domestic destination, calculated means nothing to import), the
per-channel price feeds the margin under the matching fee source and nothing else, and the
import endpoint refuses clearly rather than guessing.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.models.listing import ListingPlatform
from app.models.platform_fee import MarginFeeSource
from app.models.shipping_profile import ShippingProfile
from app.routers import shipping_profiles as router
from app.services import shipping_price_sync
from app.services.platforms.ebay import EbayAdapter
from app.services.platforms.errors import PlatformAuthError, PlatformSyncError
from app.services.platforms.etsy import EtsyAdapter
from app.services.pricing import compute_profit_margin
from app.services.shipping_profiles import (
    resolve_shipping_price_for_fee_source,
    resolve_shipping_price_for_platform,
)

ETSY = ListingPlatform.etsy
EBAY = ListingPlatform.ebay


def _money(amount: int, divisor: int = 100) -> dict:
    return {"amount": amount, "divisor": divisor, "currency_code": "GBP"}


def _etsy_profile(**overrides) -> dict:
    base = {
        "shipping_profile_id": 501,
        "title": "Small parcel",
        "profile_type": "manual",
        "origin_country_iso": "GB",
        "is_deleted": False,
        "shipping_profile_destinations": [
            {"destination_country_iso": "US", "primary_cost": _money(1250), "secondary_cost": _money(300)},
            {"destination_country_iso": "GB", "primary_cost": _money(360), "secondary_cost": _money(100)},
        ],
        "shipping_profile_upgrades": [{"upgrade_name": "Express", "price": _money(999)}],
    }
    base.update(overrides)
    return base


def _ebay_policy(**overrides) -> dict:
    base = {
        "fulfillmentPolicyId": "6001",
        "name": "Royal Mail 2nd class",
        "shippingOptions": [
            {
                "optionType": "INTERNATIONAL",
                "costType": "FLAT_RATE",
                "shippingServices": [{"sortOrder": 1, "shippingCost": {"value": "12.50", "currency": "GBP"}}],
            },
            {
                "optionType": "DOMESTIC",
                "costType": "FLAT_RATE",
                "shippingServices": [
                    {"sortOrder": 2, "shippingCost": {"value": "5.95", "currency": "GBP"}},
                    {"sortOrder": 1, "shippingCost": {"value": "3.20", "currency": "GBP"}},
                ],
            },
        ],
    }
    base.update(overrides)
    return base


class TestEtsyParsing:
    def test_domestic_destination_wins_and_upgrades_are_ignored(self):
        parsed = EtsyAdapter.parse_shipping_profile(_etsy_profile())
        assert parsed["id"] == 501 and parsed["title"] == "Small parcel"
        assert parsed["profile_type"] == "manual"
        assert parsed["origin_country_iso"] == "GB"
        # The GB row's primary_cost, not the US row (first) nor secondary_cost nor the upgrade.
        assert parsed["domestic_price"] == Decimal("3.60")
        assert parsed["domestic_fallback"] is False

    def test_falls_back_to_the_first_destination_and_says_so(self):
        """A profile that only ships abroad still has a price worth showing, but it must be
        labelled as a guess rather than presented as the domestic figure."""
        parsed = EtsyAdapter.parse_shipping_profile(
            _etsy_profile(
                shipping_profile_destinations=[
                    {"destination_country_iso": "US", "primary_cost": _money(1250)},
                    {"destination_region": "eu", "primary_cost": _money(800)},
                ]
            )
        )
        assert parsed["domestic_price"] == Decimal("12.50")
        assert parsed["domestic_fallback"] is True

    def test_calculated_profile_imports_nothing(self):
        parsed = EtsyAdapter.parse_shipping_profile(_etsy_profile(profile_type="calculated"))
        assert parsed["profile_type"] == "calculated"
        assert parsed["domestic_price"] is None

    def test_no_destinations_means_no_price_and_no_fallback_claim(self):
        parsed = EtsyAdapter.parse_shipping_profile(_etsy_profile(shipping_profile_destinations=[]))
        assert parsed["domestic_price"] is None
        assert parsed["domestic_fallback"] is False


class TestEbayParsing:
    def test_domestic_block_lowest_sort_order_service(self):
        parsed = EbayAdapter.parse_fulfillment_policy(_ebay_policy())
        assert parsed["id"] == "6001" and parsed["title"] == "Royal Mail 2nd class"
        assert parsed["cost_type"] == "FLAT_RATE"
        # DOMESTIC (not the INTERNATIONAL block listed first), then sortOrder 1 not 2.
        assert parsed["domestic_price"] == Decimal("3.20")
        assert parsed["domestic_fallback"] is False

    def test_calculated_cost_type_imports_nothing(self):
        policy = _ebay_policy()
        policy["shippingOptions"][1]["costType"] = "CALCULATED"
        parsed = EbayAdapter.parse_fulfillment_policy(policy)
        assert parsed["cost_type"] == "CALCULATED"
        assert parsed["domestic_price"] is None

    def test_no_domestic_block_falls_back_and_says_so(self):
        policy = _ebay_policy()
        policy["shippingOptions"] = [policy["shippingOptions"][0]]
        parsed = EbayAdapter.parse_fulfillment_policy(policy)
        assert parsed["domestic_price"] == Decimal("12.50")
        assert parsed["domestic_fallback"] is True


class TestPriceResolver:
    def _profile(self, **kwargs) -> ShippingProfile:
        return ShippingProfile(name="P", price=Decimal("3.00"), **kwargs)

    def test_each_fee_source_reads_its_own_channel(self):
        profile = self._profile(price_etsy=Decimal("3.60"), price_ebay=Decimal("4.10"))
        assert resolve_shipping_price_for_fee_source(profile, MarginFeeSource.etsy) == Decimal("3.60")
        assert resolve_shipping_price_for_fee_source(profile, MarginFeeSource.ebay) == Decimal("4.10")
        assert resolve_shipping_price_for_fee_source(profile, MarginFeeSource.manual) == Decimal("3.00")

    def test_unset_channel_falls_back_to_the_default_price(self):
        profile = self._profile(price_etsy=Decimal("3.60"))
        assert resolve_shipping_price_for_fee_source(profile, MarginFeeSource.ebay) == Decimal("3.00")
        assert resolve_shipping_price_for_platform(profile, EBAY) == Decimal("3.00")
        assert resolve_shipping_price_for_platform(profile, ETSY) == Decimal("3.60")
        assert resolve_shipping_price_for_platform(profile, None) == Decimal("3.00")


def test_margin_uses_the_etsy_price_under_the_etsy_fee_source_and_price_under_manual():
    """The whole reason the price is per channel: an imported Etsy figure must move the
    Etsy margin estimate and leave the manual one exactly where it was."""
    profile = ShippingProfile(name="P", price=Decimal("3.00"), price_etsy=Decimal("3.60"), cost_manual=Decimal("2"), cost_etsy=Decimal("2"))
    sale, cost, fee = Decimal("20"), Decimal("5"), Decimal("10")

    etsy_profit, _ = compute_profit_margin(
        sale, cost, Decimal("2"), fee, None, resolve_shipping_price_for_fee_source(profile, MarginFeeSource.etsy)
    )
    manual_profit, _ = compute_profit_margin(
        sale, cost, Decimal("2"), fee, None, resolve_shipping_price_for_fee_source(profile, MarginFeeSource.manual)
    )
    # revenue 23.60, fee 2.36 → 23.60 - 5 - 2 - 2.36 = 14.24
    assert etsy_profit == Decimal("14.24")
    # revenue 23.00, fee 2.30 → 23.00 - 5 - 2 - 2.30 = 13.70
    assert manual_profit == Decimal("13.70")


# --- Import endpoint ------------------------------------------------------------------------


async def _local_profile(session, **kwargs) -> ShippingProfile:
    profile = ShippingProfile(name="Small parcel", price=Decimal("3.00"), **kwargs)
    session.add(profile)
    await session.commit()
    await session.refresh(profile)
    return profile


@pytest.fixture
def marketplace(monkeypatch):
    """Scripts what the marketplace returns, in place of the adapter round trip. The
    connection lookup is bypassed too — these tests are about the import's decisions, not
    OAuth state."""

    def _use(profiles=None, *, error: Exception | None = None):
        async def _fetch(session, platform):
            if error is not None:
                raise error
            return list(profiles or [])

        monkeypatch.setattr(shipping_price_sync, "fetch_marketplace_profiles", _fetch)

    return _use


def _mp(id="501", title="Small parcel", price="3.60", calculated=False):
    return shipping_price_sync.MarketplaceProfile(
        id=id,
        title=title,
        is_calculated=calculated,
        domestic_price=Decimal(price) if price is not None else None,
        domestic_fallback=False,
    )


async def _call_import(session, profile_id: int, platform: ListingPlatform):
    return await router.import_shipping_profile_price(profile_id, platform, session=session)


class TestImportEndpoint:
    async def test_writes_only_the_channel_price(self, session, marketplace):
        profile = await _local_profile(session, etsy_shipping_profile_id=501, cost_etsy=Decimal("2.10"))
        marketplace([_mp()])

        result = await _call_import(session, profile.id, ETSY)

        assert Decimal(result.price_etsy) == Decimal("3.60")
        # The marketplace is the source of truth for the buyer price and nothing else.
        assert Decimal(result.price) == Decimal("3.00")
        assert result.price_ebay is None
        assert Decimal(result.cost_etsy) == Decimal("2.10")

    async def test_404_when_the_profile_is_not_linked(self, session, marketplace):
        profile = await _local_profile(session)
        marketplace([_mp()])
        with pytest.raises(HTTPException) as exc:
            await _call_import(session, profile.id, ETSY)
        assert exc.value.status_code == 404
        assert "not linked" in exc.value.detail

    async def test_404_when_the_linked_profile_vanished_upstream(self, session, marketplace):
        profile = await _local_profile(session, etsy_shipping_profile_id=999)
        marketplace([_mp()])
        with pytest.raises(HTTPException) as exc:
            await _call_import(session, profile.id, ETSY)
        assert exc.value.status_code == 404
        assert "no longer exists" in exc.value.detail
        await session.refresh(profile)
        assert profile.price_etsy is None

    async def test_409_when_the_marketplace_profile_is_calculated(self, session, marketplace):
        profile = await _local_profile(session, etsy_shipping_profile_id=501)
        marketplace([_mp(price=None, calculated=True)])
        with pytest.raises(HTTPException) as exc:
            await _call_import(session, profile.id, ETSY)
        assert exc.value.status_code == 409
        assert "calculated" in exc.value.detail

    async def test_sync_error_surfaces_as_the_reconnect_blocker(self, session, marketplace):
        profile = await _local_profile(session, etsy_shipping_profile_id=501)
        marketplace(error=PlatformSyncError("Etsy did not allow reading your shipping profiles. Reconnect Etsy"))
        with pytest.raises(HTTPException) as exc:
            await _call_import(session, profile.id, ETSY)
        assert exc.value.status_code == 502
        assert "Reconnect Etsy" in exc.value.detail

    async def test_auth_error_is_401(self, session, marketplace):
        profile = await _local_profile(session, ebay_fulfillment_policy_id="6001")
        marketplace(error=PlatformAuthError("token revoked"))
        with pytest.raises(HTTPException) as exc:
            await _call_import(session, profile.id, EBAY)
        assert exc.value.status_code == 401

    async def test_ebay_import_uses_the_string_policy_id(self, session, marketplace):
        profile = await _local_profile(session, ebay_fulfillment_policy_id="6001")
        marketplace([_mp(id="6001", title="Royal Mail", price="3.20")])
        result = await _call_import(session, profile.id, EBAY)
        assert Decimal(result.price_ebay) == Decimal("3.20")
        assert result.price_etsy is None


class TestLinkUniqueness:
    async def test_two_local_profiles_cannot_share_an_etsy_profile(self, session):
        from sqlalchemy.exc import IntegrityError

        await _local_profile(session, etsy_shipping_profile_id=501)
        session.add(ShippingProfile(name="Duplicate", price=Decimal("1"), etsy_shipping_profile_id=501))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_unlinked_profiles_do_not_collide(self, session):
        await _local_profile(session)
        session.add(ShippingProfile(name="Another unlinked", price=Decimal("1")))
        await session.commit()
