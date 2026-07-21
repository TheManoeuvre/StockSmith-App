from fastapi import HTTPException, status

from app.config import settings
from app.models.listing import ListingPlatform
from app.services.platforms.base import PlatformAdapter
from app.services.platforms.ebay import EbayAdapter
from app.services.platforms.etsy import EtsyAdapter

_etsy_adapter: EtsyAdapter | None = None
_ebay_adapter: EbayAdapter | None = None


def get_adapter(platform: ListingPlatform) -> PlatformAdapter:
    """Registry keyed on ListingPlatform — core sync/allocation logic depends on the
    PlatformAdapter Protocol, not on this function's internals, so adding a new
    marketplace is additive here only: a new adapter class + one branch."""
    if platform == ListingPlatform.etsy:
        global _etsy_adapter
        if _etsy_adapter is None:
            if not settings.etsy_client_id or not settings.etsy_client_secret:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Etsy is not configured — set etsy_client_id/etsy_client_secret",
                )
            _etsy_adapter = EtsyAdapter(settings.etsy_client_id, settings.etsy_client_secret)
        return _etsy_adapter
    if platform == ListingPlatform.ebay:
        global _ebay_adapter
        if _ebay_adapter is None:
            if not settings.ebay_client_id or not settings.ebay_client_secret:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="eBay is not configured — set ebay_client_id/ebay_client_secret",
                )
            _ebay_adapter = EbayAdapter(settings.ebay_client_id, settings.ebay_client_secret)
        return _ebay_adapter
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported platform: {platform.value}")
