"""RBAC 판정 입출력 DTO (SPEC-001 RBAC Rules / Boolean Vector / Visibility).

- ReadPolicy: 문서의 read policy (documents 테이블 approved/auth 필드에서 온다).
- ReadDecision: 요청 시점 판정 결과/log 전용 boolean vector. 원장에 저장하지 않는다 (DEC-016).
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class ReadPolicy:
    """문서 read policy. read_departments는 조직도 노드 id 목록이다 (DEC-012)."""

    read_roles: tuple[str, ...] = ()
    read_departments: tuple[int, ...] = ()
    read_positions: tuple[str, ...] = ()
    access_logic: str = "ANY"  # ANY | ALL | PRESET
    sensitivity: str = "normal"  # normal | sensitive
    policy_preset: str | None = None

    @staticmethod
    def from_mapping(data: dict) -> "ReadPolicy":
        return ReadPolicy(
            read_roles=tuple(data.get("read_roles") or ()),
            read_departments=tuple(data.get("read_departments") or ()),
            read_positions=tuple(data.get("read_positions") or ()),
            access_logic=data.get("access_logic") or "ANY",
            sensitivity=data.get("sensitivity") or "normal",
            policy_preset=data.get("policy_preset"),
        )


@dataclasses.dataclass(frozen=True)
class ReadDecision:
    role_match: bool
    department_match: bool
    position_match: bool
    final_readable: bool
    # 판정 근거(log/디버그용). 원장 저장 대상 아님.
    reason: str = ""
