"""로그(TRD 9.3·15절) — JSON 한 줄, 요청 ID, 마스킹, 크기 회전.

- 한 줄 = 한 사건(JSON). 폐쇄망 운영에서 수집기 없이도 ``Select-String``·``jq`` 로 읽힌다.
- 요청 ID: API 미들웨어가 요청마다 정하고(들어온 ``X-Request-ID`` 가 안전한 형식이면 그대로) 응답
  헤더로 돌려준다. 같은 요청의 모든 로그 줄에 붙는다(``contextvars`` — 비동기 작업에도 따라간다).
- 마스킹: 메시지·인자·예외 문자열을 ``security.pii.mask_text`` 로 가린다. 로그에 PII 원값을 남기지
  않는다는 규칙을 호출부마다 지키게 두면 언젠가 한 곳이 빠진다.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import re
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from ontobot.security.pii import mask_text

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def new_request_id(incoming: str | None) -> str:
    """들어온 ID 가 안전한 형식이면 이어 쓰고(프록시·클라이언트와 대조), 아니면 새로 만든다."""
    return incoming if incoming and _SAFE_ID.match(incoming) else uuid.uuid4().hex


class MaskFilter(logging.Filter):
    """메시지를 미리 만들어 가린다. 인자(args)에 PII 가 실려 와도 새지 않는다."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_text(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = mask_text(record.exc_text)
        record.request_id = request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        rid = getattr(record, "request_id", None)
        if rid:
            out["request_id"] = rid
        if record.exc_text:
            out["exc"] = record.exc_text
        return json.dumps(out, ensure_ascii=False)


def setup(
    *,
    level: str = "INFO",
    log_dir: Path | None = None,
    file_name: str = "ontobot.log",
    max_bytes: int = 20 * 1024 * 1024,
    backups: int = 10,
) -> None:
    """루트 로거를 JSON + 마스킹으로 다시 꾸민다(여러 번 불러도 처리기가 늘지 않는다)."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                log_dir / file_name, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
            )
        )
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        h.setFormatter(JsonFormatter())
        h.addFilter(MaskFilter())
        root.addHandler(h)
    root.setLevel(level.upper())
    # uvicorn 은 자기 처리기를 단다 — 루트로 올려 같은 형식으로 남긴다.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
