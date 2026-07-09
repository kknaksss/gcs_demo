"""Google Drive API client — WORK-003 (SPEC-004).

레이어 규칙(ARCH-001 §4): Drive API 호출은 이 모듈 안에서만 수행한다.
- OAuth: env(client_id/secret/refresh_token) 기반 refresh token flow. scope는
  `drive.readonly` 전제 (DEC-019). access token은 프로세스 메모리에만 둔다.
- 표면: changes.getStartPageToken / changes.list / files.get / changes.watch /
  channels.stop (SPEC-004) + files.export / files.get?alt=media 텍스트 추출
  (WORK-004 — SPEC-007 analysis_text 최소 범위).
- 추출한 본문은 task 입력 전달용이다. 제품 DB 장기 저장 금지 (DEC-019).

테스트는 httpx.MockTransport 주입으로 실호출 없이 검증한다 (transport 인자).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import httpx

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"

# SPEC-003/004 mirror 계약에 필요한 필드만 요청한다.
FILE_FIELDS = "id,name,mimeType,webViewLink,modifiedTime,version,trashed,parents"
CHANGES_FIELDS = (
    "nextPageToken,newStartPageToken,"
    f"changes(fileId,removed,time,changeType,file({FILE_FIELDS}))"
)


class DriveNotConfiguredError(Exception):
    """DRIVE_CONNECTOR_NOT_CONFIGURED — client_id/secret/refresh_token 미설정."""


class DriveApiError(Exception):
    """Drive API 호출 실패 (token/HTTP 오류). message에 secret을 담지 않는다."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class DriveFileNotFoundError(DriveApiError):
    """files.get 404 — 삭제된 파일 등."""

    def __init__(self, file_id: str) -> None:
        super().__init__(f"drive file not found: {file_id}", status_code=404)


@dataclass(frozen=True)
class DriveClientConfig:
    client_id: str
    client_secret: str
    refresh_token: str

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.refresh_token)


class GoogleDriveClient:
    """httpx 기반 Drive v3 client. 인스턴스는 access token을 캐시한다."""

    def __init__(
        self,
        config: DriveClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._config = config
        self._transport = transport
        self._timeout = timeout
        self._access_token: str | None = None
        self._token_expires_at: dt.datetime | None = None

    @property
    def configured(self) -> bool:
        return self._config.configured

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    # ------------------------------------------------------------------
    # OAuth refresh token flow
    # ------------------------------------------------------------------

    async def _get_access_token(self) -> str:
        if not self.configured:
            raise DriveNotConfiguredError
        now = dt.datetime.now(dt.timezone.utc)
        if (
            self._access_token
            and self._token_expires_at
            and now < self._token_expires_at
        ):
            return self._access_token

        async with self._client() as client:
            try:
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "client_id": self._config.client_id,
                        "client_secret": self._config.client_secret,
                        "refresh_token": self._config.refresh_token,
                    },
                )
            except httpx.HTTPError as exc:  # 네트워크 계열
                raise DriveApiError(f"token refresh failed: {exc.__class__.__name__}")
        if resp.status_code != 200:
            raise DriveApiError(
                "token refresh rejected", status_code=resp.status_code
            )
        body = resp.json()
        self._access_token = body["access_token"]
        # 60초 여유를 두고 만료 처리한다.
        expires_in = int(body.get("expires_in", 3600))
        self._token_expires_at = now + dt.timedelta(seconds=max(expires_in - 60, 0))
        return self._access_token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        token = await self._get_access_token()
        async with self._client() as client:
            try:
                resp = await client.request(
                    method,
                    f"{DRIVE_API_BASE}{path}",
                    params=params,
                    json=json_body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise DriveApiError(f"drive api failed: {exc.__class__.__name__}")
        if resp.status_code == 404:
            raise DriveApiError("drive resource not found", status_code=404)
        if resp.status_code >= 400:
            raise DriveApiError(
                f"drive api error on {path}", status_code=resp.status_code
            )
        if resp.status_code == 204 or not resp.content:
            return {}
        return resp.json()

    # ------------------------------------------------------------------
    # Drive v3 surface (SPEC-004 In scope)
    # ------------------------------------------------------------------

    async def get_start_page_token(self) -> str:
        body = await self._request("GET", "/changes/startPageToken")
        return body["startPageToken"]

    async def list_changes(self, page_token: str, *, page_size: int = 100) -> dict:
        """changes.list — {changes, nextPageToken?, newStartPageToken?}."""
        return await self._request(
            "GET",
            "/changes",
            params={
                "pageToken": page_token,
                "pageSize": page_size,
                "includeRemoved": "true",
                "fields": CHANGES_FIELDS,
            },
        )

    async def get_file(self, file_id: str) -> dict:
        try:
            return await self._request(
                "GET", f"/files/{file_id}", params={"fields": FILE_FIELDS}
            )
        except DriveApiError as exc:
            if exc.status_code == 404:
                raise DriveFileNotFoundError(file_id)
            raise

    async def watch_changes(
        self,
        *,
        page_token: str,
        channel_id: str,
        address: str,
        channel_token: str,
        ttl_sec: int | None = None,
    ) -> dict:
        """changes.watch — web_hook channel 등록. {id, resourceId, expiration}."""
        body: dict = {
            "id": channel_id,
            "type": "web_hook",
            "address": address,
            "token": channel_token,
        }
        if ttl_sec is not None:
            expiration_ms = int(
                (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl_sec))
                .timestamp()
                * 1000
            )
            body["expiration"] = str(expiration_ms)
        return await self._request(
            "POST", "/changes/watch", params={"pageToken": page_token}, json_body=body
        )

    async def stop_channel(self, channel_id: str, resource_id: str) -> None:
        await self._request(
            "POST",
            "/channels/stop",
            json_body={"id": channel_id, "resourceId": resource_id},
        )

    # ------------------------------------------------------------------
    # analysis_text 추출 (WORK-004 — 텍스트 계열 최소 범위, PDF/OCR 보류)
    # ------------------------------------------------------------------

    async def _request_text(self, path: str, *, params: dict | None = None) -> str:
        token = await self._get_access_token()
        async with self._client() as client:
            try:
                resp = await client.get(
                    f"{DRIVE_API_BASE}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                raise DriveApiError(f"drive api failed: {exc.__class__.__name__}")
        if resp.status_code == 404:
            raise DriveApiError("drive resource not found", status_code=404)
        if resp.status_code >= 400:
            raise DriveApiError(
                f"drive api error on {path}", status_code=resp.status_code
            )
        return resp.text

    async def export_file_text(self, file_id: str) -> str:
        """Google Docs 계열 → files.export(text/plain)."""
        return await self._request_text(
            f"/files/{file_id}/export", params={"mimeType": "text/plain"}
        )

    async def download_file_text(self, file_id: str) -> str:
        """text/* 파일 → files.get?alt=media 본문."""
        return await self._request_text(
            f"/files/{file_id}", params={"alt": "media"}
        )
