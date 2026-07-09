"""민감 문서 preset 정의 상수 — WORK-005 (SPEC-005 U-5, DEC-018).

출처: 전역 policy.md가 SoT다 (kknaks_profile `context/policy.md`, DEC-017 —
민감 문서 후보 유형별 read policy 추천 기준). 이 모듈은 그 기준을 backend
상수로 옮긴 것이며, preset 목록/기본 read policy가 바뀌면 policy.md를 먼저
갱신하고 여기에 동기화한다.

- `PRESET`은 자체 판정 연산이 아니라 policy 출처 표시다 (SPEC-001 RBAC Rules).
  승인 시 preset을 read policy 필드로 풀어 저장하고(DEC-018), 판정은 풀어
  저장된 필드를 preset 기본 logic(v1은 ANY)으로 평가한다.
- department 힌트는 조직도 노드 "이름" 후보다. 승인 시점에 active 조직 노드와
  이름이 일치하는 것만 노드 id로 풀어 저장한다 (DEC-012 — id 저장).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyPresetDefinition:
    name: str
    # 풀어 저장할 read_roles — 모든 preset은 admin 중심 (policy.md 기본 처리).
    read_roles: tuple[str, ...] = ("admin",)
    # 조직도에서 이름으로 resolve할 관리 부서 후보 (없으면 admin only).
    department_name_hints: tuple[str, ...] = ()
    read_positions: tuple[str, ...] = ()
    # v1 preset 기본 logic — 풀어 저장된 필드를 ANY로 평가 (SPEC-001).
    logic: str = "ANY"


POLICY_PRESETS: dict[str, PolicyPresetDefinition] = {
    # 인사: 근로계약/평가/급여/개인정보 — HR/admin 중심 (policy.md 민감 문서 후보 표)
    "HR_RESTRICTED": PolicyPresetDefinition(
        name="HR_RESTRICTED",
        department_name_hints=("인사", "HR", "피플", "People"),
    ),
    # 계약: 계약서/견적서/NDA — 관리자/관련 관리 부서 중심
    "CONTRACT_RESTRICTED": PolicyPresetDefinition(
        name="CONTRACT_RESTRICTED",
        department_name_hints=("법무", "Legal", "사업기획"),
    ),
    # 재무: 매출/비용/정산/세금 — admin/재무 담당 중심
    "FINANCE_RESTRICTED": PolicyPresetDefinition(
        name="FINANCE_RESTRICTED",
        department_name_hints=("재무", "회계", "Finance"),
    ),
    # 보안: 계정/토큰/인프라 secret — admin/기술 책임자 중심
    "SECURITY_RESTRICTED": PolicyPresetDefinition(
        name="SECURITY_RESTRICTED",
        department_name_hints=("보안", "Security", "인프라"),
    ),
    # 법무: 분쟁/고지/약관 — admin/법무 또는 경영진 중심
    "LEGAL_RESTRICTED": PolicyPresetDefinition(
        name="LEGAL_RESTRICTED",
        department_name_hints=("법무", "Legal", "경영지원"),
    ),
}

POLICY_PRESET_NAMES: tuple[str, ...] = tuple(POLICY_PRESETS)
