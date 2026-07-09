"""Drive sync worker job — WORK-003 (SPEC-004 S-5, watch 갱신).

- run_poll_once: 주기적 changes 폴링 — webhook 공개 URL이 없는 로컬 환경 대안.
  page token 이어받기는 process_changes가 담당하므로 재시작 후에도 누락 없다.
- 폴링과 webhook이 겹쳐도 apply_change 멱등이라 최종 상태는 같다.
- watch 만료 임박 시 갱신: webhook URL이 설정된 경우에만 시도한다.
- Redis 큐 본격 소비(AI job)는 WORK-004에서 배선한다.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import Settings
from app.services.drive_sync import (
    DriveChangesFailedError,
    DriveConnectorNotConfiguredError,
    DriveFolderNotConfiguredError,
    DriveSyncService,
    DriveWatchFailedError,
)

logger = logging.getLogger(__name__)


def connector_configured(settings: Settings) -> bool:
    return bool(
        settings.google_drive_client_id
        and settings.google_drive_client_secret
        and settings.google_drive_refresh_token
        and settings.google_drive_selected_folder_id
    )


async def run_poll_once(
    session_factory: async_sessionmaker, settings: Settings
) -> None:
    """1 tick: watch 만료 갱신 체크 → changes 폴링. 실패는 기록 후 다음 tick."""
    if not connector_configured(settings):
        logger.debug("drive connector not configured — poll skipped")
        return

    # watch 만료 임박 갱신 (webhook URL 있는 환경에서만 의미 있음)
    if settings.google_drive_webhook_url.startswith("https://"):
        async with session_factory() as session:
            service = DriveSyncService(session, settings=settings)
            try:
                status = await service.connector_status(resolve_folder_name=False)
                if status.watch_channel_id and status.status == "watch_expiring":
                    await service.register_watch()
                    logger.info("drive watch channel renewed")
            except (DriveWatchFailedError, DriveConnectorNotConfiguredError):
                logger.warning("drive watch renewal failed")

    async with session_factory() as session:
        service = DriveSyncService(session, settings=settings)
        try:
            summary = await service.process_changes(trigger="poll")
            if summary.processed:
                logger.info(
                    "drive poll: processed=%d new=%d updated=%d unavailable=%d",
                    summary.processed,
                    summary.new_documents,
                    summary.updated_documents,
                    summary.unavailable_documents,
                )
        except (
            DriveChangesFailedError,
            DriveConnectorNotConfiguredError,
            DriveFolderNotConfiguredError,
        ) as exc:
            # sync_failed event/last_error는 service가 기록했다.
            logger.warning("drive poll failed: %s", exc.__class__.__name__)
