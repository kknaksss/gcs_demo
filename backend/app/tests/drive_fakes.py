"""Drive API fake — httpx.MockTransport 기반 (WORK-003 테스트 공용).

외부 네트워크 실호출 없이 token/changes/files/watch 표면을 흉내낸다.
실 Drive 연동 검증은 env 투입 후 별도 수행 (WORK-003 Meta external dependency).
"""

from __future__ import annotations

import datetime as dt
import json

import httpx

from app.core.config import Settings
from app.integrations.google_drive import DriveClientConfig, GoogleDriveClient
from app.services.drive_sync import DriveSyncService

FOLDER_ID = "folder-selected"
FOLDER_MIME = "application/vnd.google-apps.folder"


def make_file(
    file_id: str,
    *,
    name: str = "문서.pdf",
    mime_type: str = "application/pdf",
    parents: list[str] | None = None,
    modified_time: str = "2026-07-08T10:00:00.000Z",
    version: str = "1",
    trashed: bool = False,
    web_view_link: str | None = None,
) -> dict:
    return {
        "id": file_id,
        "name": name,
        "mimeType": mime_type,
        "parents": parents if parents is not None else [FOLDER_ID],
        "modifiedTime": modified_time,
        "version": version,
        "trashed": trashed,
        "webViewLink": web_view_link or f"https://drive.google.com/file/d/{file_id}",
    }


def change_for(file: dict) -> dict:
    return {"fileId": file["id"], "removed": False, "file": file}


def removed_change(file_id: str) -> dict:
    return {"fileId": file_id, "removed": True}


class FakeDrive:
    """token/changes/files/watch/stop 을 제공하는 fake Drive 백엔드."""

    def __init__(self, folder_id: str = FOLDER_ID) -> None:
        self.folder_id = folder_id
        self.files: dict[str, dict] = {
            folder_id: {
                "id": folder_id,
                "name": "Cloud Intake Demo",
                "mimeType": FOLDER_MIME,
                "parents": [],
            }
        }
        self.pages: dict[str, dict] = {}
        self.start_token = "start-1"
        self.token_calls = 0
        self.changes_tokens: list[str] = []
        self.fail_changes = False
        self.watch_expiration_ms: int | None = None

    def add_file(self, file: dict) -> dict:
        self.files[file["id"]] = file
        return file

    def set_page(
        self,
        token: str,
        changes: list[dict],
        *,
        next_page_token: str | None = None,
        new_start_page_token: str | None = None,
    ) -> None:
        page: dict = {"changes": changes}
        if next_page_token:
            page["nextPageToken"] = next_page_token
        if new_start_page_token:
            page["newStartPageToken"] = new_start_page_token
        self.pages[token] = page

    # ------------------------------------------------------------------

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path

        if url.startswith("https://oauth2.googleapis.com/token"):
            self.token_calls += 1
            return httpx.Response(
                200, json={"access_token": "fake-access", "expires_in": 3600}
            )

        if path.endswith("/changes/startPageToken"):
            return httpx.Response(200, json={"startPageToken": self.start_token})

        if path.endswith("/changes/watch"):
            body = json.loads(request.content)
            expiration = self.watch_expiration_ms or int(
                (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1)).timestamp()
                * 1000
            )
            return httpx.Response(
                200,
                json={
                    "id": body["id"],
                    "resourceId": "resource-1",
                    "expiration": str(expiration),
                },
            )

        if path.endswith("/channels/stop"):
            return httpx.Response(204)

        if path.endswith("/changes"):
            if self.fail_changes:
                return httpx.Response(500, json={"error": "boom"})
            token = request.url.params.get("pageToken")
            self.changes_tokens.append(token)
            page = self.pages.get(token)
            if page is None:
                return httpx.Response(
                    200, json={"changes": [], "newStartPageToken": token}
                )
            return httpx.Response(200, json=page)

        if "/files/" in path:
            file_id = path.rsplit("/", 1)[-1]
            file = self.files.get(file_id)
            if file is None:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json=file)

        return httpx.Response(404, json={"error": f"unhandled {path}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def make_drive_settings(fake: FakeDrive, **overrides) -> Settings:
    defaults = dict(
        google_drive_client_id="test-client-id",
        google_drive_client_secret="test-client-secret",
        google_drive_refresh_token="test-refresh-token",
        google_drive_selected_folder_id=fake.folder_id,
        google_drive_webhook_url="https://demo.example.com/webhooks/google-drive",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def make_drive_client(fake: FakeDrive, settings: Settings) -> GoogleDriveClient:
    return GoogleDriveClient(
        DriveClientConfig(
            client_id=settings.google_drive_client_id,
            client_secret=settings.google_drive_client_secret,
            refresh_token=settings.google_drive_refresh_token,
        ),
        transport=fake.transport(),
    )


def make_sync_service(session, fake: FakeDrive, **overrides) -> DriveSyncService:
    settings = make_drive_settings(fake, **overrides)
    return DriveSyncService(
        session, client=make_drive_client(fake, settings), settings=settings
    )
