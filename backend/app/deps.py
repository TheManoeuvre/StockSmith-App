from collections.abc import AsyncIterator

from fastapi import Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session_factory
from app.security import verify_password


async def get_db() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session


async def require_host(request: Request) -> None:
    """Allow only requests originating on the machine that runs the backend.

    Restore is host-only for a mechanical reason, not a policy one. Applying a restore requires
    the backend process to stop and come back (see bootstrap._maybe_apply_staged_restore), and
    only the Tauri shell on the host can restart its own sidecar — the shell spawns it once at
    startup and never respawns it. A thin client that triggered a restore would kill the backend
    for every device including itself, with nothing left able to bring it back.

    `request.client.host` is trustworthy here *specifically* because nothing sits in front of
    this app: no reverse proxy, no ProxyHeadersMiddleware, so an X-Forwarded-For header cannot
    influence it. If a proxy is ever introduced, this check silently becomes spoofable and must
    be revisited.
    """
    host = request.client.host if request.client else None
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Restore can only be run on the computer that hosts StockSmith.",
        )


# Bearer tokens already bcrypt-verified against a given hash, this process.
#
# `verify_password` is a cost-12 `bcrypt.checkpw` — ~300-500ms of synchronous CPU — and it
# runs on the event loop for every `/api/v1` request. A single page opens 6-13 of them at
# once, and because the call blocks the loop they cannot overlap: they queue one bcrypt at
# a time, which is 2-4s of "everything is slow" on every navigation (measured against
# 0.10.0).
#
# There is exactly one valid secret per install and it does not change while the process
# runs, so verify it once and remember the answer. Keyed on the hash too, so if
# `shared_password_hash` is swapped at runtime a stale entry can't grant access. Only
# *successful* pairs are cached — a wrong token still pays full bcrypt every time, so this
# can't be used to grow memory or shortcut a brute-force attempt. The plaintext token is
# already held in the request headers, the frontend's settings store and `config.json` on
# disk, so keeping it in this set is not a new exposure.
_verified_tokens: set[tuple[str, str]] = set()


def reset_auth_cache() -> None:
    """Drop every remembered token. For tests that swap `shared_password_hash`."""
    _verified_tokens.clear()


async def require_auth(authorization: str | None = Header(default=None)) -> None:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")
    password = authorization.removeprefix("Bearer ")
    expected_hash = settings.shared_password_hash
    if (password, expected_hash) in _verified_tokens:
        return
    if not verify_password(password, expected_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    _verified_tokens.add((password, expected_hash))
