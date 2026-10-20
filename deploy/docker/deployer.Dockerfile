# syntax=docker/dockerfile:1.7
# OntoBot 배포 콘솔 이미지(ADR-0014) — 화면 + API + ontobotctl + docker CLI(compose).
#
#   docker build -f deploy/docker/deployer.Dockerfile -t ontobot-deployer:<버전> .
#
# 앱(ontobot)을 담지 않는다 — 앱이 깨져도 이 콘솔로 롤백할 수 있어야 한다. 도커 소켓을 쓰므로 root 로 돈다
# (compose.deployer.yaml 의 보안 설명 참고). 빌드 베이스는 태그와 다이제스트로 고정한다.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:c3de60bf2f9dd0ac6370e6117950ff62d6e339527e7472301c9c78a017978392
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.7@sha256:240fb85ab0f263ef12f492d8476aa3a2e4e1e333f7d67fbdd923d00a506a516a
ARG DOCKER_CLI_IMAGE=docker:28-cli@sha256:625d9431a9f54c5a2bc90f24f0e1c3d55b1349fd857dd85035f98c2c9acbdd4d

FROM ${UV_IMAGE} AS uv
FROM ${DOCKER_CLI_IMAGE} AS dockercli

# ---------------------------------------------------------------- 화면(frontend/src/deploy)
FROM ${NODE_IMAGE} AS web
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build:deploy

# ---------------------------------------------------------------- 콘솔
FROM ${PYTHON_IMAGE}
ENV PYTHONUTF8=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/srv/deployer/.venv
# ontobotctl 이 쓰는 것: openssl(서명 확인)·flock(util-linux)·sha256sum·tar(coreutils).
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

WORKDIR /srv/deployer
COPY deployer/pyproject.toml deployer/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY deployer/src ./src
RUN uv sync --frozen --no-dev

COPY --chmod=755 deploy/docker/ontobotctl /usr/local/bin/ontobotctl
COPY --from=web /app/frontend/dist-deploy /srv/ui
RUN mv /srv/ui/deploy.html /srv/ui/index.html

ARG ONTOBOT_VERSION=dev
RUN echo "${ONTOBOT_VERSION}" > /srv/deployer/VERSION
ENV PATH=/srv/deployer/.venv/bin:$PATH \
    ONTOBOT_DEPLOYER_UI_DIR=/srv/ui
EXPOSE 8100
HEALTHCHECK --interval=10s --timeout=4s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=3).status == 200 else 1)"]
CMD ["python", "-m", "ontobot_deployer"]
