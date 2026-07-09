"""relation 후보 처리 API 테스트 — WORK-005 Phase 2 (SPEC-005 U-6, DEC-021).

resolve(target 지정)/hold/remove/rematch. target 없는 확정 시도는
RELATION_TARGET_REQUIRED, 어떤 경로로도 document row 자동 생성 없음.
"""

from __future__ import annotations

import sqlalchemy as sa
from httpx import AsyncClient

from app.models.candidate import RelationCandidate
from app.models.document import Document
from app.tests.conftest import DEMO_PASSWORD, _Session


def _pick(records: list[dict], *, active: bool, role: str) -> dict:
    for rec in records:
        if rec["active"] is active and rec["role"] == role:
            return rec
    raise AssertionError("no matching seed record")


async def _admin_headers(client: AsyncClient, seeded: list[dict]) -> dict[str, str]:
    admin = _pick(seeded, active=True, role="admin")
    login = await client.post(
        "/auth/login", json={"email": admin["email"], "password": DEMO_PASSWORD}
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _make_document(
    *, drive_file_id: str, drive_name: str, drive_state: str = "active"
) -> int:
    async with _Session() as session:
        row = Document(
            source_provider="google_drive",
            drive_file_id=drive_file_id,
            drive_name=drive_name,
            drive_mime_type="application/pdf",
            drive_state=drive_state,
            drive_fingerprint={"drive_file_id": drive_file_id},
        )
        session.add(row)
        await session.commit()
        return row.id


async def _make_relation(
    *,
    source_document_id: int,
    raw_label: str = "[[구매 품의 규정]]",
    state: str = "unresolved",
    target_document_id: int | None = None,
) -> int:
    async with _Session() as session:
        row = RelationCandidate(
            source_document_id=source_document_id,
            raw_label=raw_label,
            suggested_relation_type="related",
            target_document_id=target_document_id,
            state=state,
        )
        session.add(row)
        await session.commit()
        return row.id


async def test_resolve_requires_target(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    source = await _make_document(drive_file_id="f-rc-1", drive_name="원본.gdoc")
    rel_id = await _make_relation(source_document_id=source)

    resp = await client.post(
        f"/admin/relation-candidates/{rel_id}/resolve",
        json={"target_document_id": None},
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error_code"] == "RELATION_TARGET_REQUIRED"

    # target 없는 후보 때문에 document row가 생기지 않는다 (DEC-021)
    async with _Session() as session:
        count = await session.scalar(
            sa.select(sa.func.count()).select_from(Document)
        )
    assert count == 1


async def test_resolve_sets_target_and_pending(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    source = await _make_document(drive_file_id="f-rc-2", drive_name="원본.gdoc")
    target = await _make_document(
        drive_file_id="f-rc-3", drive_name="구매 품의 규정.gdoc"
    )
    rel_id = await _make_relation(source_document_id=source)

    resp = await client.post(
        f"/admin/relation-candidates/{rel_id}/resolve",
        json={"target_document_id": target},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "pending"
    assert body["target_document_id"] == target
    assert body["target_drive_name"] == "구매 품의 규정.gdoc"

    # 삭제 상태 문서는 target 불가
    trashed = await _make_document(
        drive_file_id="f-rc-4", drive_name="휴지통 문서.gdoc", drive_state="trashed"
    )
    rel2 = await _make_relation(source_document_id=source, raw_label="[[다른 링크]]")
    bad = await client.post(
        f"/admin/relation-candidates/{rel2}/resolve",
        json={"target_document_id": trashed},
        headers=headers,
    )
    assert bad.status_code == 409
    assert bad.json()["detail"]["error_code"] == "DOCUMENT_UNAVAILABLE"

    # 자기 자신도 target 불가
    self_ref = await client.post(
        f"/admin/relation-candidates/{rel2}/resolve",
        json={"target_document_id": source},
        headers=headers,
    )
    assert self_ref.status_code == 409


async def test_hold_and_remove(client: AsyncClient, seeded: list[dict]) -> None:
    headers = await _admin_headers(client, seeded)
    source = await _make_document(drive_file_id="f-rc-5", drive_name="원본.gdoc")
    hold_id = await _make_relation(source_document_id=source)
    remove_id = await _make_relation(
        source_document_id=source, raw_label="[[제거 대상]]"
    )

    held = await client.post(
        f"/admin/relation-candidates/{hold_id}/hold", headers=headers
    )
    assert held.status_code == 200
    assert held.json()["state"] == "unresolved"

    removed = await client.post(
        f"/admin/relation-candidates/{remove_id}/remove", headers=headers
    )
    assert removed.status_code == 200
    assert removed.json()["state"] == "removed"

    # 종결된 후보 재처리는 불가
    again = await client.post(
        f"/admin/relation-candidates/{remove_id}/hold", headers=headers
    )
    assert again.status_code == 409
    assert again.json()["detail"]["error_code"] == "CANDIDATE_NOT_PENDING"

    missing = await client.post(
        "/admin/relation-candidates/99999/hold", headers=headers
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["error_code"] == "CANDIDATE_NOT_FOUND"


async def test_rematch_suggests_without_state_change(
    client: AsyncClient, seeded: list[dict]
) -> None:
    headers = await _admin_headers(client, seeded)
    source = await _make_document(drive_file_id="f-rc-6", drive_name="원본.gdoc")
    rel_id = await _make_relation(source_document_id=source)

    # 아직 target 문서가 없음 — 제안 없음
    empty = await client.post(
        f"/admin/relation-candidates/{rel_id}/rematch", headers=headers
    )
    assert empty.status_code == 200
    assert empty.json()["suggested_target_document_id"] is None

    # 신규 수집 문서가 생기면 drive_name 재검색으로 제안 (S-5/DEC-021)
    target = await _make_document(
        drive_file_id="f-rc-7", drive_name="구매 품의 규정"
    )
    matched = await client.post(
        f"/admin/relation-candidates/{rel_id}/rematch", headers=headers
    )
    assert matched.status_code == 200
    body = matched.json()
    assert body["suggested_target_document_id"] == target
    assert body["suggested_target_drive_name"] == "구매 품의 규정"
    # 재매칭은 제안만 — 확정은 resolve로만 (상태 변경 없음)
    assert body["candidate"]["state"] == "unresolved"
    assert body["candidate"]["target_document_id"] is None
