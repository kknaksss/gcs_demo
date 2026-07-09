"""documents / document_related_departments / document_path_histories — SPEC-003, ARCH-003 §4.

- Drive mirror 필드와 approved metadata 필드를 성격상 분리(같은 row) (SPEC-003).
- read policy 필드(read_roles/read_departments/read_positions/access_logic)는 RBAC 판정 입력.
- boolean vector 저장 금지, Drive 원문/본문 컬럼 없음 (DEC-016/019).
- 삭제는 drive_state soft delete뿐 (DEC-011).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

DRIVE_STATES = ("active", "trashed", "removed", "out_of_scope")
ACCESS_LOGICS = ("ANY", "ALL", "PRESET")
SENSITIVITIES = ("normal", "sensitive")
SOURCE_PROVIDERS = ("google_drive",)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Drive mirror ---
    source_provider: Mapped[str] = mapped_column(Text, nullable=False)
    drive_file_id: Mapped[str] = mapped_column(Text, nullable=False)
    drive_name: Mapped[str] = mapped_column(Text, nullable=False)
    drive_web_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive_mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    drive_state: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    drive_modified_time: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    drive_fingerprint: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # --- approved metadata ---
    document_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_types.id", ondelete="RESTRICT"), nullable=True
    )
    created_department_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("organization_nodes.id", ondelete="RESTRICT"), nullable=True
    )
    owning_department_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("organization_nodes.id", ondelete="RESTRICT"), nullable=True
    )
    organization_path: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), nullable=True
    )
    tree_path: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
    related_products: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text), nullable=True
    )

    # --- approved / auth (RBAC read policy) ---
    read_roles: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    read_departments: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), nullable=True
    )
    read_positions: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    access_logic: Mapped[str] = mapped_column(Text, nullable=False, default="ANY")
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    policy_preset: Mapped[str | None] = mapped_column(Text, nullable=True)

    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

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
        UniqueConstraint(
            "source_provider", "drive_file_id", name="uq_documents_provider_file"
        ),
        CheckConstraint(
            "source_provider IN ('google_drive')",
            name="ck_documents_source_provider",
        ),
        CheckConstraint(
            "drive_state IN ('active', 'trashed', 'removed', 'out_of_scope')",
            name="ck_documents_drive_state",
        ),
        CheckConstraint(
            "access_logic IN ('ANY', 'ALL', 'PRESET')",
            name="ck_documents_access_logic",
        ),
        CheckConstraint(
            "sensitivity IN ('normal', 'sensitive')",
            name="ck_documents_sensitivity",
        ),
        # ARCH-003 §4 index: drive_state, owning_department, path/read_departments GIN.
        Index("ix_documents_drive_state", "drive_state"),
        Index("ix_documents_owning_department", "owning_department_node_id"),
        Index(
            "ix_documents_organization_path_gin",
            "organization_path",
            postgresql_using="gin",
        ),
        Index("ix_documents_tree_path_gin", "tree_path", postgresql_using="gin"),
        Index(
            "ix_documents_read_departments_gin",
            "read_departments",
            postgresql_using="gin",
        ),
    )


class DocumentRelatedDepartment(Base):
    """SPEC-003/006, DEC-005 — 부서 화면 '관련 문서' 역방향 index. 읽기 권한 부여 안 함."""

    __tablename__ = "document_related_departments"

    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    organization_node_id: Mapped[int] = mapped_column(
        ForeignKey("organization_nodes.id", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentPathHistory(Base):
    """SPEC-002, DEC-015 — append-only. UPDATE/DELETE 금지(운영 규칙)."""

    __tablename__ = "document_path_histories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    previous_path: Mapped[dict] = mapped_column(JSONB, nullable=False)
    new_path: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
