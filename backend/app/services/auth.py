"""인증 service (SPEC-001 Login Boundary, WORK-001 Phase 2).

product user email/password 로그인 → JWT access + refresh 발급. 비활성(`active=false`)/
퇴사(`resigned_at`) 계정은 로그인·refresh를 차단한다. Google social login은 없다.
토큰 발급/검증 자체는 core.security, 사용자 조회는 repo가 담당한다.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    REFRESH_TOKEN_TYPE,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.dtos.user import UserDTO
from app.repos.users import UserRepository


class InvalidCredentialsError(Exception):
    """email/password 불일치."""


class AccountDisabledError(Exception):
    """비활성/퇴사 계정 로그인 차단 (Visibility Contract)."""


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._users = UserRepository(session)

    async def authenticate(self, email: str, password: str) -> UserDTO:
        user = await self._users.get_by_email(email)
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentialsError
        if not user.can_browse:
            raise AccountDisabledError
        return user

    @staticmethod
    def issue_tokens(user: UserDTO) -> tuple[str, str]:
        return create_access_token(user.id), create_refresh_token(user.id)

    async def refresh(self, refresh_token: str) -> tuple[UserDTO, str, str]:
        """refresh 토큰 검증 → 계정 재확인 → 새 access/refresh 발급(회전)."""
        try:
            user_id = decode_token(refresh_token, REFRESH_TOKEN_TYPE)
        except TokenError as exc:
            raise InvalidCredentialsError from exc
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise InvalidCredentialsError
        if not user.can_browse:
            raise AccountDisabledError
        return user, *self.issue_tokens(user)

    async def get_user(self, user_id: int) -> UserDTO | None:
        return await self._users.get_by_id(user_id)
