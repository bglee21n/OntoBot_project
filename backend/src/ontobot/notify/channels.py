"""발송 게이트웨이 어댑터 — 메일·SMS 의 네트워크 호출은 여기만 한다(TRD 17.1-4).

어댑터는 메시지 하나를 보내거나 ``ChannelError`` 를 던진다. 재시도·상태 기록은
``delivery`` 의 일이다.
"""

from __future__ import annotations

import asyncio
import json
import smtplib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ontobot.notify.config import (
    SQL_IDENT_RE,
    FakeChannel,
    NotifyConfig,
    SmsHttpChannel,
    SmsTableChannel,
    SmtpChannel,
)
from ontobot.security.secrets import env_value, resolve_db


class ChannelError(Exception):
    """보내지 못했다. 메시지는 기록에 남는다(수신자 원값을 넣지 않는다)."""


@dataclass(frozen=True)
class OutMessage:
    channel: str  # email | sms
    recipient: str
    subject: str | None
    body: str


def _secret(env: str | None) -> str | None:
    if env is None:
        return None
    value = env_value(env)
    if value is None:
        raise ChannelError(f"환경변수 {env} 가 없습니다")
    return value.get_secret_value()


async def _send_fake(cfg: FakeChannel, msg: OutMessage) -> None:
    def write() -> None:
        cfg.dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps({**asdict(msg), "at": datetime.now(UTC).isoformat()}, ensure_ascii=False)
        with (cfg.dir / f"{msg.channel}.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    await asyncio.to_thread(write)


async def _send_smtp(cfg: SmtpChannel, msg: OutMessage) -> None:
    mail = EmailMessage()
    mail["From"] = cfg.sender
    mail["To"] = msg.recipient
    mail["Subject"] = msg.subject or "OntoBot 알림"
    mail.set_content(msg.body)
    user, password = _secret(cfg.username_env), _secret(cfg.password_env)

    def send() -> None:
        with smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout_s) as smtp:
            if cfg.starttls:
                smtp.starttls()
            if user is not None and password is not None:
                smtp.login(user, password)
            smtp.send_message(mail)

    try:
        await asyncio.to_thread(send)
    except (OSError, smtplib.SMTPException) as e:
        raise ChannelError(f"SMTP 실패: {type(e).__name__}") from e


# 테스트가 가짜 전송을 끼운다(LLM 어댑터와 같은 방식).
transport: httpx.AsyncBaseTransport | None = None


async def _send_sms_http(cfg: SmsHttpChannel, msg: OutMessage) -> None:
    headers = {}
    token = _secret(cfg.token_env)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=cfg.timeout_s, transport=transport) as client:
            r = await client.post(
                cfg.url,
                json={"to": msg.recipient, "text": msg.body, "sender": cfg.sender},
                headers=headers,
            )
    except httpx.HTTPError as e:
        raise ChannelError(f"SMS 게이트웨이 연결 실패: {type(e).__name__}") from e
    if r.status_code >= 300:
        raise ChannelError(f"SMS 게이트웨이 응답 {r.status_code}")


_table_engines: dict[str, AsyncEngine] = {}


def _table_engine(ref: str) -> AsyncEngine:
    engine = _table_engines.get(ref)
    if engine is None:
        c = resolve_db(ref)
        engine = create_async_engine(
            URL.create(
                "postgresql+asyncpg",
                username=c.user,
                password=c.password.get_secret_value() or None,
                host=c.host,
                port=c.port,
                database=c.database,
            ),
            pool_size=2,
            max_overflow=0,
        )
        _table_engines[ref] = engine
    return engine


async def _send_sms_table(cfg: SmsTableChannel, msg: OutMessage) -> None:
    # 표·열 이름은 바인드할 수 없으므로 조립 직전에 형식을 다시 확인한다.
    cols = [cfg.to_column, cfg.text_column] + ([cfg.sender_column] if cfg.sender_column else [])
    for ident in [cfg.table, *cols]:
        if not SQL_IDENT_RE.match(ident):
            raise ChannelError(f"발송 테이블 식별자 형식 오류: {ident!r}")
    params: dict[str, Any] = {"p0": msg.recipient, "p1": msg.body}
    if cfg.sender_column:
        params["p2"] = cfg.sender
    names = ", ".join(cols)
    binds = ", ".join(f":p{i}" for i in range(len(cols)))
    try:
        async with _table_engine(cfg.secret_ref).begin() as conn:
            await conn.execute(
                text(f"INSERT INTO {cfg.table} ({names}) VALUES ({binds})"),  # noqa: S608
                params,
            )
    except Exception as e:  # 접속·제약 오류 모두 발송 실패다
        raise ChannelError(f"SMS 발송 테이블 실패: {type(e).__name__}") from e


async def send(cfg: NotifyConfig, msg: OutMessage) -> None:
    channel = getattr(cfg.channels, msg.channel, None)
    if channel is None:
        raise ChannelError(f"{msg.channel} 채널이 설정되지 않았습니다")
    if isinstance(channel, FakeChannel):
        await _send_fake(channel, msg)
    elif isinstance(channel, SmtpChannel):
        await _send_smtp(channel, msg)
    elif isinstance(channel, SmsHttpChannel):
        await _send_sms_http(channel, msg)
    elif isinstance(channel, SmsTableChannel):
        await _send_sms_table(channel, msg)
    else:  # pragma: no cover — 설정 모델이 막는다
        raise ChannelError(f"알 수 없는 어댑터: {channel!r}")
