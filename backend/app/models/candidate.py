"""metadata_candidates / document_relations / relation_candidates — SPEC-003/005/006, ARCH-003 §5.

- metadata_candidates.state는 원장 5개 enum만 (DEC-022). reanalyzing/new_candidate_ready는 파생.
- 문서당 pending 후보 1개(부분 unique), 결과 멱등 (document_id, candidate_fingerprint).
- document_relations는 (source,target,type) 중복 거부. broken은 파생(저장 안 함).
- unresolved relation_candidate로 document row 만들지 않음 (DEC-021).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

CANDIDATE_STATES = ("pending", "stale", "approved", "rejected", "blocked")
READ_CAPABILITIES = ("content_read", "metadata_only")
RELATION_TYPES = ("related", "references", "supersedes", "duplicate_candidate")
RELATION_CANDIDATE_STATES = ("pending", "unresolved", "approved", "removed")


class MetadataCandidate(Base):
    __tablename__ = "metadata_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    read_capability: Mapped[str] = mapped_column(Text, nullable=False)
    candidate_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False)
    candidate_fingerprint: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'stale', 'approved', 'rejected', 'blocked')",
            name="ck_metadata_candidates_state",
        ),
        CheckConstraint(
            "read_capability IN ('content_read', 'metadata_only')",
            name="ck_metadata_candidates_read_capability",
        ),
        # 문서당 pending 후보 1개 (ARCH-003 §5 부분 unique).
        Index(
            "uq_metadata_candidates_one_pending",
            "document_id",
            unique=True,
            postgresql_where=text("state = 'pending'"),
        ),
        # 결과 멱등 (document_id, candidate_fingerprint) — jsonb 동등 비교용 index.
        Index(
            "ix_metadata_candidates_state",
            "state",
        ),
        Index(
            "ix_metadata_candidates_document_created",
            "document_id",
            "created_at",
        ),
    )


class DocumentRelation(Base):
    __tablename__ = "document_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    target_document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "target_document_id",
            "relation_type",
            name="uq_document_relations_source_target_type",
        ),
        CheckConstraint(
            "relation_type IN ('related', 'references', 'supersedes', "
            "'duplicate_candidate')",
            name="ck_document_relations_type",
        ),
    )


class RelationCandidate(Base):
    __tablename__ = "relation_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    raw_label: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, default="unresolved")
    resolved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "suggested_relation_type IN ('related', 'references', 'supersedes', "
            "'duplicate_candidate')",
            name="ck_relation_candidates_suggested_type",
        ),
        CheckConstraint(
            "state IN ('pending', 'unresolved', 'approved', 'removed')",
            name="ck_relation_candidates_state",
        ),
    )
