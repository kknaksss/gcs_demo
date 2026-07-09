"""GoogleDriveClient 단위 테스트 — httpx.MockTransport, 실호출 없음 (WORK-003)."""

from __future__ import annotations

import httpx
import pytest

from app.integrations.google_drive import (
    DriveApiError,
    DriveClientConfig,
    DriveFileNotFoundError,
    DriveNotConfiguredError,
    GoogleDriveClient,
)
from app.tests.drive_fakes import FakeDrive, make_file

pytestmark = pytest.mark.asyncio


def _client(fake: FakeDrive) -> GoogleDriveClient:
    return GoogleDriveClient(
        DriveClientConfig("cid", "secret", "refresh"), transport=fake.transport()
    )


async def test_unconfigured_client_raises() -> None:
    client = GoogleDriveClient(DriveClientConfig("", "", ""))
    with pytest.raises(DriveNotConfiguredError):
        await client.get_start_page_token()


async def test_access_token_is_cached_across_calls() -> None:
    fake = FakeDrive()
    client = _client(fake)
    assert await client.get_start_page_token() == "start-1"
    await client.get_start_page_token()
    assert fake.token_calls == 1  # refresh flow 1회, 이후 캐시 사용


async def test_get_file_returns_mirror_fields() -> None:
    fake = FakeDrive()
    fake.add_file(make_file("f-1", name="계약서.pdf"))
    client = _client(fake)
    file = await client.get_file("f-1")
    assert file["name"] == "계약서.pdf"
    assert file["mimeType"] == "application/pdf"


async def test_get_file_404_raises_not_found() -> None:
    fake = FakeDrive()
    client = _client(fake)
    with pytest.raises(DriveFileNotFoundError):
        await client.get_file("missing")


async def test_list_changes_sends_page_token() -> None:
    fake = FakeDrive()
    fake.set_page("tok-5", [], new_start_page_token="tok-6")
    client = _client(fake)
    page = await client.list_changes("tok-5")
    assert page["newStartPageToken"] == "tok-6"
    assert fake.changes_tokens == ["tok-5"]


async def test_watch_changes_returns_channel() -> None:
    fake = FakeDrive()
    client = _client(fake)
    result = await client.watch_changes(
        page_token="tok-1",
        channel_id="chan-1",
        address="https://demo.example.com/webhooks/google-drive",
        channel_token="secret-token",
        ttl_sec=3600,
    )
    assert result["id"] == "chan-1"
    assert result["resourceId"] == "resource-1"
    assert result["expiration"]


async def test_api_error_raises_drive_api_error() -> None:
    fake = FakeDrive()
    fake.fail_changes = True
    client = _client(fake)
    with pytest.raises(DriveApiError):
        await client.list_changes("tok-1")


async def test_token_rejection_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = GoogleDriveClient(
        DriveClientConfig("cid", "secret", "bad"),
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(DriveApiError):
        await client.get_start_page_token()
