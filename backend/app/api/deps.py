"""Router dependencies (auth, session). Layer rule: router는 여기서 받은 의존성으로
service만 호출한다 (ARCH-001 §4).

- get_db: async session.
- get_current_user: Authorization: Bearer <access token> 검증 → UserDTO.
- require_admin: admin role guard (승인 게이트/관리 API 공용).
"""

from collections.abc import AsyncGenerator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import ACCESS_TOKEN_TYPE, TokenError, decode_token
from app.db.session import get_session_factory
from app.dtos.user import UserDTO
from app.services.auth import AuthService
from app.services.drive_sync import DriveSyncService


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


def _unauthorized(message: str = "Authentication required.") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error_code": "UNAUTHENTICATED", "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer_token(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()
    return token


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> UserDTO:
    token = _bearer_token(request)
    try:
        user_id = decode_token(token, ACCESS_TOKEN_TYPE)
    except TokenError:
        raise _unauthorized("Invalid token.")
    user = await AuthService(session).get_user(user_id)
    if user is None:
        raise _unauthorized("User not found.")
    if not user.can_browse:
        # 비활성/퇴사 계정은 세션이 있어도 접근을 차단한다 (Visibility Contract).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "ACCOUNT_DISABLED", "message": "Account is disabled or resigned."},
        )
    return user


async def require_admin(
    user: UserDTO = Depends(get_current_user),
) -> UserDTO:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "FORBIDDEN", "message": "Admin permission required."},
        )
    return user


async def get_drive_sync_service(
    session: AsyncSession = Depends(get_db),
) -> DriveSyncService:
    """Drive sync service — 테스트에서 mock transport client 주입을 위해 의존성으로 노출."""
    return DriveSyncService(session)


async def require_admin_only(
    user: UserDTO = Depends(get_current_user),
) -> UserDTO:
    """SPEC-002 계열 admin guard — Case Matrix `FORBIDDEN_ADMIN_ONLY`.

    WORK-001의 require_admin(FORBIDDEN)과 에러 코드 계약이 달라 별도 의존성으로 둔다.
    조직/트리/카탈로그 쓰기와 문서 이관/이력 라우트가 사용한다.
    """
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "FORBIDDEN_ADMIN_ONLY",
                "message": "admin permission required",
            },
        )
    return user
