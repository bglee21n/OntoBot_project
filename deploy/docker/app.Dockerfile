# syntax=docker/dockerfile:1.7
# OntoBot 앱 이미지(ADR-0013) — api·worker·migrate 가 명령만 달리해 같은 이미지를 쓴다.
#
#   docker build -f deploy/docker/app.Dockerfile -t ontobot-app:<버전> .
#
# 저장소 레이아웃(/app/backend·/app/config·/app/frontend/dist)을 그대로 둔다. ontobot 은 편집 가능 설치라
# config.py 가 .env·config/ 를 패키지 위치의 parents[3](= /app)에서 찾는다 — 운영에서는 /app/.env 를 쓰지 않고
# 환경변수(compose env_file)와 마운트한 /app/config 를 쓴다.
# 빌드 베이스 이미지는 태그와 다이제스트로 고정한다(운영 제3자 이미지는 deploy/docker/images.lock).

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------- 프론트 빌드(CDN 없음 — 자산은 빌드에 포함)
FROM ${NODE_IMAGE} AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- 앱
FROM ${PYTHON_IMAGE} AS app
ENV PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/backend/.venv
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app/backend
# 의존성 층을 먼저(uv.lock 고정) — 소스만 바뀌면 이 층을 다시 쓰지 않는다.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev

COPY config/ /app/config/
COPY scripts/ /app/scripts/
COPY domain-packs/ /app/domain-packs/
COPY --from=web /app/frontend/dist /app/frontend/dist

ARG ONTOBOT_VERSION=dev
RUN echo "${ONTOBOT_VERSION}" > /app/VERSION \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin ontobot \
    && mkdir -p /app/logs \
    && chown -R ontobot:ontobot /app/logs

ENV PATH=/app/backend/.venv/bin:$PATH \
    ONTOBOT_FRONTEND_DIST=/app/frontend/dist \
    ONTOBOT_LOG_DIR=/app/logs
USER ontobot
EXPOSE 8000
# 재시작 판단은 /healthz 만 본다(/readyz 는 DB·LLM 을 보므로 물리면 멀쩡한 프로세스를 죽인다 — CLAUDE.md).
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=4 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "ontobot.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
