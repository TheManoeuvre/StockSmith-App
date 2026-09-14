"""Tests for `deps.require_auth`'s verified-token cache.

`require_auth` runs a cost-12 `bcrypt.checkpw` on the event loop for every `/api/v1`
request. There is one valid secret per install and it doesn't change while the process
runs, so a verified `(token, hash)` pair is remembered and later requests skip bcrypt
entirely. These tests pin the three things that keeps honest: a wrong token never gets
in and is never cached, a hash swap invalidates a cached pair, and the happy path only
pays bcrypt once.
"""

import pytest
from fastapi import HTTPException

from app import deps
from app.security import hash_password

_PASSWORD = "correct-horse-battery-staple"
_HASH = hash_password(_PASSWORD)


@pytest.fixture(autouse=True)
def _fixed_hash_and_clean_cache(monkeypatch):
    monkeypatch.setattr(deps.settings, "shared_password_hash", _HASH)
    deps.reset_auth_cache()
    yield
    deps.reset_auth_cache()


async def test_valid_token_passes_and_is_cached(monkeypatch):
    calls = _count_verify_calls(monkeypatch)

    await deps.require_auth(f"Bearer {_PASSWORD}")
    await deps.require_auth(f"Bearer {_PASSWORD}")
    await deps.require_auth(f"Bearer {_PASSWORD}")

    assert calls() == 1  # bcrypt ran once; the next two were cache hits


async def test_wrong_token_is_rejected_every_time_and_never_cached(monkeypatch):
    calls = _count_verify_calls(monkeypatch)

    for _ in range(3):
        with pytest.raises(HTTPException) as exc:
            await deps.require_auth("Bearer not-the-password")
        assert exc.value.status_code == 401

    assert calls() == 3  # no shortcut for a bad token — full bcrypt each time


async def test_missing_or_malformed_header_is_rejected_without_bcrypt(monkeypatch):
    calls = _count_verify_calls(monkeypatch)

    for header in (None, "", "Basic abc", "Bearer", _PASSWORD):
        with pytest.raises(HTTPException) as exc:
            await deps.require_auth(header)
        assert exc.value.status_code == 401

    assert calls() == 0


async def test_hash_swap_invalidates_a_cached_pair(monkeypatch):
    await deps.require_auth(f"Bearer {_PASSWORD}")  # cached against _HASH

    new_password = "a-different-secret"
    monkeypatch.setattr(deps.settings, "shared_password_hash", hash_password(new_password))

    with pytest.raises(HTTPException):
        await deps.require_auth(f"Bearer {_PASSWORD}")  # old token no longer matches
    await deps.require_auth(f"Bearer {new_password}")  # new one does


async def test_reset_auth_cache_forces_reverification(monkeypatch):
    calls = _count_verify_calls(monkeypatch)

    await deps.require_auth(f"Bearer {_PASSWORD}")
    deps.reset_auth_cache()
    await deps.require_auth(f"Bearer {_PASSWORD}")

    assert calls() == 2


def _count_verify_calls(monkeypatch):
    """Wrap `deps.verify_password` with a counter, returning a getter for the count."""
    n = 0
    real = deps.verify_password

    def counting(password: str, password_hash: str) -> bool:
        nonlocal n
        n += 1
        return real(password, password_hash)

    monkeypatch.setattr(deps, "verify_password", counting)
    return lambda: n
