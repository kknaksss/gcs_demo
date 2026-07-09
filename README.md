# gcs_demo — cloud-file-organizer

Google Drive 문서 수집 → AI 메타데이터 후보(open-kknaks) → 관리자 승인 → 문서 탐색 데모.

**문서 SoT**: `kknaks_profile/products/cloud-file-organizer/` (spec이 계약 기준, 구조는 ARCH-001~003)

## 구조

```text
backend/    FastAPI async API + worker (schema → router → dto → service → dto → repo)
frontend/   Next.js + shadcn/ui (app/login, app/documents, app/admin/*)
ai_worker/  open-kknaks ClaudeWorker — claude CLI로 분류 task 실행 (SPEC-007)
  workspace/           빌드 시점에 이미지에 고정되는 claude 작업 프로젝트
    CLAUDE.md, agent.md      진입 문서
context/    분류 가이드 등 AI 실행 context (빌드 시 workspace/context 로 COPY)
  classification-guide.md   출력 schema/분류 규칙
Dockerfile            API image
Dockerfile.worker     product worker image (Drive sync, job orchestration)
docker-compose.local.yml   local: api / worker / ai-worker / postgres / redis / frontend
docker-compose.prod.yml    prod: api / worker / ai-worker / frontend (.env.prod 주입)
```

## AI worker (로컬 claude)

- `ai_worker/workspace/`(진입 문서)와 레포 루트 `context/`(분류 가이드)는 **빌드 시점에
  `/app/workspace`로 합쳐져 이미지에 COPY**된다. claude는 이 디렉토리 안에서 실행되어
  진입 문서(CLAUDE.md → agent.md → context/classification-guide.md)를 스스로 읽는다.
  루트 `context/`를 바꾸면 `docker compose build ai-worker`로 반영한다 — 마운트가 아니라 이미지 고정이다.
- mac 로컬: darwin claude는 linux 컨테이너에서 못 돌므로 linux/arm64 네이티브 claude 도구
  세트를 마운트한다 (`CLAUDE_TOOLS_HOST_PATH`, 기본값은 ax-graph가 만든
  `~/.cache/axkg-live/.claude-tools` 재사용).
- 서버 배포는 호스트 네이티브 claude 마운트 방식 (docker-compose.prod.yml 주석 참고).

## Layer rule (ARCH-001 §4)

- SQLAlchemy `stmt`/raw query는 `app/repos/` 안에서만 작성한다.
- Google Drive / open-kknaks 호출은 `app/integrations/` 안에서만 한다.
- AI job 상태 원장은 PostgreSQL `ai_queue_jobs`(ARCH-002), Redis는 dispatch 전용.

## 시작

```bash
cp .env.example .env.local   # 값 채우기 (.env.local은 commit 금지)
docker compose -f docker-compose.local.yml up --build
```

backend 단독 개발:

```bash
cd backend
uv venv && uv pip install -r pyproject.toml
uvicorn app.main:app --reload      # API
python -m app.workers.main         # worker
alembic revision --autogenerate    # ARCH-002/003 기준 테이블은 work에서 작성
```

frontend 단독 개발:

```bash
cd frontend && npm install && npm run dev
```
