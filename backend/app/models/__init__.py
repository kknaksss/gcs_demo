"""SQLAlchemy models. 테이블 정의는 ARCH-002/ARCH-003을 따른다.

enum 축은 v1에서 text + CHECK 제약으로 저장한다 (ARCH-003 §설계 원칙: 최종 enum
table 분리는 구현/DB spec에서 결정). 조직/트리/문서종류 참조는 stable id FK.
boolean vector 컬럼은 어떤 원장에도 두지 않는다 (DEC-016).
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# metadata 등록을 위해 모든 model 모듈을 import한다 (Alembic autogenerate/target_metadata).
from app.models.ai_queue import AiQueueJob  # noqa: E402,F401
from app.models.candidate import (  # noqa: E402,F401
    DocumentRelation,
    MetadataCandidate,
    RelationCandidate,
)
from app.models.document import (  # noqa: E402,F401
    Document,
    DocumentPathHistory,
    DocumentRelatedDepartment,
)
from app.models.drive_sync import DriveSyncEvent, DriveSyncState  # noqa: E402,F401
from app.models.organization import (  # noqa: E402,F401
    DocumentTreeNode,
    DocumentType,
    OrganizationNode,
)
from app.models.user import User  # noqa: E402,F401

__all__ = [
    "Base",
    "User",
    "OrganizationNode",
    "DocumentTreeNode",
    "DocumentType",
    "Document",
    "DocumentRelatedDepartment",
    "DocumentPathHistory",
    "MetadataCandidate",
    "DocumentRelation",
    "RelationCandidate",
    "DriveSyncState",
    "DriveSyncEvent",
    "AiQueueJob",
]
