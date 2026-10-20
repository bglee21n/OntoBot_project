"""설정 — 환경변수(compose.deployer.yaml 이 넘긴다)."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field
from pathlib import Path


def _cidrs(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    return [ipaddress.ip_network(c.strip(), strict=False) for c in raw.split(",") if c.strip()]


@dataclass(frozen=True)
class Settings:
    home: Path
    drop_dir: Path | None
    ctl: str = "ontobotctl"
    ui_dir: Path | None = None
    allow_cidrs: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = field(default_factory=list)
    max_upload_mb: int = 30000
    cookie_secure: bool = False
    session_minutes: int = 60
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "ontobot_db"
    db_user: str = "ontobot"
    db_password: str = ""

    @property
    def data(self) -> Path:
        """콘솔 자신의 기록(작업·번들 점검 결과).

        번들 폴더 안에는 쓰지 않는다(서명 밖 파일이 된다).
        """
        return self.home / "deployer"

    @property
    def bundles(self) -> Path:
        return self.home / "bundles"

    @property
    def incoming(self) -> Path:
        return self.home / "deployer" / "incoming"

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        drop = e.get("ONTOBOT_DEPLOYER_DROP_DIR")
        ui = e.get("ONTOBOT_DEPLOYER_UI_DIR", "/srv/ui")
        return cls(
            home=Path(e.get("ONTOBOT_HOME", "/opt/ontobot")),
            drop_dir=Path(drop) if drop else None,
            ctl=e.get("ONTOBOT_DEPLOYER_CTL", "ontobotctl"),
            ui_dir=Path(ui) if ui and Path(ui).is_dir() else None,
            allow_cidrs=_cidrs(e.get("ONTOBOT_DEPLOYER_ALLOW_CIDRS", "")),
            max_upload_mb=int(e.get("ONTOBOT_DEPLOYER_MAX_UPLOAD_MB", "30000")),
            cookie_secure=e.get("ONTOBOT_DEPLOYER_COOKIE_SECURE", "false").lower() == "true",
            session_minutes=int(e.get("ONTOBOT_DEPLOYER_SESSION_MINUTES", "60")),
            db_host=e.get("ONTOBOT_DB_HOST", "postgres"),
            db_port=int(e.get("ONTOBOT_DB_PORT", "5432")),
            db_name=e.get("ONTOBOT_DB_NAME", "ontobot_db"),
            db_user=e.get("ONTOBOT_DB_USER", "ontobot"),
            db_password=e.get("ONTOBOT_DB_PASSWORD", ""),
        )
