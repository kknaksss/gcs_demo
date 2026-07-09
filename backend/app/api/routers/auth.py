"""auth 라우트 (SPEC-001 §4, WORK-001 Phase 2).

- POST /auth/login    : email/password 로그인 → access(body) + refresh(httpOnly cookie)
- POST /auth/refresh  : refresh cookie로 access 재발급(+ refresh 회전)
- POST /auth/logout   : refresh cookie 제거
- GET  /auth/me       : 현재 사용자/권한 속성

에러봉투는 {detail: {error_code, message}} 형태로 통일한다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.dtos.user import UserDTO
from app.schemas.auth import LoginRequest, TokenResponse, UserProfile
from app.services.auth import (
    AccountDisabledError,
    AuthService,
    InvalidCredentialsError,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _profile(user: UserDTO) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        position=user.position,
        department=user.department,
        department_node_id=user.department_node_id,
        team_node_id=user.team_node_id,
        active=user.active,
        is_admin=user.is_admin,
        resigned_at=user.resigned_at,
    )


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=refresh_token,
        max_age=settings.refresh_token_ttl,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/auth",
    )


def _token_response(
    response: Response, user: UserDTO, access: str, refresh: str
) -> TokenResponse:
    _set_refresh_cookie(response, refresh)
    return TokenResponse(
        access_token=access,
        expires_in=get_settings().access_token_ttl,
        user=_profile(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    service = AuthService(session)
    try:
        user = await service.authenticate(body.email, body.password)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error_code": "INVALID_CREDENTIALS",
                "message": "Invalid email or password.",
            },
        )
    except AccountDisabledError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error_code": "ACCOUNT_DISABLED",
                "message": "Account is disabled or resigned.",
            },
        )
    access, refresh = service.issue_tokens(user)
    return _token_response(response, user, access, refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    settings = get_settings()
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "UNAUTHENTICATED", "message": "Missing refresh session."},
        )
    service = AuthService(session)
    try:
        user, access, new_refresh = await service.refresh(token)
    except InvalidCredentialsError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error_code": "INVALID_REFRESH", "message": "Refresh session expired."},
        )
    except AccountDisabledError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error_code": "ACCOUNT_DISABLED", "message": "Account is disabled or resigned."},
        )
    return _token_response(response, user, access, new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> Response:
    settings = get_settings()
    response.delete_cookie(key=settings.refresh_cookie_name, path="/auth")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserProfile)
async def me(user: UserDTO = Depends(get_current_user)) -> UserProfile:
    return _profile(user)
