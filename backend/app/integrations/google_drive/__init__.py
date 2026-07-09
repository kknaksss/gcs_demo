"""Google Drive client (SPEC-004). scope는 v1 `drive.readonly`만 허용.

Drive API 호출은 이 패키지 전용 — router/service에서 직접 호출 금지
(integrations 격리, ARCH-001 §4/§12). 본문 export client는 WORK-004 경계.
"""

from app.integrations.google_drive.client import (
    DRIVE_READONLY_SCOPE,
    DriveApiError,
    DriveClientConfig,
    DriveFileNotFoundError,
    DriveNotConfiguredError,
    GoogleDriveClient,
)

__all__ = [
    "DRIVE_READONLY_SCOPE",
    "DriveApiError",
    "DriveClientConfig",
    "DriveFileNotFoundError",
    "DriveNotConfiguredError",
    "GoogleDriveClient",
]
