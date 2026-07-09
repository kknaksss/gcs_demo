"""Mediness user seed — WORK-001 Phase 3 (SPEC-001 Source Seed 매핑, 멱등 upsert).

seed source는 `mediness.public.users` 26 rows에서 **안전한 필드만** 추출한 repo 내
`data/mediness_users.json`이다 (password/전화/생일 등 credential은 추출 단계에서 제외).
각 user는 `source_user_id` 기준으로 멱등 upsert되고, 로그인용으로는 원본 credential이
아니라 공통 데모 password(설정값)를 부여한다.

department 매핑: seed `department` 텍스트를 조직도 부서 노드 이름과 매칭한다(이름 규칙).
매칭 실패(department null 포함)는 admin 보정 대상으로 남긴다.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.repos.users import UserRepository
from app.seeds.organization import seed_organization

# 실데이터(mediness_users.json)는 직원 PII라 commit하지 않는다(.gitignore) — 로컬에만 둔다.
# 파일이 없으면 가짜 sample(users.sample.json)로 seed한다. SEED_USERS_FILE env로 override 가능.
_DATA_DIR = Path(__file__).parent / "data"
_REAL_DATA_FILE = _DATA_DIR / "mediness_users.json"
_SAMPLE_DATA_FILE = _DATA_DIR / "users.sample.json"


def _resolve_data_file() -> Path:
    import os

    override = os.environ.get("SEED_USERS_FILE")
    if override:
        return Path(override)
    return _REAL_DATA_FILE if _REAL_DATA_FILE.exists() else _SAMPLE_DATA_FILE

# seed JSON에서 users 테이블 컬럼으로 매핑 (SPEC-001 Source Seed).
_FIELD_KEYS = (
    "email",
    "name",
    "role",
    "position",
    "department",
    "active",
    "employment_type",
)


@dataclasses.dataclass
class SeedResult:
    total: int = 0
    created: int = 0
    updated: int = 0
    mapped: int = 0
    unmapped: int = 0


def load_seed_records() -> list[dict]:
    with _resolve_data_file().open(encoding="utf-8") as fh:
        return json.load(fh)


def _parse_resigned_at(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value)


async def seed_users(session: AsyncSession, demo_password: str) -> SeedResult:
    records = load_seed_records()
    department_names = [r.get("department") for r in records]
    dept_map = await seed_organization(session, department_names)

    demo_hash = hash_password(demo_password)
    users = UserRepository(session)
    result = SeedResult(total=len(records))

    for rec in records:
        fields = {key: rec.get(key) for key in _FIELD_KEYS}
        fields["resigned_at"] = _parse_resigned_at(rec.get("resigned_at"))
        source_user_id = uuid.UUID(rec["source_user_id"])

        dto, created = await users.upsert_by_source_user_id(
            source_user_id=source_user_id,
            fields=fields,
            password_hash=demo_hash,
        )
        result.created += int(created)
        result.updated += int(not created)

        # 이름 규칙 매핑: department 텍스트 → 부서 노드. 실패분은 admin 보정 대상.
        node_id = dept_map.get(rec.get("department") or "")
        if node_id is not None:
            await users.set_department_node(dto.id, department_node_id=node_id)
            result.mapped += 1
        else:
            result.unmapped += 1

    return result
