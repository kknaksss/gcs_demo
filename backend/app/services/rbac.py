"""RBAC read 판정 core (SPEC-001 §4 RBAC Rules / Visibility Contract). WORK-001 Phase 4.

`evaluate_read(user, policy)`는 원장 저장 없이 요청 시점 판정 결과(ReadDecision)만
돌려준다 (DEC-016). 화면 필터 적용은 후속 WP(WORK-006)가 이 함수를 호출한다.

판정 순서:
1. Visibility 차단: active=false / resigned_at 존재 → 무조건 불가.
2. admin role → 민감 문서 포함 전체 읽기 가능.
3. department_node_id 없음 → 일반 문서 탐색 제한(불가). admin 보정 대상.
4. read policy를 access_logic(ANY/ALL/PRESET)으로 평가.

department 매칭: 사용자의 department_node_id/team_node_id 중 하나라도 policy의
read_departments 노드 목록에 있으면 매치. seed에서 하위 팀 사용자도
department_node_id를 부모 부서로 들고 있으므로 'department면 하위 팀 포함'이 성립한다.
"""

from __future__ import annotations

from app.dtos.rbac import ReadDecision, ReadPolicy
from app.dtos.user import UserDTO


def _axis_matches(policy: ReadPolicy, user: UserDTO) -> tuple[bool, bool, bool]:
    role_match = bool(policy.read_roles) and user.role in policy.read_roles

    user_nodes = {
        n for n in (user.department_node_id, user.team_node_id) if n is not None
    }
    department_match = bool(policy.read_departments) and bool(
        user_nodes.intersection(policy.read_departments)
    )

    position_match = bool(policy.read_positions) and user.position in policy.read_positions
    return role_match, department_match, position_match


def _apply_logic(
    policy: ReadPolicy,
    role_match: bool,
    department_match: bool,
    position_match: bool,
) -> bool:
    """제약(policy 값이 있는) 축만 대상으로 ANY/ALL/PRESET 평가.

    PRESET은 자체 연산이 아니라 policy 출처 표시다. preset이 read policy 필드로 풀어
    저장되고 v1 preset 기본 logic은 ANY이므로 PRESET은 ANY로 평가한다 (SPEC-001).
    """
    constrained: list[bool] = []
    if policy.read_roles:
        constrained.append(role_match)
    if policy.read_departments:
        constrained.append(department_match)
    if policy.read_positions:
        constrained.append(position_match)

    if not constrained:
        # 어떤 축도 제약하지 않는 문서는 일반 공개 문서로 본다.
        return True

    logic = policy.access_logic
    if logic == "ALL":
        return all(constrained)
    # ANY, PRESET(v1 기본 ANY), 그 외 미지정은 ANY로 처리.
    return any(constrained)


def evaluate_read(user: UserDTO, policy: ReadPolicy) -> ReadDecision:
    role_match, department_match, position_match = _axis_matches(policy, user)

    # 1. Visibility 차단 (계정 상태)
    if not user.active or user.resigned_at is not None:
        return ReadDecision(
            role_match=role_match,
            department_match=department_match,
            position_match=position_match,
            final_readable=False,
            reason="inactive_or_resigned",
        )

    # 2. admin 전체 허용 (민감 문서 포함)
    if user.is_admin:
        return ReadDecision(
            role_match=role_match,
            department_match=department_match,
            position_match=position_match,
            final_readable=True,
            reason="admin_override",
        )

    # 3. 조직 매핑 없음 → 일반 문서 탐색 제한
    if user.department_node_id is None:
        return ReadDecision(
            role_match=role_match,
            department_match=department_match,
            position_match=position_match,
            final_readable=False,
            reason="unmapped_department",
        )

    # 4. read policy 평가
    final_readable = _apply_logic(
        policy, role_match, department_match, position_match
    )
    return ReadDecision(
        role_match=role_match,
        department_match=department_match,
        position_match=position_match,
        final_readable=final_readable,
        reason=policy.access_logic,
    )
