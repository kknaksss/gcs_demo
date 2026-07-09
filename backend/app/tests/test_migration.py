"""도메인 스키마 불변식 테스트 — WORK-001 Phase 1 (ARCH-002/003 AC).

conftest가 model metadata로 만든 test DB에 대해 핵심 제약을 검증한다. Alembic
upgrade/downgrade 왕복은 별도 수동 검증(리포트 참조); 여기서는 제약 동작을 본다.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.candidate import MetadataCandidate
from app.models.document import Document
from app.tests.conftest import _Session

_ALL_TABLES = {
    "users",
    "organization_nodes",
    "document_tree_nodes",
    "document_types",
    "documents",
    "document_related_departments",
    "document_path_histories",
    "metadata_candidates",
    "document_relations",
    "relation_candidates",
    "drive_sync_state",
    "drive_sync_events",
    "ai_queue_jobs",
}


async def test_all_domain_tables_exist() -> None:
    async with _Session() as session:
        rows = (
            await session.execute(
                sa.text(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'public'"
                )
            )
        ).scalars().all()
    assert _ALL_TABLES.issubset(set(rows))


async def test_gin_and_partial_unique_indexes_present() -> None:
    async with _Session() as session:
        indexes = (
            await session.execute(
                sa.text("select indexname from pg_indexes where schemaname='public'")
            )
        ).scalars().all()
    assert "ix_documents_read_departments_gin" in indexes
    assert "ix_documents_organization_path_gin" in indexes
    assert "uq_metadata_candidates_one_pending" in indexes


def _document(drive_file_id: str) -> Document:
    return Document(
        source_provider="google_drive",
        drive_file_id=drive_file_id,
        drive_name="doc",
        drive_mime_type="application/pdf",
        drive_state="active",
        drive_fingerprint={"mime_type": "application/pdf", "rev": "1"},
    )


async def test_documents_provider_file_unique() -> None:
    async with _Session() as session:
        session.add(_document("f1"))
        await session.commit()
    async with _Session() as session:
        session.add(_document("f1"))
        try:
            await session.commit()
            raised = False
        except IntegrityError:
            raised = True
    assert raised is True


async def test_only_one_pending_candidate_per_document() -> None:
    async with _Session() as session:
        doc = _document("f-pending")
        session.add(doc)
        await session.flush()
        session.add(
            MetadataCandidate(
                document_id=doc.id,
                state="pending",
                read_capability="metadata_only",
                candidate_metadata={},
                candidate_fingerprint={"rev": "1"},
            )
        )
        await session.commit()
        doc_id = doc.id

    async with _Session() as session:
        session.add(
            MetadataCandidate(
                document_id=doc_id,
                state="pending",
                read_capability="metadata_only",
                candidate_metadata={},
                candidate_fingerprint={"rev": "2"},
            )
        )
        try:
            await session.commit()
            raised = False
        except IntegrityError:
            raised = True
    assert raised is True


async def test_bad_enum_rejected_by_check_constraint() -> None:
    async with _Session() as session:
        bad = _document("f-bad")
        bad.drive_state = "not_a_state"
        session.add(bad)
        try:
            await session.commit()
            raised = False
        except IntegrityError:
            raised = True
    assert raised is True
