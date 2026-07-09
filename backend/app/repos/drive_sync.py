"""drive_sync_state / drive_sync_events repository — WORK-003 (SPEC-004, ARCH-003 §6).

- state는 단일 row(id=1). env 값은 저장하지 않는다.
- events는 append-only audit. message에 원문/secret 금지.
stmt는 이 repo 안에서만 (ARCH-001 §4).
"""

from __future__ import annotations

import datetime as dt

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drive_sync import DriveSyncEvent, DriveSyncState

_STATE_ID = 1
_UNSET = object()


class DriveSyncStateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> DriveSyncState | None:
        return await self._session.get(DriveSyncState, _STATE_ID)

    async def ensure(self) -> DriveSyncState:
        """단일 row(id=1)를 보장한다 (없으면 생성)."""
        state = await self.get()
        if state is None:
            state = DriveSyncState(id=_STATE_ID)
            self._session.add(state)
            await self._session.flush()
        return state

    async def update(
        self,
        *,
        page_token: object = _UNSET,
        watch_channel_id: object = _UNSET,
        watch_resource_id: object = _UNSET,
        watch_expires_at: object = _UNSET,
        last_sync_at: object = _UNSET,
        last_error: object = _UNSET,
    ) -> DriveSyncState:
        state = await self.ensure()
        if page_token is not _UNSET:
            state.page_token = page_token  # type: ignore[assignment]
        if watch_channel_id is not _UNSET:
            state.watch_channel_id = watch_channel_id  # type: ignore[assignment]
        if watch_resource_id is not _UNSET:
            state.watch_resource_id = watch_resource_id  # type: ignore[assignment]
        if watch_expires_at is not _UNSET:
            state.watch_expires_at = watch_expires_at  # type: ignore[assignment]
        if last_sync_at is not _UNSET:
            state.last_sync_at = last_sync_at  # type: ignore[assignment]
        if last_error is not _UNSET:
            state.last_error = last_error  # type: ignore[assignment]
        await self._session.flush()
        return state


class DriveSyncEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        event_type: str,
        result: str,
        drive_file_id: str | None = None,
        document_id: int | None = None,
        message: str | None = None,
        occurred_at: dt.datetime | None = None,
    ) -> DriveSyncEvent:
        event = DriveSyncEvent(
            event_type=event_type,
            result=result,
            drive_file_id=drive_file_id,
            document_id=document_id,
            message=message,
        )
        if occurred_at is not None:
            event.occurred_at = occurred_at
        self._session.add(event)
        await self._session.flush()
        return event

    async def list_recent(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[DriveSyncEvent], int]:
        """최신순 페이지네이션 — (events, total)."""
        total = await self._session.scalar(
            sa.select(sa.func.count()).select_from(DriveSyncEvent)
        )
        rows = await self._session.scalars(
            sa.select(DriveSyncEvent)
            .order_by(DriveSyncEvent.occurred_at.desc(), DriveSyncEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows), int(total or 0)
