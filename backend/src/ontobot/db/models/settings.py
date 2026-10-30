"""관리자가 화면에서 바꾸는 운영 설정(키 → 값). 정의·데이터가 아니다 — 바꾸면 감사에 남긴다."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz


class AppSetting(IdMixin, Base):
    """행이 없으면 코드의 기본값을 쓴다(예: 객체 탐색기 확장 한도, ``explorer.limits``)."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[Any] = mapped_column(JSONB)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = mapped_column(
        tz(), server_default=func.now(), onupdate=func.now()
    )
