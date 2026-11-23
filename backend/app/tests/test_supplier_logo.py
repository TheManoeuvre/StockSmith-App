"""Supplier logos: favicon fetched from the website, upload overriding it, removal falling back.

The network is never touched — fetch_favicon is replaced with a stub, and its own parsing is
tested through the pure helpers. What matters here is the precedence rule: an upload is never
replaced by a favicon fetch, and removing it reverts to the favicon.
"""

import io

import pytest
from fastapi import HTTPException
from PIL import Image

from app.config import settings
from app.models.purchase import Purchase
from app.routers import suppliers as suppliers_router
from app.schemas.purchase import PurchaseRead
from app.schemas.supplier import SupplierCreate, SupplierUpdate
from app.models.supplier import Supplier
from app.services import supplier_logo


def _image_bytes(fmt: str = "PNG", size: tuple[int, int] = (400, 200), colour=(200, 30, 30, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, colour).save(buf, format=fmt if fmt != "ICO" else "ICO")
    return buf.getvalue()


class _FakeRequest:
    def __init__(self, body: bytes):
        self._body = body

    async def body(self) -> bytes:
        return self._body


@pytest.fixture(autouse=True)
def asset_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "asset_root", str(tmp_path))
    return tmp_path


@pytest.fixture
def favicon(monkeypatch):
    """Stub for the network fetch; set `favicon.result` to the PNG (or None) it should return."""

    class Stub:
        result: bytes | None = supplier_logo.normalise_logo(_image_bytes())
        calls: list[str] = []

    stub = Stub()
    stub.calls = []

    async def fake_fetch(url: str):
        stub.calls.append(url)
        return stub.result

    monkeypatch.setattr(supplier_logo, "fetch_favicon", fake_fetch)
    return stub


def test_normalise_shrinks_and_keeps_transparency():
    png = supplier_logo.normalise_logo(_image_bytes(size=(1000, 500), colour=(0, 0, 0, 0)))
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    assert max(image.size) == 256
    assert image.convert("RGBA").getpixel((0, 0))[3] == 0


def test_normalise_reads_ico():
    assert Image.open(io.BytesIO(supplier_logo.normalise_logo(_image_bytes("ICO", (32, 32))))).format == "PNG"


def test_normalise_rejects_non_images():
    with pytest.raises(ValueError):
        supplier_logo.normalise_logo(b'<svg xmlns="http://www.w3.org/2000/svg"/>')


def test_icon_candidates_prefer_touch_icons_and_resolve_relative_urls():
    html = """
        <link rel="stylesheet" href="/a.css">
        <link rel="shortcut icon" href="/favicon-32.png">
        <link href='/touch.png' rel='apple-touch-icon'>
    """
    assert supplier_logo._icon_candidates(html, "https://shop.example/page") == [
        "https://shop.example/touch.png",
        "https://shop.example/favicon-32.png",
    ]


async def test_create_with_website_fetches_favicon(session, favicon):
    row = await suppliers_router.create_supplier(
        SupplierCreate(name="Acme", website_url="https://acme.example"), session
    )
    assert favicon.calls == ["https://acme.example"]
    assert row.logo_source == "favicon" and row.logo_updated_at is not None
    assert (supplier_logo.asset_root() / row.logo_path).exists()


async def test_changing_website_refetches_but_other_edits_do_not(session, favicon):
    row = await suppliers_router.create_supplier(
        SupplierCreate(name="Acme", website_url="https://acme.example"), session
    )
    favicon.calls.clear()

    await suppliers_router.update_supplier(row.id, SupplierUpdate(name="Acme Ltd", website_url="https://acme.example"), session)
    assert favicon.calls == []

    await suppliers_router.update_supplier(row.id, SupplierUpdate(name="Acme Ltd", website_url="https://acme.test"), session)
    assert favicon.calls == ["https://acme.test"]


async def test_clearing_website_drops_the_favicon(session, favicon):
    row = await suppliers_router.create_supplier(
        SupplierCreate(name="Acme", website_url="https://acme.example"), session
    )
    path = supplier_logo.asset_root() / row.logo_path
    updated = await suppliers_router.update_supplier(row.id, SupplierUpdate(name="Acme", website_url=None), session)
    assert updated.logo_path is None and updated.logo_updated_at is None
    assert not path.exists()


async def test_upload_overrides_favicon_and_survives_website_change(session, favicon):
    row = await suppliers_router.create_supplier(
        SupplierCreate(name="Acme", website_url="https://acme.example"), session
    )
    row = await suppliers_router.upload_supplier_logo(row.id, _FakeRequest(_image_bytes("PNG", (64, 64))), session)
    assert row.logo_source == "upload"

    favicon.calls.clear()
    row = await suppliers_router.update_supplier(row.id, SupplierUpdate(name="Acme", website_url="https://other.example"), session)
    assert favicon.calls == []
    assert row.logo_source == "upload"

    with pytest.raises(HTTPException) as err:
        await suppliers_router.refresh_supplier_favicon(row.id, session)
    assert err.value.status_code == 400


async def test_removing_upload_reverts_to_favicon(session, favicon):
    row = await suppliers_router.create_supplier(
        SupplierCreate(name="Acme", website_url="https://acme.example"), session
    )
    await suppliers_router.upload_supplier_logo(row.id, _FakeRequest(_image_bytes()), session)
    row = await suppliers_router.delete_supplier_logo(row.id, session)
    assert row.logo_source == "favicon"


async def test_removing_upload_without_website_leaves_no_logo(session, favicon):
    row = await suppliers_router.create_supplier(SupplierCreate(name="Acme"), session)
    await suppliers_router.upload_supplier_logo(row.id, _FakeRequest(_image_bytes()), session)
    row = await suppliers_router.delete_supplier_logo(row.id, session)
    assert row.logo_source is None and row.logo_updated_at is None


async def test_upload_rejects_garbage(session, favicon):
    row = await suppliers_router.create_supplier(SupplierCreate(name="Acme"), session)
    with pytest.raises(HTTPException) as err:
        await suppliers_router.upload_supplier_logo(row.id, _FakeRequest(b"not an image"), session)
    assert err.value.status_code == 400


async def test_failed_refresh_keeps_existing_favicon(session, favicon):
    row = await suppliers_router.create_supplier(
        SupplierCreate(name="Acme", website_url="https://acme.example"), session
    )
    path = supplier_logo.asset_root() / row.logo_path
    favicon.result = None
    with pytest.raises(HTTPException):
        await suppliers_router.refresh_supplier_favicon(row.id, session)
    assert path.exists()
    assert (await session.get(Supplier, row.id)).logo_source == "favicon"


async def test_delete_supplier_removes_logo_file(session, favicon):
    row = await suppliers_router.create_supplier(
        SupplierCreate(name="Acme", website_url="https://acme.example"), session
    )
    path = supplier_logo.asset_root() / row.logo_path
    await suppliers_router.delete_supplier(row.id, session)
    assert not path.exists()


async def test_purchase_exposes_supplier_logo_stamp(session, favicon):
    row = await suppliers_router.create_supplier(
        SupplierCreate(name="Acme", website_url="https://acme.example"), session
    )
    purchase = Purchase(supplier_id=row.id, order_date=__import__("datetime").date(2026, 1, 1))
    session.add(purchase)
    await session.commit()
    await session.refresh(purchase, ["supplier", "lines"])
    assert PurchaseRead.model_validate(purchase).supplier_logo_updated_at == row.logo_updated_at
