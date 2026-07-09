"""Drive sync service — WORK-003 (SPEC-004).

- webhook/수동 retry/worker 폴링/재시작 이어받기가 모두 `apply_change` 단일
  진입을 사용한다 (Internal Interface Contract — 멱등 보장 지점).
- notification은 trigger일 뿐, 변경 원장은 `changes.list`다.
- mirror 필드만 갱신, 승인 metadata 불가침. 원문/본문 저장 금지 (DEC-019).
- fingerprint 변경 → pending candidate stale + `candidate_staled` event +
  stale_reanalysis job 자동 enqueue (DEC-022, WORK-004).
- document 신규 upsert → classification job enqueue (SPEC-007 S-1).
  trashed/removed/out_of_scope 문서는 enqueue 대상이 아니다 — 훅은 active
  upsert 경로에서만 호출된다.

webhook 검증(확정, spec 환류 대상): `X-Goog-Channel-ID`가 drive_sync_state의
watch_channel_id와 일치하고, `X-Goog-Channel-Token`이 채널 등록 시 넣은
HMAC-SHA256(jwt_secret, channel_id)과 일치해야 한다. 불일치 시
`webhook_received` event를 `DRIVE_WEBHOOK_INVALID`로 기록만 하고 무시한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.integrations.google_drive import (
    DriveApiError,
    DriveClientConfig,
    DriveFileNotFoundError,
    GoogleDriveClient,
)
from app.repos.documents import DocumentRepository
from app.repos.drive_sync import DriveSyncEventRepository, DriveSyncStateRepository
from app.services.documents import DocumentsService, build_fingerprint

logger = logging.getLogger(__name__)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_SCOPE_WALK_MAX_DEPTH = 16


class DriveConnectorNotConfiguredError(Exception):
    """DRIVE_CONNECTOR_NOT_CONFIGURED — Drive OAuth env 누락."""


class DriveFolderNotConfiguredError(Exception):
    """DRIVE_FOLDER_NOT_CONFIGURED — 감시 폴더 env 누락."""


class DriveChangesFailedError(Exception):
    """DRIVE_CHANGES_FAILED — changes.list/token 처리 실패."""


class DriveWatchFailedError(Exception):
    """DRIVE_WATCH_EXPIRED — watch channel 등록/갱신 실패."""


@dataclass
class SyncOutcome:
    """apply_change 결과 — upserted | unavailable | skipped."""

    status: str
    document_id: int | None = None
    created: bool = False
    fingerprint_changed: bool = False


@dataclass
class SyncSummary:
    processed: int = 0
    new_documents: int = 0
    updated_documents: int = 0
    unavailable_documents: int = 0
    skipped: int = 0
    failed: int = 0
    page_token: str | None = None
    outcomes: list[SyncOutcome] = field(default_factory=list)


@dataclass
class ConnectorStatus:
    """GET /admin/drive-connector 응답 원천 (SPEC-004 Connector status)."""

    status: str  # connected | disconnected | watch_expiring | error
    scope: str
    selected_folder_id: str | None
    selected_folder_name: str | None
    watch_channel_id: str | None
    watch_expires_at: dt.datetime | None
    last_sync_at: dt.datetime | None
    last_error: str | None
    page_token: str | None


def webhook_channel_token(secret: str, channel_id: str) -> str:
    """채널 등록 시 Drive에 넘기고, webhook 수신 시 재계산해 비교하는 token."""
    return hmac.new(
        secret.encode(), channel_id.encode(), hashlib.sha256
    ).hexdigest()


def build_drive_client(
    settings: Settings, *, transport=None
) -> GoogleDriveClient:
    return GoogleDriveClient(
        DriveClientConfig(
            client_id=settings.google_drive_client_id,
            client_secret=settings.google_drive_client_secret,
            refresh_token=settings.google_drive_refresh_token,
        ),
        transport=transport,
    )


class DriveSyncService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        client: GoogleDriveClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._client = client or build_drive_client(self._settings)
        self._docs = DocumentRepository(session)
        self._state = DriveSyncStateRepository(session)
        self._events = DriveSyncEventRepository(session)
        self._documents_service = DocumentsService(session)
        # 한 sync run 안에서 조상 폴더 판정 결과를 재사용한다.
        self._scope_cache: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # WORK-004 hook — AI classification enqueue (Internal Interface Contract)
    # ------------------------------------------------------------------

    async def on_document_upserted(self, document_id: int, fingerprint: dict) -> None:
        """신규 document → classification job enqueue (SPEC-007 S-1, 멱등)."""
        from app.services.ai_jobs import AiJobsService, DocumentUnavailableError

        try:
            await AiJobsService(self._session, settings=self._settings).enqueue_classification(
                document_id, fingerprint
            )
        except DocumentUnavailableError:
            # active 경로에서만 호출되지만, 경합 시에도 sync는 계속 진행한다.
            logger.info("classification enqueue skipped: document unavailable")

    async def on_fingerprint_changed(
        self, document_id: int, fingerprint: dict
    ) -> None:
        """stale 후 자동 재분석 — stale_reanalysis job enqueue (DEC-022)."""
        from app.services.ai_jobs import AiJobsService, DocumentUnavailableError

        try:
            job, created = await AiJobsService(
                self._session, settings=self._settings
            ).enqueue_stale_reanalysis(document_id, fingerprint)
        except DocumentUnavailableError:
            logger.info("stale reanalysis skipped: document unavailable")
            return
        if created:
            await self._events.record(
                event_type="reanalysis_enqueued",
                result="success",
                document_id=document_id,
                message=f"stale_reanalysis job {job.id} enqueued",
            )

    # ------------------------------------------------------------------
    # 설정 가드
    # ------------------------------------------------------------------

    def _require_configured(self) -> None:
        if not self._client.configured:
            raise DriveConnectorNotConfiguredError
        if not self._settings.google_drive_selected_folder_id:
            raise DriveFolderNotConfiguredError

    # ------------------------------------------------------------------
    # 선택 폴더 scope 판정
    # ------------------------------------------------------------------

    async def _is_in_scope(self, file: dict) -> bool:
        """파일이 선택 폴더 하위인지 — parents 사슬을 files.get으로 거슬러 판정."""
        selected = self._settings.google_drive_selected_folder_id
        if file.get("id") == selected:
            return True
        parents = list(file.get("parents") or [])
        depth = 0
        while parents and depth < _SCOPE_WALK_MAX_DEPTH:
            depth += 1
            parent_id = parents[0]
            if parent_id == selected:
                return True
            if parent_id in self._scope_cache:
                return self._scope_cache[parent_id]
            try:
                parent = await self._client.get_file(parent_id)
            except DriveFileNotFoundError:
                self._scope_cache[parent_id] = False
                return False
            next_parents = list(parent.get("parents") or [])
            in_scope = selected in next_parents
            if in_scope or not next_parents:
                self._scope_cache[parent_id] = in_scope
                return in_scope
            parents = next_parents
        return False

    # ------------------------------------------------------------------
    # 단일 change 적용 (멱등 진입점)
    # ------------------------------------------------------------------

    async def apply_change(self, change: dict) -> SyncOutcome:
        """webhook/수동 retry/폴링/이어받기 공용 단일 진입 — 멱등.

        같은 change를 여러 번 적용해도 최종 document 상태는 같다 (SPEC-004 AC).
        """
        file_id = change.get("fileId")
        if not file_id:
            return SyncOutcome(status="skipped")

        if change.get("removed"):
            return await self._apply_unavailable(file_id, "removed")

        file = change.get("file")
        if file is None:
            try:
                file = await self._client.get_file(file_id)
            except DriveFileNotFoundError:
                return await self._apply_unavailable(file_id, "removed")

        if file.get("trashed"):
            return await self._apply_unavailable(file_id, "trashed")

        if file.get("mimeType") == FOLDER_MIME_TYPE:
            # 폴더 자체는 document record 대상이 아니다.
            return SyncOutcome(status="skipped")

        if not await self._is_in_scope(file):
            return await self._apply_unavailable(file_id, "out_of_scope")

        return await self._apply_upsert(file_id, file)

    async def _apply_unavailable(self, file_id: str, state: str) -> SyncOutcome:
        """삭제/휴지통/범위 제외 → drive_state soft delete 전환 (hard delete 금지)."""
        document = await self._docs.get_by_drive_file_id(file_id)
        if document is None:
            # 수집된 적 없는 파일 — 범위 밖/삭제 모두 수집 제외 (backfill 금지).
            return SyncOutcome(status="skipped")
        already = document.drive_state == state
        if not already:
            await self._docs.set_drive_state(document.id, state)
            blocked = await self._documents_service.mark_candidates_blocked(
                document.id,
                reason=f"document unavailable: drive_state={state}",
            )
            message = f"drive_state -> {state}"
            if state == "out_of_scope":
                message = f"DRIVE_FILE_OUT_OF_SCOPE: {message}"
            if blocked:
                message += f" (pending candidates blocked: {len(blocked)})"
            await self._events.record(
                event_type="document_unavailable",
                result="success",
                drive_file_id=file_id,
                document_id=document.id,
                message=message,
            )
        return SyncOutcome(status="unavailable", document_id=document.id)

    async def _apply_upsert(self, file_id: str, file: dict) -> SyncOutcome:
        modified_time = _parse_datetime(file.get("modifiedTime"))
        fingerprint = build_fingerprint(
            drive_file_id=file_id,
            drive_modified_time=modified_time,
            drive_name=file.get("name") or "",
            mime_type=file.get("mimeType") or "",
            version=file.get("version"),
            # content_fingerprint는 본문을 읽은 경우에만 — 본문 읽기는 WORK-004 경계.
        )

        existing = await self._docs.get_by_drive_file_id(file_id)
        previous_fingerprint = existing.drive_fingerprint if existing else None
        previous_state = existing.drive_state if existing else None

        document, created = await self._docs.upsert_mirror(
            drive_file_id=file_id,
            drive_name=file["name"],
            drive_mime_type=file["mimeType"],
            drive_web_url=file.get("webViewLink"),
            drive_modified_time=modified_time,
            drive_state="active",  # trashed/out_of_scope → active 복구 전이 포함
            drive_fingerprint=fingerprint,
        )

        changed = (not created) and previous_fingerprint != fingerprint
        if changed:
            staled = await self._documents_service.mark_candidates_stale(
                document.id, fingerprint
            )
            for candidate in staled:
                await self._events.record(
                    event_type="candidate_staled",
                    result="success",
                    drive_file_id=file_id,
                    document_id=document.id,
                    message=f"candidate {candidate.id} staled: fingerprint changed",
                )
            await self.on_fingerprint_changed(document.id, fingerprint)

        if created or changed or previous_state != "active":
            if created:
                message = "new document"
            elif previous_state != "active":
                message = f"restored: {previous_state} -> active"
            else:
                message = "mirror updated (fingerprint changed)"
            await self._events.record(
                event_type="document_upserted",
                result="success",
                drive_file_id=file_id,
                document_id=document.id,
                message=message,
            )
        if created:
            await self.on_document_upserted(document.id, fingerprint)

        return SyncOutcome(
            status="upserted",
            document_id=document.id,
            created=created,
            fingerprint_changed=changed,
        )

    # ------------------------------------------------------------------
    # changes.list 처리 + page token 이어받기
    # ------------------------------------------------------------------

    async def process_changes(self, *, trigger: str) -> SyncSummary:
        """changes.list 기반 sync 본체. token은 처리 성공 후에만 전진 저장한다."""
        self._require_configured()
        state = await self._state.ensure()
        summary = SyncSummary()

        try:
            token = state.page_token or await self._client.get_start_page_token()
            while token:
                page = await self._client.list_changes(token)
                for change in page.get("changes", []):
                    try:
                        outcome = await self.apply_change(change)
                    except Exception as exc:  # change 단위 실패는 기록 후 계속
                        summary.failed += 1
                        await self._events.record(
                            event_type="sync_failed",
                            result="failed",
                            drive_file_id=change.get("fileId"),
                            message=f"change apply failed: {exc.__class__.__name__}",
                        )
                        continue
                    summary.processed += 1
                    summary.outcomes.append(outcome)
                    if outcome.status == "upserted":
                        if outcome.created:
                            summary.new_documents += 1
                        else:
                            summary.updated_documents += 1
                    elif outcome.status == "unavailable":
                        summary.unavailable_documents += 1
                    else:
                        summary.skipped += 1

                next_token = page.get("nextPageToken") or page.get(
                    "newStartPageToken"
                )
                # 처리 성공 후 다음 token 저장 (SPEC-004 Validation change token).
                await self._state.update(page_token=next_token)
                await self._session.commit()
                if page.get("nextPageToken"):
                    token = next_token
                else:
                    break

            now = dt.datetime.now(dt.timezone.utc)
            await self._state.update(last_sync_at=now, last_error=None)
            # 빈 폴링 tick마다 event를 쌓지 않는다 — 변경이 있었던 run만 감사에 남긴다.
            if summary.processed or summary.failed or trigger != "poll":
                await self._events.record(
                    event_type="changes_listed",
                    result="success",
                    message=(
                        f"trigger={trigger}, processed={summary.processed}, "
                        f"new={summary.new_documents}, "
                        f"updated={summary.updated_documents}, "
                        f"unavailable={summary.unavailable_documents}, "
                        f"skipped={summary.skipped}, failed={summary.failed}"
                    ),
                )
            await self._session.commit()
        except DriveApiError as exc:
            await self._state.update(last_error=str(exc))
            await self._events.record(
                event_type="sync_failed",
                result="failed",
                message=f"DRIVE_CHANGES_FAILED: {exc}",
            )
            await self._session.commit()
            raise DriveChangesFailedError(str(exc)) from exc

        refreshed = await self._state.get()
        summary.page_token = refreshed.page_token if refreshed else None
        return summary

    async def retry_sync(self) -> SyncSummary:
        """POST /admin/drive-connector/sync/retry — 실패 sync 재처리(멱등)."""
        return await self.process_changes(trigger="admin_retry")

    # ------------------------------------------------------------------
    # watch channel 등록/갱신
    # ------------------------------------------------------------------

    async def register_watch(self) -> ConnectorStatus:
        """changes.watch channel 등록/갱신 — 기존 channel은 best-effort stop."""
        self._require_configured()
        webhook_url = self._settings.google_drive_webhook_url
        if not webhook_url or not webhook_url.startswith("https://"):
            # SPEC-004 Validation: webhook URL은 HTTPS. env 미비로 취급한다.
            raise DriveConnectorNotConfiguredError

        state = await self._state.ensure()
        page_token = state.page_token or await self._client.get_start_page_token()
        channel_id = str(uuid.uuid4())
        channel_token = webhook_channel_token(self._settings.jwt_secret, channel_id)

        old_channel_id = state.watch_channel_id
        old_resource_id = state.watch_resource_id

        try:
            result = await self._client.watch_changes(
                page_token=page_token,
                channel_id=channel_id,
                address=webhook_url,
                channel_token=channel_token,
                ttl_sec=self._settings.drive_watch_ttl_sec or None,
            )
        except DriveApiError as exc:
            await self._state.update(last_error=f"watch failed: {exc}")
            await self._session.commit()
            raise DriveWatchFailedError(str(exc)) from exc

        expires_at = _parse_expiration_ms(result.get("expiration"))
        await self._state.update(
            page_token=page_token,
            watch_channel_id=result.get("id", channel_id),
            watch_resource_id=result.get("resourceId"),
            watch_expires_at=expires_at,
            last_error=None,
        )
        await self._session.commit()

        if old_channel_id and old_resource_id:
            try:
                await self._client.stop_channel(old_channel_id, old_resource_id)
            except DriveApiError:
                logger.warning("failed to stop old watch channel %s", old_channel_id)

        return await self.connector_status(resolve_folder_name=False)

    def _watch_expiring(self, expires_at: dt.datetime | None) -> bool:
        if expires_at is None:
            return True  # watch 미등록/만료 정보 없음 → 갱신 필요로 표기
        threshold = dt.timedelta(
            seconds=self._settings.drive_watch_expiring_threshold_sec
        )
        return expires_at - dt.datetime.now(dt.timezone.utc) <= threshold

    # ------------------------------------------------------------------
    # connector status (SPEC-004 U-1)
    # ------------------------------------------------------------------

    async def connector_status(
        self, *, resolve_folder_name: bool = True
    ) -> ConnectorStatus:
        settings = self._settings
        state = await self._state.get()
        folder_id = settings.google_drive_selected_folder_id or None
        configured = self._client.configured and bool(folder_id)

        folder_name: str | None = None
        if configured and resolve_folder_name:
            try:
                folder = await self._client.get_file(folder_id)  # type: ignore[arg-type]
                folder_name = folder.get("name")
            except DriveApiError:
                folder_name = None  # 상태 조회는 Drive 실패에도 응답한다

        if not configured:
            status = "disconnected"
        elif state is not None and state.last_error:
            status = "error"
        elif self._watch_expiring(state.watch_expires_at if state else None):
            status = "watch_expiring"
        else:
            status = "connected"

        return ConnectorStatus(
            status=status,
            scope="drive.readonly",
            selected_folder_id=folder_id,
            selected_folder_name=folder_name,
            watch_channel_id=state.watch_channel_id if state else None,
            watch_expires_at=state.watch_expires_at if state else None,
            last_sync_at=state.last_sync_at if state else None,
            last_error=state.last_error if state else None,
            page_token=state.page_token if state else None,
        )

    # ------------------------------------------------------------------
    # webhook 수신 (trigger로만 사용)
    # ------------------------------------------------------------------

    async def handle_webhook(
        self,
        *,
        channel_id: str | None,
        channel_token: str | None,
        resource_id: str | None,
        resource_state: str | None,
    ) -> bool:
        """Drive push notification 수신. 반환값: 유효 알림 여부.

        payload는 신뢰하지 않는다 — 유효하면 changes.list 처리를 trigger한다.
        """
        state = await self._state.get()
        expected_channel = state.watch_channel_id if state else None
        expected_token = (
            webhook_channel_token(self._settings.jwt_secret, expected_channel)
            if expected_channel
            else None
        )
        valid = (
            expected_channel is not None
            and channel_id == expected_channel
            and channel_token == expected_token
            and (
                state.watch_resource_id is None
                or resource_id == state.watch_resource_id
            )
        )
        if not valid:
            await self._events.record(
                event_type="webhook_received",
                result="failed",
                message="DRIVE_WEBHOOK_INVALID: channel id/token mismatch",
            )
            await self._session.commit()
            return False

        await self._events.record(
            event_type="webhook_received",
            result="success",
            message=f"resource_state={resource_state or 'unknown'}",
        )
        await self._session.commit()

        if resource_state == "sync":
            # channel 등록 직후 확인 ping — 변경 처리는 하지 않는다.
            return True

        try:
            await self.process_changes(trigger="webhook")
        except (
            DriveChangesFailedError,
            DriveConnectorNotConfiguredError,
            DriveFolderNotConfiguredError,
        ):
            # 실패는 sync_failed event/last_error로 이미 기록 — webhook 응답은 200 계열.
            logger.warning("webhook-triggered sync failed")
        return True

    # ------------------------------------------------------------------
    # sync events 조회 (감사)
    # ------------------------------------------------------------------

    async def list_events(self, *, limit: int, offset: int):
        return await self._events.list_recent(limit=limit, offset=offset)


def _parse_datetime(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_expiration_ms(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.timezone.utc)
