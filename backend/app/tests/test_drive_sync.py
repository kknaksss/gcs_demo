"""Drive sync 서비스 테스트 — WORK-003 Phase 1/2/3 (SPEC-003/004).

외부 네트워크 없이 httpx.MockTransport(FakeDrive)로 changes 처리 흐름을 검증한다.
- 멱등 upsert / fingerprint / soft delete 전이 / out_of_scope / 복구
- page token 저장·이어받기
- fingerprint 변경 → pending candidate stale + candidate_staled event
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import MetadataCandidate
from app.models.document import Document
from app.models.drive_sync import DriveSyncEvent, DriveSyncState
from app.services.documents import (
    DocumentsService,
    FingerprintComponentMissingError,
    build_fingerprint,
)
from app.tests.drive_fakes import (
    FakeDrive,
    change_for,
    make_file,
    make_sync_service,
    removed_change,
)


async def _documents(session: AsyncSession) -> list[Document]:
    return list(await session.scalars(sa.select(Document).order_by(Document.id)))


async def _events(session: AsyncSession, event_type: str) -> list[DriveSyncEvent]:
    return list(
        await session.scalars(
            sa.select(DriveSyncEvent)
            .where(DriveSyncEvent.event_type == event_type)
            .order_by(DriveSyncEvent.id)
        )
    )


# ----------------------------------------------------------------------
# Phase 1 — fingerprint 유틸
# ----------------------------------------------------------------------


def test_build_fingerprint_requires_all_components() -> None:
    with pytest.raises(FingerprintComponentMissingError) as exc:
        build_fingerprint(
            drive_file_id="f1",
            drive_modified_time=None,
            drive_name="doc",
            mime_type="",
        )
    assert set(exc.value.missing) == {"drive_modified_time", "mime_type"}


def test_build_fingerprint_composition() -> None:
    fp = build_fingerprint(
        drive_file_id="f1",
        drive_modified_time=dt.datetime(2026, 7, 8, 10, tzinfo=dt.timezone.utc),
        drive_name="doc.pdf",
        mime_type="application/pdf",
        version="3",
        content_fingerprint="sha256:abc",
    )
    assert fp["drive_file_id"] == "f1"
    assert fp["mime_type"] == "application/pdf"  # = drive_mime_type (SPEC-003 AC)
    assert fp["version"] == "3"
    assert fp["content_fingerprint"] == "sha256:abc"


# ----------------------------------------------------------------------
# Phase 1/2 — 멱등 upsert + 상태 전이
# ----------------------------------------------------------------------


async def test_apply_change_creates_document(db_session: AsyncSession) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-new", name="회의록.pdf"))
    service = make_sync_service(db_session, fake)

    outcome = await service.apply_change(change_for(file))
    await db_session.commit()

    assert outcome.status == "upserted" and outcome.created
    docs = await _documents(db_session)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.drive_name == "회의록.pdf"
    assert doc.drive_state == "active"
    assert doc.drive_mime_type == doc.drive_fingerprint["mime_type"]
    # mirror만 채워지고 approved 필드는 불가침 (기본값 유지)
    assert doc.document_type_id is None and doc.summary is None


async def test_apply_same_change_twice_is_idempotent(
    db_session: AsyncSession,
) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-idem"))
    service = make_sync_service(db_session, fake)

    first = await service.apply_change(change_for(file))
    second = await service.apply_change(change_for(file))
    await db_session.commit()

    docs = await _documents(db_session)
    assert len(docs) == 1  # row 1개 (SPEC-004 AC 멱등)
    assert first.status == second.status == "upserted"
    assert second.created is False and second.fingerprint_changed is False
    assert docs[0].drive_state == "active"


async def test_trashed_and_restore_transitions(db_session: AsyncSession) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-trash"))
    service = make_sync_service(db_session, fake)

    await service.apply_change(change_for(file))
    trashed = dict(file, trashed=True)
    outcome = await service.apply_change(change_for(trashed))
    await db_session.commit()

    docs = await _documents(db_session)
    assert outcome.status == "unavailable"
    assert len(docs) == 1  # hard delete 없이 상태 전환만
    assert docs[0].drive_state == "trashed"

    # Drive restore 감지 → trashed → active (SPEC-003 state machine)
    await service.apply_change(change_for(file))
    await db_session.commit()
    assert (await _documents(db_session))[0].drive_state == "active"

    events = await _events(db_session, "document_unavailable")
    assert len(events) == 1 and events[0].result == "success"


async def test_removed_change_soft_deletes(db_session: AsyncSession) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-removed"))
    service = make_sync_service(db_session, fake)

    await service.apply_change(change_for(file))
    outcome = await service.apply_change(removed_change("f-removed"))
    await db_session.commit()

    docs = await _documents(db_session)
    assert outcome.status == "unavailable"
    assert docs[0].drive_state == "removed"


async def test_out_of_scope_transition_and_recovery(
    db_session: AsyncSession,
) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-scope"))
    service = make_sync_service(db_session, fake)
    await service.apply_change(change_for(file))

    # 폴더 밖으로 이동 → out_of_scope
    fake.add_file(make_file("other-folder", name="다른 폴더",
                            mime_type="application/vnd.google-apps.folder",
                            parents=["root"]))
    moved = dict(file, parents=["other-folder"])
    fake.add_file(moved)
    outcome = await service.apply_change(change_for(moved))
    await db_session.commit()
    docs = await _documents(db_session)
    assert outcome.status == "unavailable"
    assert docs[0].drive_state == "out_of_scope"

    # 다시 선택 폴더로 → active 복귀 (SPEC-003 state machine)
    back = dict(file, parents=[fake.folder_id])
    fake.add_file(back)
    await service.apply_change(change_for(back))
    await db_session.commit()
    assert (await _documents(db_session))[0].drive_state == "active"


async def test_out_of_scope_unknown_file_not_collected(
    db_session: AsyncSession,
) -> None:
    fake = FakeDrive()
    outside = fake.add_file(make_file("f-outside", parents=["somewhere-else"]))
    fake.add_file({"id": "somewhere-else", "name": "x",
                   "mimeType": "application/vnd.google-apps.folder",
                   "parents": []})
    service = make_sync_service(db_session, fake)

    outcome = await service.apply_change(change_for(outside))
    await db_session.commit()

    assert outcome.status == "skipped"
    assert await _documents(db_session) == []


async def test_nested_subfolder_file_is_in_scope(db_session: AsyncSession) -> None:
    fake = FakeDrive()
    fake.add_file({"id": "sub-1", "name": "sub",
                   "mimeType": "application/vnd.google-apps.folder",
                   "parents": [fake.folder_id]})
    nested = fake.add_file(make_file("f-nested", parents=["sub-1"]))
    service = make_sync_service(db_session, fake)

    outcome = await service.apply_change(change_for(nested))
    await db_session.commit()

    assert outcome.status == "upserted"
    assert (await _documents(db_session))[0].drive_state == "active"


# ----------------------------------------------------------------------
# Phase 3 — fingerprint 변경 → pending candidate stale + event
# ----------------------------------------------------------------------


async def _add_pending_candidate(
    session: AsyncSession, document_id: int, fingerprint: dict
) -> MetadataCandidate:
    candidate = MetadataCandidate(
        document_id=document_id,
        state="pending",
        read_capability="content_read",
        candidate_metadata={"document_type": "회의록"},
        candidate_fingerprint=fingerprint,
    )
    session.add(candidate)
    await session.flush()
    return candidate


async def test_fingerprint_change_stales_pending_candidate(
    db_session: AsyncSession,
) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-stale", version="1"))
    service = make_sync_service(db_session, fake)
    await service.apply_change(change_for(file))
    doc = (await _documents(db_session))[0]
    candidate = await _add_pending_candidate(
        db_session, doc.id, doc.drive_fingerprint
    )
    await db_session.commit()

    modified = dict(
        file, modifiedTime="2026-07-08T12:34:56.000Z", version="2", name="개정.pdf"
    )
    fake.add_file(modified)
    outcome = await service.apply_change(change_for(modified))
    await db_session.commit()

    assert outcome.fingerprint_changed is True
    refreshed = await db_session.get(MetadataCandidate, candidate.id)
    assert refreshed is not None
    assert refreshed.state == "stale"
    assert refreshed.reason and "fingerprint" in refreshed.reason
    events = await _events(db_session, "candidate_staled")
    assert len(events) == 1 and events[0].document_id == doc.id


async def test_unavailable_blocks_pending_candidate(
    db_session: AsyncSession,
) -> None:
    fake = FakeDrive()
    file = fake.add_file(make_file("f-block"))
    service = make_sync_service(db_session, fake)
    await service.apply_change(change_for(file))
    doc = (await _documents(db_session))[0]
    candidate = await _add_pending_candidate(
        db_session, doc.id, doc.drive_fingerprint
    )
    await db_session.commit()

    await service.apply_change(change_for(dict(file, trashed=True)))
    await db_session.commit()

    refreshed = await db_session.get(MetadataCandidate, candidate.id)
    assert refreshed is not None
    assert refreshed.state == "blocked"


async def test_mark_candidates_stale_hook_direct(db_session: AsyncSession) -> None:
    """WORK-004가 연결할 훅 표면 — services/documents.mark_candidates_stale."""
    fake = FakeDrive()
    file = fake.add_file(make_file("f-hook"))
    service = make_sync_service(db_session, fake)
    await service.apply_change(change_for(file))
    doc = (await _documents(db_session))[0]
    await _add_pending_candidate(db_session, doc.id, doc.drive_fingerprint)

    staled = await DocumentsService(db_session).mark_candidates_stale(
        doc.id, {"changed": True}, reason="test reason"
    )
    await db_session.commit()

    assert len(staled) == 1
    assert staled[0].state == "stale" and staled[0].reason == "test reason"


# ----------------------------------------------------------------------
# Phase 2 — process_changes: token 저장/이어받기, 이벤트, 실패 기록
# ----------------------------------------------------------------------


async def test_process_changes_stores_next_token(db_session: AsyncSession) -> None:
    fake = FakeDrive()
    f1 = fake.add_file(make_file("f-1"))
    f2 = fake.add_file(make_file("f-2", name="둘째.pdf"))
    fake.set_page(
        "start-1", [change_for(f1)], next_page_token="page-2"
    )
    fake.set_page("page-2", [change_for(f2)], new_start_page_token="start-9")
    service = make_sync_service(db_session, fake)

    summary = await service.process_changes(trigger="test")

    assert summary.processed == 2 and summary.new_documents == 2
    state = await db_session.get(DriveSyncState, 1)
    assert state is not None
    assert state.page_token == "start-9"  # 처리 성공 후 다음 token 저장
    assert state.last_sync_at is not None and state.last_error is None
    listed = await _events(db_session, "changes_listed")
    assert len(listed) == 1 and "processed=2" in (listed[0].message or "")


async def test_process_changes_resumes_from_saved_token(
    db_session: AsyncSession,
) -> None:
    """재시작 후 저장 token부터 이어받는다 (SPEC-004 S-5)."""
    fake = FakeDrive()
    f1 = fake.add_file(make_file("f-resume"))
    fake.set_page("start-1", [change_for(f1)], new_start_page_token="start-2")
    service = make_sync_service(db_session, fake)
    await service.process_changes(trigger="test")

    # 재시작 시뮬레이션 — 새 service 인스턴스, 저장된 token 사용
    f2 = fake.add_file(make_file("f-after-restart", name="이어받기.pdf"))
    fake.set_page("start-2", [change_for(f2)], new_start_page_token="start-3")
    service2 = make_sync_service(db_session, fake)
    summary = await service2.process_changes(trigger="test")

    assert summary.new_documents == 1
    assert "start-2" in fake.changes_tokens  # 저장 token으로 이어받음
    docs = await _documents(db_session)
    assert {d.drive_file_id for d in docs} == {"f-resume", "f-after-restart"}
    state = await db_session.get(DriveSyncState, 1)
    assert state is not None and state.page_token == "start-3"


async def test_process_changes_reprocess_is_idempotent(
    db_session: AsyncSession,
) -> None:
    """같은 change 페이지를 다시 처리해도 최종 상태 동일 (다시 처리 CTA)."""
    fake = FakeDrive()
    f1 = fake.add_file(make_file("f-repeat"))
    fake.set_page("start-1", [change_for(f1)], new_start_page_token="start-2")
    fake.set_page("start-2", [change_for(f1)], new_start_page_token="start-2")
    service = make_sync_service(db_session, fake)

    await service.process_changes(trigger="test")
    await service.process_changes(trigger="test")

    docs = await _documents(db_session)
    assert len(docs) == 1 and docs[0].drive_state == "active"


async def test_process_changes_failure_records_event_and_last_error(
    db_session: AsyncSession,
) -> None:
    from app.services.drive_sync import DriveChangesFailedError

    fake = FakeDrive()
    fake.fail_changes = True
    service = make_sync_service(db_session, fake)

    with pytest.raises(DriveChangesFailedError):
        await service.process_changes(trigger="test")

    state = await db_session.get(DriveSyncState, 1)
    assert state is not None and state.last_error
    failed = await _events(db_session, "sync_failed")
    assert len(failed) == 1
    assert "DRIVE_CHANGES_FAILED" in (failed[0].message or "")


async def test_process_changes_requires_configuration(
    db_session: AsyncSession,
) -> None:
    from app.services.drive_sync import (
        DriveConnectorNotConfiguredError,
        DriveFolderNotConfiguredError,
    )

    fake = FakeDrive()
    unconfigured = make_sync_service(
        db_session,
        fake,
        google_drive_client_id="",
        google_drive_client_secret="",
        google_drive_refresh_token="",
    )
    with pytest.raises(DriveConnectorNotConfiguredError):
        await unconfigured.process_changes(trigger="test")

    no_folder = make_sync_service(
        db_session, fake, google_drive_selected_folder_id=""
    )
    with pytest.raises(DriveFolderNotConfiguredError):
        await no_folder.process_changes(trigger="test")


# ----------------------------------------------------------------------
# Phase 2 — watch 등록/갱신 + connector status
# ----------------------------------------------------------------------


async def test_register_watch_saves_channel_state(db_session: AsyncSession) -> None:
    fake = FakeDrive()
    service = make_sync_service(db_session, fake)

    status = await service.register_watch()

    state = await db_session.get(DriveSyncState, 1)
    assert state is not None
    assert state.watch_channel_id and state.watch_resource_id == "resource-1"
    assert state.watch_expires_at is not None
    assert status.status == "connected"


async def test_register_watch_requires_https_webhook_url(
    db_session: AsyncSession,
) -> None:
    from app.services.drive_sync import DriveConnectorNotConfiguredError

    fake = FakeDrive()
    service = make_sync_service(
        db_session, fake, google_drive_webhook_url="http://insecure.example.com"
    )
    with pytest.raises(DriveConnectorNotConfiguredError):
        await service.register_watch()


async def test_connector_status_transitions(db_session: AsyncSession) -> None:
    fake = FakeDrive()

    # env 없음 → disconnected
    unconfigured = make_sync_service(
        db_session,
        fake,
        google_drive_client_id="",
        google_drive_client_secret="",
        google_drive_refresh_token="",
    )
    assert (await unconfigured.connector_status()).status == "disconnected"

    # env + watch 미등록 → watch_expiring (갱신 필요 표기)
    service = make_sync_service(db_session, fake)
    assert (
        await service.connector_status(resolve_folder_name=False)
    ).status == "watch_expiring"

    # watch 등록(만료 여유) → connected + 폴더 표시명 resolve
    await service.register_watch()
    status = await service.connector_status()
    assert status.status == "connected"
    assert status.scope == "drive.readonly"
    assert status.selected_folder_name == "Cloud Intake Demo"

    # 만료 임박 → watch_expiring
    from app.repos.drive_sync import DriveSyncStateRepository

    repo = DriveSyncStateRepository(db_session)
    await repo.update(
        watch_expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)
    )
    await db_session.commit()
    assert (
        await service.connector_status(resolve_folder_name=False)
    ).status == "watch_expiring"

    # 마지막 오류 → error
    await repo.update(last_error="DRIVE_CHANGES_FAILED: boom")
    await db_session.commit()
    assert (
        await service.connector_status(resolve_folder_name=False)
    ).status == "error"
