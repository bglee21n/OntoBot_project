"""시크릿 참조 해석(TRD 9.3) — DB 에는 참조만, 값은 환경변수·시크릿 파일에서만 읽는다.

참조 형식:

- ``env:PREFIX`` — ``PREFIX_HOST`` · ``PREFIX_PORT`` · ``PREFIX_DB`` · ``PREFIX_USER`` ·
  ``PREFIX_PASSWORD``. 프로세스 환경변수가 먼저, 없으면 설정 파일(``.env``)
- ``file:경로`` — 같은 키(host·port·db·user·password)를 가진 YAML/JSON 파일

접속 정보를 URL 한 줄로 받지 않는다 — 비밀번호의 ``@``·``:``·``/`` 가 URL 구분자로 읽혀 엉뚱한
곳으로 접속한다(config.py 와 같은 이유). 해석한 값은 ``SecretStr`` 로 감싸 로그에 새지 않게 한다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import SecretStr

_PREFIX = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class SecretRefError(Exception):
    """참조 형식이 틀렸거나 값을 찾지 못했다. 메시지에 값은 싣지 않는다."""


@dataclass(frozen=True)
class DbCredentials:
    host: str
    port: int
    database: str
    user: str
    password: SecretStr

    def __repr__(self) -> str:  # 비밀번호가 디버그 출력에 새지 않게
        return f"DbCredentials({self.user}@{self.host}:{self.port}/{self.database})"


def validate_ref(ref: str) -> None:
    """저장 시점 검사 — 형식만 본다(값이 있는지는 접속 시험이 본다)."""
    kind, _, rest = ref.partition(":")
    if kind == "env" and _PREFIX.match(rest):
        return
    if kind == "file" and rest:
        return
    raise SecretRefError("secret_ref 는 'env:PREFIX'(대문자) 또는 'file:경로' 형식이어야 합니다")


def resolve_db(ref: str) -> DbCredentials:
    validate_ref(ref)
    kind, _, rest = ref.partition(":")
    if kind == "env":
        # Windows 서비스 배포는 설정을 .env 에 둔다(ADR-0007). 앱 설정이 .env 를 읽어도 환경변수로
        # 내보내지는 않으므로 여기서도 같은 파일을 본다. 프로세스 환경변수가 먼저다.
        from_file = _env_file_values()
        values = {
            key: os.environ.get(f"{rest}_{key.upper()}") or from_file.get(f"{rest}_{key.upper()}")
            for key in ("host", "port", "db", "user", "password")
        }
        missing = [f"{rest}_{k.upper()}" for k, v in values.items() if v is None and k != "port"]
    else:
        path = Path(rest)
        if not path.is_file():
            raise SecretRefError(f"시크릿 파일이 없습니다: {path}")
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        values = {
            k: (None if loaded.get(k) is None else str(loaded[k]))
            for k in ("host", "port", "db", "user", "password")
        }
        missing = [k for k, v in values.items() if v is None and k != "port"]
    if missing:
        raise SecretRefError(f"접속 정보가 없습니다: {', '.join(missing)}")
    return DbCredentials(
        host=str(values["host"]),
        port=int(values["port"] or 5432),
        database=str(values["db"]),
        user=str(values["user"]),
        password=SecretStr(str(values["password"])),
    )


def _env_file_values() -> dict[str, str | None]:
    from ontobot.config import get_settings

    path = get_settings().model_config.get("env_file")
    if not path or not Path(str(path)).is_file():
        return {}
    return dict(dotenv_values(str(path), encoding="utf-8"))
