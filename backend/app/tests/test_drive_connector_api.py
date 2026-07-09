"""Drive connector admin API + webhook 테스트 — WORK-003 (SPEC-004 계약).

라우터의 DriveSyncService 의존성을 FakeDrive(MockTransport) 기반으로 override해
외부 실호출 없이 검증한다.
"""

from __future__ import annotations

import sqlalchemy as sa
from httpx import AsyncClient

from app.api.deps import get_drive_sync_service
from app.main import app
from app.models.document import Document
from app.models.drive_sync import DriveSyncEvent, DriveSyncState
from app.services.drive_sync import DriveSyncService, webhook_channel_token
from app.tests.conftest import DEMO_PASSWORD, _Session
from app.tests.drive_fakes import (
    FakeDrive,
    change_for,
    make_drive_client,
    make_drive_settings,
    make_file,
)


def _pick(records: list[dict], *, active: bool, role: str) -> dict:
    for rec in records:
        if rec["active"] is active and rec["role"] == role:
            return rec
    raise AssertionError("no matching seed record")


async def _bearer(client: AsyncClient, email: str) -> dict[str, str]:
    login = await client.post(
        "/auth/login", json={"email": email, "password": DEMO_PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _install_fake_service(fake: FakeDrive, **overrides) -> None:
    """라우터 의존성 override — 요청마다 새 세션 + fake client service."""
    settings = make_drive_settings(fake, **overrides)

    async def _override():
        async with _Session() as session:
            yield DriveSyncService(
                session,
                client=make_drive_client(fake, settings),
                settings=settings,
            )

    app.dependency_overrides[get_drive_sync_service] = _override


async def _register_watch(fake: FakeDrive, **overrides) -> tuple[str, str]:
    """상태 세팅용 — watch 등록 후 (channel_id, channel_token) 반환."""
    settings = make_drive_settings(fake, **overrides)
    async with _Session() as session:
        service = DriveSyncService(
            session, client=make_drive_client(fake, settings), settings=settings
        )
        await service.register_watch()
        state = await session.get(DriveSyncState, 1)
        assert state is not None and state.watch_channel_id
        return state.watch_channel_id, webhook_channel_token(
            settings.jwt_secret, state.watch_channel_id
        )


# ----------------------------------------------------------------------
# admin guard
# ----------------------------------------------------------------------


async def test_connector_endpoints_require_admin(
    client: AsyncClient, seeded: list[dict]
) -> None:
    member = _pick(seeded, active=True, role="member")
    headers = await _bearer(client, member["email"])
    for method, path in [
        ("GET", "/admin/drive-connector"),
        ("POST", "/admin/drive-connector/watch"),
        ("POST", "/admin/drive-connector/sync/retry"),
        ("GET", "/admin/drive-sync-events"),
    ]:
        resp = await client.request(method, path, headers=headers)
        assert resp.status_code == 403, path
        assert resp.json()["detail"]["error_code"] == "FORBIDDEN_ADMIN_ONLY"


# ----------------------------------------------------------------------
# GET /admin/drive-connector
# ----------------------------------------------------------------------


async def test_connector_status_disconnected_without_env(
    client: AsyncClient, seeded: list[dict]
) -> None:
    fake = FakeDrive()
    _install_fake_service(
        fake,
        google_drive_client_id="",
        google_drive_client_secret="",
        google_drive_refresh_token="",
        google_drive_selected_folder_id="",
    )
    headers = await _bearer(client, _pick(seeded, active=True, role="admin")["email"])
    resp = await client.get("/admin/drive-connector", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "disconnected"
    assert body["scope"] == "drive.readonly"


async def test_connector_status_connected_after_watch(
    client: AsyncClient, seeded: list[dict]
) -> None:
    fake = FakeDrive()
    await _register_watch(fake)
    _install_fake_service(fake)
    headers = await _bearer(client, _pick(seeded, active=True, role="admin")["email"])
    resp = await client.get("/admin/drive-connector", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "connected"
    assert body["selected_folder_name"] == "Cloud Intake Demo"
    assert body["watch_channel_id"] and body["watch_expires_at"]


# ----------------------------------------------------------------------
# POST /admin/drive-connector/watch, sync/retry — Case Matrix
# ----------------------------------------------------------------------


async def test_watch_returns_not_configured_error(
    client: AsyncClient, seeded: list[dict]
) -> None:
    fake = FakeDrive()
    _install_fake_service(
        fake,
        google_drive_client_id="",
        google_drive_client_secret="",
        google_drive_refresh_token="",
    )
    headers = await _bearer(client, _pick(seeded, active=True, role="admin")["email"])
    resp = await client.post("/admin/drive-connector/watch", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "DRIVE_CONNECTOR_NOT_CONFIGURED"


async def test_retry_returns_folder_not_configured(
    client: AsyncClient, seeded: list[dict]
) -> None:
    fake = FakeDrive()
    _install_fake_service(fake, google_drive_selected_folder_id="")
    headers = await _bearer(client, _pick(seeded, active=True, role="admin")["email"])
    resp = await client.post("/admin/drive-connector/sync/retry", headers=headers)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error_code"] == "DRIVE_FOLDER_NOT_CONFIGURED"


async def test_retry_processes_changes_and_reports_counts(
    client: AsyncClient, seeded: list[dict]
) -> None:
    fake = FakeDrive()
    f1 = fake.add_file(make_file("f-api-1", name="신규.pdf"))
    fake.set_page("start-1", [change_for(f1)], new_start_page_token="start-2")
    _install_fake_service(fake)
    headers = await _bearer(client, _pick(seeded, active=True, role="admin")["email"])

    resp = await client.post("/admin/drive-connector/sync/retry", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["processed"] == 1 and body["new_documents"] == 1
    assert body["page_token"] == "start-2"

    # 같은 change 재처리(멱등) — 최종 상태 동일
    fake.set_page("start-2", [change_for(f1)], new_start_page_token="start-2")
    resp2 = await client.post("/admin/drive-connector/sync/retry", headers=headers)
    assert resp2.status_code == 200
    async with _Session() as session:
        docs = list(await session.scalars(sa.select(Document)))
        assert len(docs) == 1 and docs[0].drive_state == "active"


async def test_changes_failure_maps_to_502(
    client: AsyncClient, seeded: list[dict]
) -> None:
    fake = FakeDrive()
    fake.fail_changes = True
    _install_fake_service(fake)
    headers = await _bearer(client, _pick(seeded, active=True, role="admin")["email"])
    resp = await client.post("/admin/drive-connector/sync/retry", headers=headers)
    assert resp.status_code == 502
    assert resp.json()["detail"]["error_code"] == "DRIVE_CHANGES_FAILED"


# ----------------------------------------------------------------------
# GET /admin/drive-sync-events — 감사 목록 최신순
# ----------------------------------------------------------------------


async def test_sync_events_listing_newest_first(
    client: AsyncClient, seeded: list[dict]
) -> None:
    fake = FakeDrive()
    f1 = fake.add_file(make_file("f-ev-1"))
    f2 = fake.add_file(make_file("f-ev-2", name="둘째.pdf"))
    fake.set_page(
        "start-1", [change_for(f1), change_for(f2)], new_start_page_token="s2"
    )
    _install_fake_service(fake)
    headers = await _bearer(client, _pick(seeded, active=True, role="admin")["email"])
    await client.post("/admin/drive-connector/sync/retry", headers=headers)

    resp = await client.get(
        "/admin/drive-sync-events", params={"limit": 2}, headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 3  # document_upserted x2 + changes_listed
    assert len(body["events"]) == 2
    ids = [e["id"] for e in body["events"]]
    assert ids == sorted(ids, reverse=True)  # 최신순
    assert body["events"][0]["event_type"] == "changes_listed"


# ----------------------------------------------------------------------
# POST /webhooks/google-drive — 검증/트리거
# ----------------------------------------------------------------------


async def test_webhook_invalid_channel_recorded_and_ignored(
    client: AsyncClient,
) -> None:
    fake = FakeDrive()
    f1 = fake.add_file(make_file("f-wh-bad"))
    fake.set_page("start-1", [change_for(f1)], new_start_page_token="s2")
    _install_fake_service(fake)

    resp = await client.post(
        "/webhooks/google-drive",
        headers={
            "X-Goog-Channel-ID": "unknown-channel",
            "X-Goog-Channel-Token": "bogus",
            "X-Goog-Resource-State": "change",
        },
    )
    assert resp.status_code == 204  # 외부에 정보를 누설하지 않는다

    async with _Session() as session:
        events = list(
            await session.scalars(
                sa.select(DriveSyncEvent).where(
                    DriveSyncEvent.event_type == "webhook_received"
                )
            )
        )
        assert len(events) == 1
        assert events[0].result == "failed"
        assert "DRIVE_WEBHOOK_INVALID" in (events[0].message or "")
        # 처리 자체가 일어나지 않았다 (trigger 무시)
        docs = list(await session.scalars(sa.select(Document)))
        assert docs == []


async def test_webhook_valid_triggers_changes_processing(
    client: AsyncClient,
) -> None:
    fake = FakeDrive()
    channel_id, channel_token = await _register_watch(fake)
    f1 = fake.add_file(make_file("f-wh-ok", name="웹훅수집.pdf"))
    fake.set_page("start-1", [change_for(f1)], new_start_page_token="s2")
    _install_fake_service(fake)

    resp = await client.post(
        "/webhooks/google-drive",
        headers={
            "X-Goog-Channel-ID": channel_id,
            "X-Goog-Channel-Token": channel_token,
            "X-Goog-Resource-ID": "resource-1",
            "X-Goog-Resource-State": "change",
        },
    )
    assert resp.status_code == 204

    async with _Session() as session:
        docs = list(await session.scalars(sa.select(Document)))
        assert len(docs) == 1 and docs[0].drive_name == "웹훅수집.pdf"
        received = list(
            await session.scalars(
                sa.select(DriveSyncEvent).where(
                    DriveSyncEvent.event_type == "webhook_received",
                    DriveSyncEvent.result == "success",
                )
            )
        )
        assert len(received) == 1


async def test_webhook_sync_ping_does_not_process(client: AsyncClient) -> None:
    fake = FakeDrive()
    channel_id, channel_token = await _register_watch(fake)
    f1 = fake.add_file(make_file("f-wh-ping"))
    fake.set_page("start-1", [change_for(f1)], new_start_page_token="s2")
    _install_fake_service(fake)

    resp = await client.post(
        "/webhooks/google-drive",
        headers={
            "X-Goog-Channel-ID": channel_id,
            "X-Goog-Channel-Token": channel_token,
            "X-Goog-Resource-ID": "resource-1",
            "X-Goog-Resource-State": "sync",
        },
    )
    assert resp.status_code == 204
    async with _Session() as session:
        docs = list(await session.scalars(sa.select(Document)))
        assert docs == []  # 등록 확인 ping은 변경 처리 trigger가 아니다
