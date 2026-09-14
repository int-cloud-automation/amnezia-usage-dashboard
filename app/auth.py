from __future__ import annotations

import hmac
import time
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from .config import Settings


def verify_password(settings: Settings, username: str, password: str) -> bool:
    user_ok = hmac.compare_digest(
        username.encode("utf-8"), settings.admin_user.encode("utf-8")
    )
    pass_ok = hmac.compare_digest(
        password.encode("utf-8"), settings.admin_password.encode("utf-8")
    )
    return user_ok and pass_ok


def client_ip(request: Request) -> str:
    """Caller IP. Only Cloudflare can reach the origin, so its header is trusted."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class LoginGuard:
    """In-memory lockout after repeated failed logins from one IP."""

    def __init__(self, max_attempts: int, lockout_sec: int):
        self.max_attempts = max_attempts
        self.lockout_sec = lockout_sec
        self._failures: dict[str, tuple[int, float]] = {}

    def _prune(self, now: float) -> None:
        expired = [
            ip for ip, (_, last) in self._failures.items()
            if now - last > self.lockout_sec
        ]
        for ip in expired:
            del self._failures[ip]

    def retry_after(self, ip: str) -> int:
        now = time.monotonic()
        self._prune(now)
        count, last = self._failures.get(ip, (0, 0.0))
        if count < self.max_attempts:
            return 0
        remaining = self.lockout_sec - (now - last)
        return max(0, int(remaining))

    def record_failure(self, ip: str) -> None:
        now = time.monotonic()
        self._prune(now)
        count, _ = self._failures.get(ip, (0, 0.0))
        self._failures[ip] = (count + 1, now)

    def reset(self, ip: str) -> None:
        self._failures.pop(ip, None)


async def require_session(request: Request) -> None:
    if request.session.get("auth") is True:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


async def require_session_or_redirect(request: Request) -> None:
    if request.session.get("auth") is True:
        return
    raise HTTPException(
        status_code=status.HTTP_303_SEE_OTHER,
        headers={"Location": "/login"},
    )


SessionDep = Annotated[None, Depends(require_session)]
PageAuthDep = Annotated[None, Depends(require_session_or_redirect)]
