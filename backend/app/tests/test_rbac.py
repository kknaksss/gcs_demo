"""RBAC 판정 core 단위 테스트 — SPEC-001 §6 Acceptance Criteria (Phase 4).

evaluate_read는 DB 없이 판정 결과/log(boolean vector)만 돌려준다 (DEC-016).
"""

from __future__ import annotations

import datetime as dt
import uuid

from app.dtos.rbac import ReadDecision, ReadPolicy
from app.dtos.user import UserDTO
from app.services.rbac import evaluate_read


def _user(
    *,
    role: str = "member",
    position: str = "staff",
    department_node_id: int | None = 10,
    team_node_id: int | None = None,
    active: bool = True,
    resigned_at: dt.datetime | None = None,
) -> UserDTO:
    return UserDTO(
        id=1,
        source_user_id=uuid.uuid4(),
        email="u@example.com",
        name="u",
        role=role,
        position=position,
        department="be",
        department_node_id=department_node_id,
        team_node_id=team_node_id,
        active=active,
        employment_type=None,
        resigned_at=resigned_at,
    )


def test_admin_reads_everything_including_sensitive() -> None:
    user = _user(role="admin")
    policy = ReadPolicy(
        read_roles=("member",),
        read_departments=(999,),
        access_logic="ALL",
        sensitivity="sensitive",
        policy_preset="board_only",
    )
    decision = evaluate_read(user, policy)
    assert decision.final_readable is True
    assert decision.reason == "admin_override"


def test_inactive_user_cannot_read() -> None:
    user = _user(active=False)
    policy = ReadPolicy(read_roles=("member",))
    assert evaluate_read(user, policy).final_readable is False


def test_resigned_user_cannot_read() -> None:
    user = _user(resigned_at=dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc))
    assert evaluate_read(user, policy=ReadPolicy()).final_readable is False


def test_unmapped_department_restricted() -> None:
    user = _user(department_node_id=None)
    # role은 맞지만 조직 매핑이 없으면 일반 문서 탐색 제한.
    policy = ReadPolicy(read_roles=("member",))
    decision = evaluate_read(user, policy)
    assert decision.final_readable is False
    assert decision.reason == "unmapped_department"


def test_public_document_readable_when_no_constraints() -> None:
    user = _user()
    assert evaluate_read(user, ReadPolicy()).final_readable is True


def test_any_logic_one_axis_match() -> None:
    user = _user(role="member", position="ceo", department_node_id=10)
    policy = ReadPolicy(
        read_roles=("plan",),  # 불일치
        read_positions=("ceo",),  # 일치
        access_logic="ANY",
    )
    decision = evaluate_read(user, policy)
    assert decision.role_match is False
    assert decision.position_match is True
    assert decision.final_readable is True


def test_all_logic_requires_every_constrained_axis() -> None:
    user = _user(role="member", position="staff", department_node_id=10)
    policy = ReadPolicy(
        read_roles=("member",),
        read_departments=(10,),
        read_positions=("ceo",),  # 불일치
        access_logic="ALL",
    )
    assert evaluate_read(user, policy).final_readable is False

    policy_ok = ReadPolicy(
        read_roles=("member",),
        read_departments=(10,),
        access_logic="ALL",
    )
    assert evaluate_read(user, policy_ok).final_readable is True


def test_preset_evaluates_as_any_over_unpacked_fields() -> None:
    user = _user(role="member", department_node_id=10)
    policy = ReadPolicy(
        read_roles=("plan",),
        read_departments=(10,),  # 일치
        access_logic="PRESET",
        policy_preset="team_scope",
    )
    assert evaluate_read(user, policy).final_readable is True


def test_department_match_includes_subteam_via_department_node() -> None:
    # 하위 팀 사용자는 department_node_id로 부모 부서를 들고 있으므로,
    # policy가 부서 노드(10)를 지정하면 매치된다.
    user = _user(department_node_id=10, team_node_id=25)
    policy = ReadPolicy(read_departments=(10,), access_logic="ANY")
    assert evaluate_read(user, policy).department_match is True

    # policy가 팀 노드(25)를 지정하면 팀 축으로 매치된다.
    policy_team = ReadPolicy(read_departments=(25,), access_logic="ANY")
    assert evaluate_read(user, policy_team).department_match is True


def test_returns_read_decision_type() -> None:
    assert isinstance(evaluate_read(_user(), ReadPolicy()), ReadDecision)
