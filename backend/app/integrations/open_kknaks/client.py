"""open-kknaks task client — WORK-004 (SPEC-007 Task Submit Contract, DEC-024).

레이어 규칙(ARCH-001 §4): open-kknaks 호출은 이 모듈 안에서만 수행한다.
- 실행은 AgentClient.submit(Redis broker) 경유 — Anthropic SDK 직접 import 금지.
- task payload에는 classification prompt만 담는다. Drive/DB secret, OAuth
  token을 절대 포함하지 않는다 (SPEC-007 Implementation Rules).
- 테스트는 ClassificationTaskClient protocol에 fake를 주입한다 — open-kknaks
  실 실행 검증은 env 투입 후 별도 수행 (WORK-004 제약).

open_kknaks import는 lazy로 둔다: 패키지가 없는 환경에서도 이 모듈 import와
protocol 기반 테스트가 가능해야 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.core.config import Settings

SUPPORTED_PROVIDERS = ("claude", "codex")

# open-kknaks Task terminal 상태 (OKK-SPEC-001).
_TASK_DONE = "done"
_TASK_FAILED = "failed"
_TASK_CANCELLED = "cancelled"

_TIMEOUT_EXCEPTIONS = ("TaskTimeoutError", "IdleTimeoutError")


class OpenKknaksNotConfiguredError(Exception):
    """OPEN_KKNAKS_NOT_CONFIGURED — broker/model env 미설정."""


class OpenKknaksProviderInvalidError(Exception):
    """OPEN_KKNAKS_PROVIDER_INVALID — claude/codex 외 provider."""


@dataclass
class TaskOutcome:
    """AgentClient 결과 요약 — worker가 job 상태 전이에 사용한다."""

    status: Literal["succeeded", "failed", "timeout"]
    task_id: str
    result_text: str | None = None
    error: str | None = None


class ClassificationTaskClient(Protocol):
    """worker가 의존하는 표면 — 테스트에서 fake로 대체한다."""

    async def submit(self, prompt: str) -> str:
        """task 제출 → external task id."""
        ...

    async def wait(self, task_id: str) -> TaskOutcome:
        """task 완료 대기 → 결과 요약."""
        ...

    async def close(self) -> None: ...


def validate_open_kknaks_settings(settings: Settings) -> None:
    """SPEC-007 필수 env 검사 — submit 전에 호출한다."""
    if not settings.open_kknaks_broker_url or not settings.open_kknaks_model:
        raise OpenKknaksNotConfiguredError
    if settings.open_kknaks_provider not in SUPPORTED_PROVIDERS:
        raise OpenKknaksProviderInvalidError(settings.open_kknaks_provider)


class OpenKknaksTaskClient:
    """AgentClient(Redis broker) 래퍼 — ClassificationTaskClient 구현."""

    def __init__(self, settings: Settings) -> None:
        validate_open_kknaks_settings(settings)
        self._settings = settings
        self._broker = None
        self._client = None

    async def _ensure_client(self):
        if self._client is None:
            # lazy import — 패키지 없는 환경에서 모듈 import가 깨지지 않게.
            from open_kknaks import AgentClient, RedisBroker

            self._broker = RedisBroker(
                url=self._settings.open_kknaks_broker_url,
                namespace=self._settings.open_kknaks_namespace,
            )
            await self._broker.connect()
            self._client = AgentClient(self._broker)
        return self._client

    async def submit(self, prompt: str) -> str:
        client = await self._ensure_client()
        return await client.submit(
            prompt,
            queue=self._settings.open_kknaks_queue,
            provider=self._settings.open_kknaks_provider,
            model=self._settings.open_kknaks_model,
            options={
                "stream": False,
                "timeout_sec": self._settings.open_kknaks_timeout_sec,
            },
        )

    async def wait(self, task_id: str) -> TaskOutcome:
        client = await self._ensure_client()
        # broker result stream 대기 + timeout 여유 (open-kknaks가 자체 timeout을
        # 먼저 걸고, 그래도 안 오면 여기서 CLASSIFICATION_TIMEOUT 처리).
        task = await client.result(
            task_id, timeout=self._settings.open_kknaks_timeout_sec + 30
        )
        if task is None:
            return TaskOutcome(
                status="failed", task_id=task_id, error="task not found in broker"
            )
        if task.status == _TASK_DONE:
            return TaskOutcome(
                status="succeeded", task_id=task_id, result_text=task.result
            )
        if task.status == _TASK_FAILED:
            if (task.exception_type or "") in _TIMEOUT_EXCEPTIONS:
                return TaskOutcome(
                    status="timeout", task_id=task_id, error=task.error
                )
            return TaskOutcome(status="failed", task_id=task_id, error=task.error)
        if task.status == _TASK_CANCELLED:
            return TaskOutcome(
                status="failed", task_id=task_id, error="task cancelled"
            )
        # 대기 초과 — 아직 terminal 아님.
        return TaskOutcome(
            status="timeout", task_id=task_id, error="result wait timed out"
        )

    async def close(self) -> None:
        if self._broker is not None:
            await self._broker.close()
            self._broker = None
            self._client = None


def build_task_client(settings: Settings) -> ClassificationTaskClient:
    """worker 기본 client factory — env 검증 포함."""
    return OpenKknaksTaskClient(settings)
