"""``python -m ontobot_deployer`` — 배포 콘솔 실행(컨테이너 진입점)."""

from __future__ import annotations

import os

import uvicorn

from ontobot_deployer.api import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.environ.get("ONTOBOT_DEPLOYER_LISTEN", "0.0.0.0"),  # noqa: S104 — 컨테이너 안
        port=int(os.environ.get("ONTOBOT_DEPLOYER_LISTEN_PORT", "8100")),
        server_header=False,
    )


if __name__ == "__main__":
    main()
