"""open-kknaks ClaudeWorker — cloud-file-organizer AI classification task 실행 (SPEC-007, DEC-024).

제품 backend(AgentClient.submit) → Redis broker → 이 worker가 claude를 실행해
구조화된 candidate JSON을 반환한다. 제품 DB/Drive에는 직접 쓰지 않는다.
"""

import asyncio
import os

from open_kknaks.broker.redis import RedisBroker
from open_kknaks.config import ClaudeConfig
from open_kknaks.middleware.cost import CostMiddleware
from open_kknaks.middleware.logging import LoggingMiddleware
from open_kknaks.middleware.retries import RetriesMiddleware
from open_kknaks.middleware.timeout import TimeoutMiddleware
from open_kknaks.worker.worker import ClaudeWorker

# 분류 작업 workspace — 이미지에 COPY된 프로젝트(진입 문서 CLAUDE.md/agent.md +
# context/classification-guide.md). claude를 이 디렉토리 "안에서" 실행하면 진입 문서를
# 스스로 읽는다. WORK_DIR env가 없을 때의 기본값을 run.py 옆 workspace로 고정한다
# (docker: /app/workspace, 로컬: ai_worker/workspace — 둘 다 이 경로로 해석됨).
DEFAULT_WORK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspace")


async def main() -> None:
    broker = RedisBroker(
        url=os.environ.get("OPEN_KKNAKS_BROKER_URL", "redis://localhost:6379/0"),
        namespace=os.environ.get("OPEN_KKNAKS_NAMESPACE", "gcs-demo"),
    )
    await broker.connect()

    config = ClaudeConfig(
        work_dir=os.environ.get("WORK_DIR", DEFAULT_WORK_DIR),
    )

    worker = ClaudeWorker(
        broker=broker,
        config=config,
        queues=os.environ.get("OPEN_KKNAKS_QUEUE", "document-classification").split(","),
        concurrency=int(os.environ.get("CONCURRENCY", "2")),
        middleware=[
            LoggingMiddleware(),
            RetriesMiddleware(max_retries=2),
            TimeoutMiddleware(),
            CostMiddleware(
                worker_budget_usd=5.0,
                global_budget_usd=20.0,
            ),
        ],
    )

    print(f"AI worker starting: queues={worker.queues}, concurrency={worker.concurrency}")

    try:
        await worker.run()
    finally:
        await broker.close()


if __name__ == "__main__":
    asyncio.run(main())
