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

## 로컬 실행 절차 (docker 없이, 2026-07-09 실검증 방식)

```bash
# 0) 인프라만 docker (다른 스택과 포트 충돌 시 compose 포트 조정)
docker compose -f docker-compose.local.yml up -d db redis

# 1) env 로드 — .env.local은 레포 루트라 backend cwd에서는 자동 로드되지 않는다.
#    로컬 직접 실행 시 반드시 shell에서 source로 주입한다.
set -a && source .env.local && set +a

# 2) backend API + product worker (예: 포트 충돌 회피용 18000)
cd backend
.venv/bin/uvicorn app.main:app --port 18000 &
.venv/bin/python -m app.workers.main &          # Drive 폴링 60s + AI job 5s

# 3) ai_worker — 맥에서는 네이티브 claude를 그대로 사용 (docker/claude-tools 불필요)
cd ../ai_worker && ../backend/.venv/bin/python run.py &

# 4) frontend (API 포트에 맞춰)
cd ../frontend && NEXT_PUBLIC_API_BASE_URL=http://localhost:18000 npm run dev -- --port 13000
# ⚠️ backend CORS_ORIGINS에 FE 포트가 포함돼야 한다 (.env.local)
```

로컬 실행 주의사항:

- **ai_worker workspace 자산은 symlink 금지** — claude 샌드박스가 workspace 외부를 가리키는
  symlink 읽기를 차단해 분류가 산문 응답으로 실패한다(2026-07-09 실검증). 로컬에서는
  `context/classification-guide.md`를 `ai_worker/workspace/context/`로 **복사**해서 쓴다
  (gitignore 대상 — SoT는 레포 루트 `context/`, docker 빌드는 COPY로 자동 처리).
- AI job 소비는 **의도적으로 순차 1건 처리**다(로컬 LLM 과부하 방지 — 사용자 확정).
  대량 업로드 시 문서당 ~2분씩 순차 소화되며 유실 없이 밀린다.
- webhook 없이 폴링만으로 동작한다. 실시간이 필요하면 tunnel/홈서버 프록시로
  `GOOGLE_DRIVE_WEBHOOK_URL`을 채우고 admin 화면에서 watch 갱신.
