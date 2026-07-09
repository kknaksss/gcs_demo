"""테스트 픽스처 — 전용 test DB(gcs_demo_test)에 대해 실제 async 스택을 검증한다.

- 세션 스코프: 테스트 DB에 전 테이블 create_all / 종료 시 drop_all.
- 함수 스코프: 매 테스트 전 전 테이블 TRUNCATE로 격리.
- db_session: repo/service 직접 검증용 AsyncSession.
- client: httpx AsyncClient(ASGITransport) — get_db를 test factory로 오버라이드.

DATABASE_URL_TEST env로 접속 대상을 바꿀 수 있다(기본 localhost gcs_demo_test).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_db
from app.core.config import get_settings
from app.main import app
from app.models import Base

DEMO_PASSWORD = get_settings().demo_user_password

TEST_DATABASE_URL = os.environ.get(
    "DATABASE_URL_TEST",
    "postgresql+asyncpg://gcs:gcs@localhost:5432/gcs_demo_test",
)

# NullPool: 매 연산마다 새 연결을 열어 pytest-asyncio의 함수별 event loop 간
# asyncpg 연결 재사용(InterfaceError)을 피한다.
_engine = create_async_engine(TEST_DATABASE_URL, echo=False, poolclass=NullPool)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema() -> AsyncGenerator[None, None]:
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await _engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _truncate() -> AsyncGenerator[None, None]:
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    async with _engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with _Session() as session:
        yield session


@pytest_asyncio.fixture
async def seeded() -> list[dict]:
    """seed_users를 test DB에 실행하고 seed 레코드를 돌려준다 (Phase 2/3 공용)."""
    from app.seeds.users import load_seed_records, seed_users

    async with _Session() as session:
        await seed_users(session, DEMO_PASSWORD)
        await session.commit()
    return load_seed_records()


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with _Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()
