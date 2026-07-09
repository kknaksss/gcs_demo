# Classification Guide

cloud-file-organizer AI 문서 분류 task(SPEC-007) 실행 규칙이다.
task prompt에 담긴 **Classification Input**을 읽고, 아래 **Output Schema**를 따르는 **JSON object 하나만** 출력한다.
JSON 밖의 설명 문장, 인사, 코드펜스 밖 텍스트를 출력하지 않는다.

> schema 원천: backend `app/services/classification.py`의 `ClassificationOutput`(pydantic)이 validator다.
> 이 문서와 backend 모델은 항상 동기 유지한다 (드리프트 금지 — WORK-004 Internal Interface Contract).

## Classification Input (task prompt로 전달됨)

| Field | Type | 설명 |
|---|---|---|
| `document_id` | int | 제품 document id (참고용 — 출력에 다시 넣지 않는다) |
| `drive_file_id` | text | Drive file id |
| `drive_fingerprint` | object | 현재 Drive composite fingerprint |
| `drive_mirror` | object | `drive_name`, `mime_type`, `drive_modified_time` |
| `read_capability` | enum | `content_read` \| `metadata_only` |
| `analysis_text` | text \| null | 분석용 추출 텍스트 (metadata_only면 null). 저장/복사 금지 |
| `organization_context` | object | `nodes[]` — `{id, type(company/department/team), name, parent_id}` active만 |
| `document_tree_context` | object | `nodes[]` — `{id, type(work/document_type), name, parent_id, organization_node_id, document_type_id}` active만 |
| `document_type_catalog` | array | 전사 공통 문서종류 `{id, name}` |
| `policy_context` | object | 민감 문서 preset 목록 + 판단 규칙 요지 |
| `relation_type_catalog` | array | v1 relation type 4종 |

## Output Schema

아래 필드 외의 key를 추가하지 않는다 (`extra="forbid"` — 추가 key는 validation 실패).

```json
{
  "document_type": "문서종류 이름 (required — catalog 값 우선, 없으면 새 이름 제안)",
  "created_department": "생성부서 이름 후보 또는 null",
  "owning_department": "귀속/관리 부서 이름 (required — organization_context 안의 값, 단일값)",
  "physical_tree_path": {
    "organization_path": ["회사 이름", "부서 이름"],
    "tree_path": ["업무(work) 노드 이름", "문서종류 노드 이름"]
  },
  "related_departments": ["관련 부서 이름"],
  "related_products": ["관련 제품 이름"],
  "summary": "한두 문장 요약 또는 null",
  "sensitivity": "normal | sensitive (required)",
  "policy_preset": "HR_RESTRICTED | CONTRACT_RESTRICTED | FINANCE_RESTRICTED | SECURITY_RESTRICTED | LEGAL_RESTRICTED | null",
  "read_policy": {
    "read_roles": ["admin", "member"],
    "read_departments": ["부서 이름"],
    "read_positions": [],
    "access_logic": "ANY | ALL | PRESET"
  },
  "relation_candidates": [
    {
      "raw_label": "[[다른 문서 이름]]",
      "relation_type": "related | references | supersedes | duplicate_candidate",
      "target_hint": "target 문서의 정확한 파일명 또는 null"
    }
  ],
  "confidence": 0.0,
  "reasons": ["추천 이유"]
}
```

필수(required): `document_type`, `owning_department`, `physical_tree_path`, `sensitivity`, `read_policy`.
`confidence`는 0~1 범위. 나머지는 생략 가능(빈 배열/null 허용).

## 분류 규칙

- `document_type`은 `document_type_catalog`에서 우선 고른다. 정말 맞는 것이 없을 때만
  새 이름을 제안한다 — backend가 카탈로그 미존재 값을 "추가 필요 후보"로 표시한다.
- `owning_department`는 `organization_context`에 있는 이름만 쓴다. 단일값이다 (DEC-005).
- `physical_tree_path.organization_path`는 회사→부서 순서의 조직 노드 이름 배열,
  `tree_path`는 `document_tree_context` 안의 노드 이름 배열이다. backend가 이름을
  노드 id로 resolve해 저장한다 — context에 없는 이름은 admin 보정 대상으로 넘어간다.
- 존재하지 않는 부서/문서종류/제품명을 지어내지 않는다.

## 민감도 / read policy 규칙 (policy_context — DEC-008/017/018)

- 정책 판단은 추천이다. 최종 확정은 관리자 승인 게이트에서 한다.
- HR(인사/급여/평가/개인정보), 계약(NDA/견적/외부업체), 재무(매출/정산/세금/계좌),
  보안(계정/토큰/secret/인프라), 법무(분쟁/약관/고지) 문서는 기본 `sensitive` +
  해당 preset 후보를 제시한다.
- 민감 문서는 더 좁은 read policy를 추천한다 (admin/담당 부서 중심).
- Drive 폴더 위치만 보고 접근권한을 확정하지 않는다.
- 본문에 secret 값이 보이면 값을 요약/복사하지 않는다. 존재 여부와 위험만 `reasons`에 표시한다.

## metadata_only 규칙 (SPEC-007 S-2)

- `read_capability=metadata_only`면 파일명/MIME/수정시각만으로 제한된 후보를 만든다.
- 불확실한 필드는 null/빈 배열로 두고, `confidence`를 낮게 준다.
- 본문이 없다고 임의로 내용을 추정해 `summary`를 지어내지 않는다.

## relation(wikilink) 규칙 (DEC-021)

- `raw_label`은 `[[문서 이름]]` 표기다. `analysis_text`에서 다른 문서를 참조하는
  단서(파일명, "지난주 회의록 참고" 등)가 있을 때만 만든다.
- `relation_type`은 `relation_type_catalog`의 4종만 쓴다.
- target 문서를 특정할 수 없으면 지어내지 말고 `target_hint: null`로 둔다.
  backend가 resolve에 실패하면 unresolved 후보로 저장한다 — 새 문서를 만들지 않는다.

## 경계 (재확인)

- 출력은 후보(candidate)다. 승인값이 아니다.
- 제품 DB, Google Drive API를 직접 호출하지 않는다.
- 원문 텍스트를 저장하거나 workspace 밖으로 복사하지 않는다.
