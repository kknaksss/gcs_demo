"""users — SPEC-001 Product User Model / ARCH-003 §3.

Mediness `public.users`를 seed source로 하는 제품 내부 user 원장.
- int identity PK, 원본 uuid는 `source_user_id`로 보존(unique, 멱등 upsert 기준).
- 권한 판정 소속 기준은 조직도 노드 매핑(`department_node_id`/`team_node_id`) (DEC-012).
- boolean vector(role_match 등) 컬럼은 두지 않는다 (DEC-016).
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    source_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[str] = mapped_column(Text, nullable=False)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)

    department_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("organization_nodes.id", ondelete="SET NULL"), nullable=True
    )
    team_node_id: Mapped[int | None] = mapped_column(
        ForeignKey("organization_nodes.id", ondelete="SET NULL"), nullable=True
    )

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    employment_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    resigned_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 데모 로그인용 password hash (SPEC-001 Login Boundary: 발급 상세는 구현 spec 결정).
    # 원본 Mediness credential은 저장하지 않는다 — 데모 공통 password를 seed 시 발급.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    seeded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = ()

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
