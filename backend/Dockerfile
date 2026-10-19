# 백엔드 이미지. 가상환경은 uv 가 만든다(uv.lock 그대로 — 개발 환경과 같은 버전).
# 폐쇄망 설치는 deploy/offline 번들(이미지 tar)을 쓴다 — 실행 중 외부 네트워크를 호출하지 않는다.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

EXPOSE 8000
CMD ["uvicorn", "ontobot.main:app", "--host", "0.0.0.0", "--port", "8000"]
