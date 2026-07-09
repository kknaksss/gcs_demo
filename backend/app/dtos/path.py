"""ValidatedPath DTO — WORK-002 Internal Interface Contract.

`services/document_tree.validate_active_path()`의 반환 객체. 승인(WORK-005)과
문서 이관이 같은 검증 결과를 소비한다. path array는 노드 id 저장 (SPEC-002).
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class ValidatedPath:
    organization_path: list[int]
    tree_path: list[int]
    display_path: str
    owning_department_node_id: int | None
    owning_department: str | None
