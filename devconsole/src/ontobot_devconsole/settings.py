"""설정 — 저장소 루트는 패키지 위치에서 찾는다(devconsole/src/ontobot_devconsole → 세 단계 위)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LOOPBACK = ("127.0.0.1", "localhost", "::1")


def read_env_file(path: Path) -> dict[str, str]:
    """``.env`` 를 글자 그대로 읽는다(셸로 실행하지 않는다). 값은 화면에 내지 않는다."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip()
    return out


@dataclass(frozen=True)
class Settings:
    root: Path
    host: str = "127.0.0.1"
    port: int = 8200
    ui_dir: Path | None = None

    @property
    def data(self) -> Path:
        """콘솔 기록(작업·서비스 로그) — 저장소 안이지만 git 에는 넣지 않는다(.gitignore)."""
        return self.root / ".devconsole"

    @property
    def bundles(self) -> Path:
        return self.root / "dist" / "bundle"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"

    def signing_key(self) -> Path | None:
        """서명 개인키 경로(``.env`` 의 ``ONTOBOT_SIGNING_KEY``).

        내용은 읽지 않는다 — 스크립트에 경로만 넘긴다.
        """
        raw = os.environ.get("ONTOBOT_SIGNING_KEY") or read_env_file(self.env_file).get(
            "ONTOBOT_SIGNING_KEY"
        )
        return Path(raw) if raw else None

    def signing_pub(self) -> Path | None:
        key = self.signing_key()
        return key.with_suffix(".pub") if key else None

    @classmethod
    def from_env(cls) -> Settings:
        root = Path(os.environ.get("ONTOBOT_DEVCONSOLE_ROOT", str(REPO_ROOT)))
        ui = root / "frontend" / "dist-devconsole"
        return cls(
            root=root,
            host=os.environ.get("ONTOBOT_DEVCONSOLE_HOST", "127.0.0.1"),
            port=int(os.environ.get("ONTOBOT_DEVCONSOLE_PORT", "8200")),
            ui_dir=ui if ui.is_dir() else None,
        )
