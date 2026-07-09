"""User 내부 DTO (ARCH-001 §4 dto layer). service/repo 전달 객체.

RBAC 판정과 인증에 필요한 사용자 속성을 담는다. `password_hash`는 내부 인증 검증
전용이며 어떤 응답 schema로도 노출하지 않는다. boolean vector는 여기 담지 않는다
(요청 시점 판정 결과는 rbac ReadDecision에서 다룬다).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid


@dataclasses.dataclass(frozen=True)
class UserDTO:
    id: int
    source_user_id: uuid.UUID
    email: str
    name: str
    role: str
    position: str
    department: str | None
    department_node_id: int | None
    team_node_id: int | None
    active: bool
    employment_type: str | None
    resigned_at: dt.datetime | None
    password_hash: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def can_browse(self) -> bool:
        """Visibility Contract 기본 전제: 활성·비퇴사여야 문서 탐색이 가능하다."""
        return self.active and self.resigned_at is None
