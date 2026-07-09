"""Backend worker entrypoint (ARCH-001 §9) — WORK-003 + WORK-004.

두 개의 주기 루프를 함께 돌린다.
- Drive sync 폴링(webhook 불가 로컬 대안) + watch 만료 갱신 체크 (WORK-003).
- AI classification job 소비: DB `ai_queue_jobs` 원장 폴링 → open-kknaks
  task submit → 결과 검증/candidate 저장 (WORK-004). Redis는 open-kknaks
  dispatch 전용 — 상태 SoT는 PostgreSQL이라 재시작에도 이어받는다.
"""

import asyncio
import logging

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.session import get_session_factory
from app.workers.ai_jobs import run_ai_jobs_once
from app.workers.drive_sync import run_poll_once

logger = logging.getLogger(__name__)


async def _drive_sync_loop(session_factory, settings) -> None:
    while True:
        try:
            await run_poll_once(session_factory, settings)
        except Exception:  # tick 단위 격리 — worker는 죽지 않는다
            logger.exception("drive sync tick failed")
        await asyncio.sleep(settings.drive_sync_poll_interval_sec)


async def _ai_jobs_loop(session_factory, settings) -> None:
    while True:
        try:
            await run_ai_jobs_once(session_factory, settings)
        except Exception:  # tick 단위 격리
            logger.exception("ai jobs tick failed")
        await asyncio.sleep(settings.ai_jobs_poll_interval_sec)


async def run() -> None:
    settings = get_settings()
    session_factory = get_session_factory()
    logger.info("worker started", extra={"job_id": None})
    logger.info("redis dispatch target: %s", settings.redis_url)
    logger.info(
        "drive sync poll interval: %ss / ai jobs poll interval: %ss",
        settings.drive_sync_poll_interval_sec,
        settings.ai_jobs_poll_interval_sec,
    )
    await asyncio.gather(
        _drive_sync_loop(session_factory, settings),
        _ai_jobs_loop(session_factory, settings),
    )


def main() -> None:
    setup_logging()
    asyncio.run(run())


if __name__ == "__main__":
    main()
