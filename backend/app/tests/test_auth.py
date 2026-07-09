"""JWT 인증 API 테스트 — WORK-001 Phase 2 (SPEC-001 Login Boundary).

seed 계정으로 로그인 → access(body) + refresh(httpOnly cookie), 비활성 차단,
/auth/me, refresh 회전, logout을 검증한다.
"""

from __future__ import annotations

from httpx import AsyncClient

from app.tests.conftest import DEMO_PASSWORD


def _pick(records: list[dict], *, active: bool, role: str | None = None) -> dict:
    for rec in records:
        if rec["active"] is active and (role is None or rec["role"] == role):
            return rec
    raise AssertionError("no matching seed record")


async def test_login_success_sets_access_and_refresh_cookie(
    client: AsyncClient, seeded: list[dict]
) -> None:
    user = _pick(seeded, active=True)
    resp = await client.post(
        "/auth/login", json={"email": user["email"], "password": DEMO_PASSWORD}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == user["email"]
    assert "refresh_token" in resp.cookies


async def test_login_wrong_password_401(
    client: AsyncClient, seeded: list[dict]
) -> None:
    user = _pick(seeded, active=True)
    resp = await client.post(
        "/auth/login", json={"email": user["email"], "password": "nope"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["error_code"] == "INVALID_CREDENTIALS"


async def test_inactive_account_login_blocked_403(
    client: AsyncClient, seeded: list[dict]
) -> None:
    user = _pick(seeded, active=False)
    resp = await client.post(
        "/auth/login", json={"email": user["email"], "password": DEMO_PASSWORD}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error_code"] == "ACCOUNT_DISABLED"


async def test_me_requires_bearer(client: AsyncClient, seeded: list[dict]) -> None:
    assert (await client.get("/auth/me")).status_code == 401


async def test_me_returns_current_user(
    client: AsyncClient, seeded: list[dict]
) -> None:
    user = _pick(seeded, active=True)
    login = await client.post(
        "/auth/login", json={"email": user["email"], "password": DEMO_PASSWORD}
    )
    token = login.json()["access_token"]
    resp = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["email"] == user["email"]


async def test_refresh_rotates_access_token(
    client: AsyncClient, seeded: list[dict]
) -> None:
    user = _pick(seeded, active=True)
    await client.post(
        "/auth/login", json={"email": user["email"], "password": DEMO_PASSWORD}
    )
    # refresh cookie는 client jar에 남아 자동 전송된다.
    resp = await client.post("/auth/refresh")
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_refresh_without_cookie_401(client: AsyncClient) -> None:
    resp = await client.post("/auth/refresh")
    assert resp.status_code == 401


async def test_logout_clears_cookie(
    client: AsyncClient, seeded: list[dict]
) -> None:
    user = _pick(seeded, active=True)
    await client.post(
        "/auth/login", json={"email": user["email"], "password": DEMO_PASSWORD}
    )
    resp = await client.post("/auth/logout")
    assert resp.status_code == 204
