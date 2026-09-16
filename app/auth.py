from __future__ import annotations

import hmac
import re
import time
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, Request, status

from .config import Settings

# bcrypt truncates at 72 bytes; reject longer so behavior is explicit.
_MAX_PASSWORD_BYTES = 72
_MIN_PASSWORD_LEN = 10


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > _MAX_PASSWORD_BYTES:
        raise ValueError("password is too long")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode("ascii")


def check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("ascii"),
        )
    except (ValueError, TypeError):
        return False


def validate_new_password(password: str) -> str | None:
    """Return an error message, or None if the password is acceptable."""
    if len(password) < _MIN_PASSWORD_LEN:
        return f"New password must be at least {_MIN_PASSWORD_LEN} characters."
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        return "New password is too long."
    if password.isspace() or not password.strip():
        return "New password cannot be blank."
    # Prefer mixed classes without forcing a complex policy.
    classes = sum(
        (
            bool(re.search(r"[a-z]", password)),
            bool(re.search(r"[A-Z]", password)),
            bool(re.search(r"\d", password)),
            bool(re.search(r"[^A-Za-z0-9]", password)),
        )
    )
    if classes < 2:
        return "Use a mix of letters, numbers, or symbols."
    return None


def verify_username(settings: Settings, username: str) -> bool:
    return hmac.compare_digest(
        username.encode("utf-8"), settings.admin_user.encode("utf-8")
    )


def verify_password_hash(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    return check_password(password, password_hash)


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
