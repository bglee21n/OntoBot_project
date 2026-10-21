"""``python -m ontobot_devconsole`` — 개발 콘솔 실행(127.0.0.1 전용)."""

from __future__ import annotations

import sys

import uvicorn

from ontobot_devconsole.api import create_app
from ontobot_devconsole.settings import LOOPBACK, Settings


def main() -> None:
    s = Settings.from_env()
    if s.host not in LOOPBACK:
        # 로그인이 없는 개발 도구다 — 바깥 주소로 열면 같은 망의 누구나 이 PC 에서 명령을 돌린다.
        sys.exit(f"개발 콘솔은 127.0.0.1 에서만 엽니다(지금 ONTOBOT_DEVCONSOLE_HOST={s.host})")
    if s.ui_dir is None:
        print("화면 빌드가 없습니다 — cd frontend && npm run build:devconsole (API 만 뜹니다)")
    print(f"개발 콘솔: http://127.0.0.1:{s.port}")
    uvicorn.run(create_app(s), host=s.host, port=s.port, server_header=False, log_level="warning")


if __name__ == "__main__":
    main()
