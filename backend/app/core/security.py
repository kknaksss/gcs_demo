"""Auth primitives (ARCH-001 §11, SPEC-001 Login Boundary).

backend JWT access token + httpOnly refresh token cookie.
- password: bcrypt hash/verify (데모 공통 password를 seed가 발급).
- JWT: access(짧은 TTL) / refresh(긴 TTL) 두 종류. subject는 product user id(int).
Google social login은 사용하지 않는다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import bcrypt
import jwt

from app.core.config import get_settings

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


class TokenError(Exception):
    """유효하지 않은/만료된 토큰."""


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(raw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(raw.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _encode(sub: int, token_type: str, ttl_seconds: int, now: dt.datetime) -> str:
    settings = get_settings()
    payload: dict[str, Any] = {
        "sub": str(sub),
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return _encode(user_id, ACCESS_TOKEN_TYPE, get_settings().access_token_ttl, now)


def create_refresh_token(user_id: int, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return _encode(user_id, REFRESH_TOKEN_TYPE, get_settings().refresh_token_ttl, now)


def decode_token(token: str, expected_type: str) -> int:
    """토큰을 검증하고 product user id를 돌려준다. 실패 시 TokenError."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:  # 만료/서명/포맷 오류 모두 포함
        raise TokenError(str(exc)) from exc
    if payload.get("type") != expected_type:
        raise TokenError("token type mismatch")
    sub = payload.get("sub")
    if sub is None:
        raise TokenError("missing subject")
    try:
        return int(sub)
    except (TypeError, ValueError) as exc:
        raise TokenError("invalid subject") from exc
