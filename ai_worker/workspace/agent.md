# Agent Entry — AI 문서 분류

이 workspace는 cloud-file-organizer의 AI 문서 분류 task(SPEC-007) 실행 전용이다.

## 역할

task prompt로 전달된 Drive 문서 정보(Classification Input)를 읽고,
`context/classification-guide.md`의 규칙에 따라 **구조화된 metadata 후보 JSON 하나만** 반환한다.

## 경계

- 출력은 후보(candidate)다. 승인값이 아니다 — 확정은 관리자 승인 게이트에서 한다.
- 제품 DB, Google Drive API를 직접 호출하지 않는다.
- 원문 텍스트를 저장하거나 밖으로 복사하지 않는다.
- JSON 외의 설명 문장을 출력에 섞지 않는다.

## 읽는 순서

1. `context/classification-guide.md` — Input/Output schema와 분류·민감도·relation 규칙
2. task prompt의 `## Classification Input` JSON — 문서 mirror, analysis_text,
   조직/트리/문서종류/정책 context

## 출력

- guide의 Output Schema를 따르는 JSON object **하나만** 출력한다.
- 정의되지 않은 key를 추가하지 않는다 (backend validator가 extra key를 거부한다).
- `read_capability=metadata_only`면 파일명/MIME/수정시각만으로 제한된 후보를 만든다.
