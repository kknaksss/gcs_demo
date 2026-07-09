"""Drive push notification 수신 — WORK-003 (SPEC-004).

- POST /webhooks/google-drive: 알림은 trigger로만 사용, payload는 신뢰하지 않는다.
  처리 자체는 changes.list 기반 (SPEC-004 Implementation Rules).
- 검증: X-Goog-Channel-ID == drive_sync_state.watch_channel_id AND
  X-Goog-Channel-Token == HMAC(jwt_secret, channel_id). 불일치 시
  DRIVE_WEBHOOK_INVALID를 sync audit에 기록 후 무시한다 — 항상 2xx로 응답해
  channel 정보를 외부에 누설하지 않는다 (Pre-deploy Check).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import get_drive_sync_service
from app.services.drive_sync import DriveSyncService

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks/google-drive", status_code=status.HTTP_204_NO_CONTENT)
async def receive_google_drive_notification(
    request: Request,
    service: DriveSyncService = Depends(get_drive_sync_service),
) -> Response:
    await service.handle_webhook(
        channel_id=request.headers.get("X-Goog-Channel-ID"),
        channel_token=request.headers.get("X-Goog-Channel-Token"),
        resource_id=request.headers.get("X-Goog-Resource-ID"),
        resource_state=request.headers.get("X-Goog-Resource-State"),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
