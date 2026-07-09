"""Drive connector admin 라우트 — WORK-003 (SPEC-004 API Contract).

- GET  /admin/drive-connector            : connector 상태 조회 (admin)
- POST /admin/drive-connector/watch      : watch channel 생성/갱신 (admin)
- POST /admin/drive-connector/sync/retry : 실패 sync 재처리, 멱등 (admin)
- GET  /admin/drive-sync-events          : sync 감사 목록, 최신순 (admin)

에러봉투 {detail:{error_code,message}} — SPEC-004 Case Matrix.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_drive_sync_service, require_admin_only
from app.api.errors import SPEC003_004_ERRORS, spec003_004_http_error
from app.dtos.user import UserDTO
from app.schemas.drive_sync import (
    ConnectorStatusOut,
    SyncEventListResponse,
    SyncEventOut,
    SyncRetryResponse,
)
from app.services.drive_sync import ConnectorStatus, DriveSyncService

router = APIRouter(tags=["drive-connector"])


def _status_out(status: ConnectorStatus) -> ConnectorStatusOut:
    return ConnectorStatusOut(
        status=status.status,  # type: ignore[arg-type]
        scope=status.scope,
        selected_folder_id=status.selected_folder_id,
        selected_folder_name=status.selected_folder_name,
        watch_channel_id=status.watch_channel_id,
        watch_expires_at=status.watch_expires_at,
        last_sync_at=status.last_sync_at,
        last_error=status.last_error,
        page_token=status.page_token,
    )


@router.get("/admin/drive-connector", response_model=ConnectorStatusOut)
async def get_drive_connector(
    _admin: UserDTO = Depends(require_admin_only),
    service: DriveSyncService = Depends(get_drive_sync_service),
) -> ConnectorStatusOut:
    return _status_out(await service.connector_status())


@router.post("/admin/drive-connector/watch", response_model=ConnectorStatusOut)
async def register_drive_watch(
    _admin: UserDTO = Depends(require_admin_only),
    service: DriveSyncService = Depends(get_drive_sync_service),
) -> ConnectorStatusOut:
    try:
        return _status_out(await service.register_watch())
    except SPEC003_004_ERRORS as exc:
        raise spec003_004_http_error(exc)


@router.post("/admin/drive-connector/sync/retry", response_model=SyncRetryResponse)
async def retry_drive_sync(
    _admin: UserDTO = Depends(require_admin_only),
    service: DriveSyncService = Depends(get_drive_sync_service),
) -> SyncRetryResponse:
    try:
        summary = await service.retry_sync()
    except SPEC003_004_ERRORS as exc:
        raise spec003_004_http_error(exc)
    return SyncRetryResponse(
        processed=summary.processed,
        new_documents=summary.new_documents,
        updated_documents=summary.updated_documents,
        unavailable_documents=summary.unavailable_documents,
        skipped=summary.skipped,
        failed=summary.failed,
        page_token=summary.page_token,
    )


@router.get("/admin/drive-sync-events", response_model=SyncEventListResponse)
async def list_drive_sync_events(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin: UserDTO = Depends(require_admin_only),
    service: DriveSyncService = Depends(get_drive_sync_service),
) -> SyncEventListResponse:
    events, total = await service.list_events(limit=limit, offset=offset)
    return SyncEventListResponse(
        events=[
            SyncEventOut(
                id=e.id,
                event_type=e.event_type,  # type: ignore[arg-type]
                drive_file_id=e.drive_file_id,
                document_id=e.document_id,
                occurred_at=e.occurred_at,
                result=e.result,  # type: ignore[arg-type]
                message=e.message,
            )
            for e in events
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
