"""auth API 요청/응답 schema (SPEC-001 §4 API Contract, WORK-001 Phase 2).

FE(T-002 로그인 화면)가 이 계약으로 붙는다.
- access token은 응답 body로 전달(FE가 Authorization: Bearer로 사용).
- refresh token은 응답 body에 넣지 않고 httpOnly secure cookie로만 내려간다.
- /auth/me는 RBAC 판정에 필요한 사용자 속성을 노출한다(password_hash 절대 미노출).
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserProfile(BaseModel):
    """GET /auth/me + 로그인 응답에 포함되는 사용자 속성."""

    id: int
    email: str
    name: str
    role: str
    position: str
    department: str | None = None
    department_node_id: int | None = None
    team_node_id: int | None = None
    active: bool
    is_admin: bool
    resigned_at: dt.datetime | None = None


class TokenResponse(BaseModel):
    """로그인/refresh 성공 응답. refresh token은 cookie로만 전달된다."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserProfile
