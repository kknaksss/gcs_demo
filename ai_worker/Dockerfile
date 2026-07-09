# build context는 레포 루트다 (compose: context ., dockerfile ai_worker/Dockerfile).
# 루트 context/ 를 빌드 시점에 workspace 안으로 COPY해 이미지에 고정하기 위함.
FROM python:3.12-slim

# node install — claude CLI 실행 런타임 (claude binary는 호스트/도구세트 mount로 주입)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# PyPI 패키지 설치 (소스 복사 없음)
RUN pip install --no-cache-dir "open-kknaks==2.0.2"

# 워커 코드 + 분류 작업 workspace — root 로 실행 (mount 한 호스트 도구 권한 회피).
# 진입 문서(CLAUDE.md/agent.md)는 ai_worker/workspace/, 분류 가이드는 레포 루트 context/ 에서
# 가져와 /app/workspace/context 로 합친다. claude 는 이 workspace 안에서 실행되어 진입 문서를
# 스스로 읽는다 — 마운트 아님, 빌드 시점 고정. 가이드 변경은 re-build로 반영.
WORKDIR /app
COPY ai_worker/ .
COPY context/ ./workspace/context/

# claude 실행 프로젝트 = 이미지에 박힌 분류 workspace. run.py 기본값과 동일하나 명시 고정.
ENV WORK_DIR=/app/workspace

CMD ["python3", "run.py"]
