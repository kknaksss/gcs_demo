"""SPEC-004 connector/sync API schema — WORK-003.

- GET /admin/drive-connector, POST /admin/drive-connector/watch,
  POST /admin/drive-connector/sync/retry, GET /admin/drive-sync-events.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel


class ConnectorStatusOut(BaseModel):
    """SPEC-004 §4 Connector status 계약."""

    status: Literal["connected", "disconnected", "watch_expiring", "error"]
    scope: str
    selected_folder_id: str | None = None
    selected_folder_name: str | None = None
    watch_channel_id: str | None = None
    watch_expires_at: dt.datetime | None = None
    last_sync_at: dt.datetime | None = None
    last_error: str | None = None
    # Sync Activity(U-2) 표시용 — 마지막 변경 토큰.
    page_token: str | None = None


class SyncRetryResponse(BaseModel):
    """다시 처리 결과 — 같은 change 중복 처리에도 최종 상태 동일(멱등)."""

    processed: int
    new_documents: int
    updated_documents: int
    unavailable_documents: int
    skipped: int
    failed: int
    page_token: str | None = None


class SyncEventOut(BaseModel):
    """SPEC-004 §4 Sync event 계약."""

    id: int
    event_type: Literal[
        "webhook_received",
        "changes_listed",
        "document_upserted",
        "document_unavailable",
        "candidate_staled",
        "reanalysis_enqueued",
        "sync_failed",
    ]
    drive_file_id: str | None = None
    document_id: int | None = None
    occurred_at: dt.datetime
    result: Literal["success", "skipped", "failed"]
    message: str | None = None


class SyncEventListResponse(BaseModel):
    events: list[SyncEventOut]
    total: int
    limit: int
    offset: int
