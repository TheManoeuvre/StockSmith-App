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


async def require_auth(authorization: str | None = Header(default=None)) -> None:
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing credentials")
    password = authorization.removeprefix("Bearer ")
    if not verify_password(password, settings.shared_password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
