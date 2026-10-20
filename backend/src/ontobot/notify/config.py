"""발송 게이트웨이 설정(``config/notify.yaml``).

비밀값은 파일에 적지 않는다 — 환경변수 이름(``*_env``)이나 시크릿 참조(``secret_ref``)만 적고, 값은
프로세스 환경 → ``.env`` 순서로 찾는다(``security.secrets`` 와 같은 규칙).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# SMS 발송 테이블의 표·열 이름 — SQL 에 식별자로 들어가므로 형식을 강제한다(바인드할 수 없다).
SQL_IDENT = r"^[A-Za-z_][A-Za-z0-9_]{0,62}(\.[A-Za-z_][A-Za-z0-9_]{0,62})?$"
SQL_IDENT_RE = re.compile(SQL_IDENT)
SqlIdent = Annotated[str, StringConstraints(pattern=SQL_IDENT)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FakeChannel(_Strict):
    """개발·테스트용 — 실제로 보내지 않고 ``dir`` 의 ``<채널>.jsonl`` 에 한 줄씩 남긴다."""

    adapter: Literal["fake"]
    dir: Path


class SmtpChannel(_Strict):
    adapter: Literal["smtp"]
    host: str
    port: int = 25
    starttls: bool = False
    sender: str  # From 주소
    username_env: str | None = None
    password_env: str | None = None
    timeout_s: float = 10


class SmsHttpChannel(_Strict):
    """사내 SMS 게이트웨이 HTTP — ``POST url`` 에 JSON ``{"to", "text", "sender"}``."""

    adapter: Literal["sms_http"]
    url: str
    sender: str
    token_env: str | None = None  # 있으면 Authorization: Bearer <값>
    timeout_s: float = 10


class SmsTableChannel(_Strict):
    """사내 SMS 발송 테이블에 한 행 INSERT(UMS 류). 접속은 ``secret_ref``(env:/file:)."""

    adapter: Literal["sms_table"]
    secret_ref: str
    table: SqlIdent
    to_column: SqlIdent
    text_column: SqlIdent
    sender_column: SqlIdent | None = None
    sender: str | None = None


EmailChannel = Annotated[FakeChannel | SmtpChannel, Field(discriminator="adapter")]
SmsChannel = Annotated[
    FakeChannel | SmsHttpChannel | SmsTableChannel, Field(discriminator="adapter")
]


class Channels(_Strict):
    email: EmailChannel | None = None
    sms: SmsChannel | None = None


class NotifyConfig(_Strict):
    # 실행 한 번에 보낼 수 있는 메일·SMS 수신자 수(채널별). 넘으면 미리보기에서 막는다.
    max_recipients: int = 50
    max_attempts: int = 5
    retry_seconds: int = 60
    channels: Channels = Field(default_factory=Channels)

    def enabled(self, channel: str) -> bool:
        return getattr(self.channels, channel, None) is not None

    def is_fake(self, channel: str) -> bool:
        return isinstance(getattr(self.channels, channel, None), FakeChannel)


def load(path: Path, *, base: Path | None = None) -> NotifyConfig:
    """파일이 없으면 모든 채널이 꺼진 설정이다(앱 안 알림만 된다).

    가짜 채널의 상대 경로 ``dir`` 은 ``base``(기본: 저장소 루트) 기준이다 — 작업 폴더에 따라
    API 와 워커가 다른 곳에 쓰지 않게.
    """
    if not path.exists():
        return NotifyConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = NotifyConfig.model_validate(data)
    root = base or Path(__file__).resolve().parents[3]
    for name in ("email", "sms"):
        ch = getattr(cfg.channels, name)
        if isinstance(ch, FakeChannel) and not ch.dir.is_absolute():
            ch.dir = root / ch.dir
    return cfg
