"""organization_nodes / document_tree_nodes / document_types — SPEC-002/005, ARCH-003 §3.

- 조직/트리/문서종류 참조는 name이 아니라 stable id (DEC-004/007/012).
- 계층 check: company root, department<company, team<department (SPEC-002 Validation).
- hard delete 금지, inactive 전환만 (DEC-013) — 모델 레벨에선 status enum으로 표현.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base

ORG_NODE_TYPES = ("company", "department", "team")
NODE_STATUSES = ("active", "inactive")
TREE_NODE_TYPES = ("work", "document_type")


class OrganizationNode(Base):
    __tablename__ = "organization_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("organization_nodes.id", ondelete="RESTRICT"), nullable=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
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
            "type IN ('company', 'department', 'team')",
            name="ck_organization_nodes_type",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_organization_nodes_status",
        ),
        # 계층: company는 parent 없음, department/team은 parent 필수 (SPEC-002 Validation).
        CheckConstraint(
            "(type = 'company' AND parent_id IS NULL) OR "
            "(type IN ('department', 'team') AND parent_id IS NOT NULL)",
            name="ck_organization_nodes_hierarchy_parent",
        ),
    )


class DocumentType(Base):
    __tablename__ = "document_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
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


class DocumentTreeNode(Base):
    __tablename__ = "document_tree_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_node_id: Mapped[int] = mapped_column(
        ForeignKey("organization_nodes.id", ondelete="RESTRICT"), nullable=False
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_tree_nodes.id", ondelete="RESTRICT"), nullable=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    document_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("document_types.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
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
            "type IN ('work', 'document_type')",
            name="ck_document_tree_nodes_type",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_document_tree_nodes_status",
        ),
        # type=document_type 노드만 카탈로그(document_type_id)를 참조한다 (DEC-007).
        CheckConstraint(
            "(type = 'document_type' AND document_type_id IS NOT NULL) OR "
            "(type = 'work' AND document_type_id IS NULL)",
            name="ck_document_tree_nodes_type_ref",
        ),
    )
