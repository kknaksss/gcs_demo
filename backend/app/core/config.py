"""Typed settings from env (ARCH-001 §10). Env names follow SPEC-004/SPEC-007."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", extra="ignore")

    # App
    app_env: str = "local"
    cors_origins: str = "http://localhost:3000"
    public_api_base_url: str = "http://localhost:8000"

    # DB / Redis
    database_url: str = "postgresql+asyncpg://gcs:gcs@localhost:5432/gcs_demo"
    redis_url: str = "redis://localhost:6379/0"

    # Auth (JWT access + refresh cookie, ARCH-001 §11)
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl: int = 1800
    refresh_token_ttl: int = 1209600
    # refresh token은 httpOnly secure cookie (ARCH-001 Accepted Defaults).
    refresh_cookie_name: str = "refresh_token"
    cookie_secure: bool = False  # local http는 false, prod https는 true
    cookie_samesite: str = "lax"
    # 데모 로그인 공통 password (SPEC-001 Open Issue 확정안). 원본 Mediness credential
    # 은 seed에 반영하지 않고, 모든 seed user에 이 공통 데모 password 해시를 부여한다.
    demo_user_password: str = "cfo-demo-2026"

    # Google Drive connector (SPEC-004 Environment contract)
    google_drive_client_id: str = ""
    google_drive_client_secret: str = ""
    google_drive_refresh_token: str = ""
    google_drive_selected_folder_id: str = ""
    google_drive_webhook_url: str = ""
    # WORK-003 sync 기본값 — 폴링 주기(webhook 불가 로컬 대안), watch 갱신 임계/TTL.
    drive_sync_poll_interval_sec: int = 60
    drive_watch_expiring_threshold_sec: int = 21600  # 6h 이내 만료 → watch_expiring
    drive_watch_ttl_sec: int = 86400  # changes.watch 요청 expiration (0이면 Google 기본)

    # open-kknaks (SPEC-007)
    open_kknaks_broker_url: str = ""
    open_kknaks_namespace: str = "gcs-demo"
    open_kknaks_provider: str = "claude"
    open_kknaks_model: str = ""
    open_kknaks_queue: str = "document-classification"
    open_kknaks_timeout_sec: int = 300

    # WORK-004 AI job worker 기본값 — DB 원장 폴링 주기/배치/재시도 backoff.
    ai_jobs_poll_interval_sec: int = 5
    ai_jobs_batch_size: int = 5
    ai_jobs_retry_backoff_sec: int = 30
    # analysis_text 최소 범위 상한 (원문 장기 저장 금지 — task 입력 전달만).
    analysis_text_max_chars: int = 20000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
