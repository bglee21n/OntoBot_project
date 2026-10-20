"""보안 — 사용자·역할·타입 권한·세션·감사 로그(TRD 6.6, 9절)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz


class AppUser(IdMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (CheckConstraint("status IN ('active','disabled')", name="status"),)

    # OIDC 사용자는 oidc_sub, 로컬 계정은 username + password_hash 를 가진다.
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    # Action 알림(메일·SMS) 연락처 — PII. 화면·로그·감사에서는 마스킹한다.
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))


class Role(IdMixin, Base):
    __tablename__ = "role"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str | None] = mapped_column(String(255))


class UserRole(Base):
    __tablename__ = "user_role"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("role.id", ondelete="CASCADE"), primary_key=True
    )


class TypePermission(IdMixin, Base):
    """MVP는 타입 단위 읽기 권한만 둔다(PRD 5.5). 속성·행 단위는 P1."""

    __tablename__ = "type_permission"
    __table_args__ = (Index("uq_type_permission_role_type", "role_id", "object_type", unique=True),)

    role_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("role.id", ondelete="CASCADE"))
    object_type: Mapped[str] = mapped_column(String(64))
    can_read: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class UserSession(IdMixin, Base):
    """서버 세션 — 쿠키에는 무작위 토큰만, DB에는 그 해시만 둔다(즉시 폐기 가능, TRD 3절)."""

    __tablename__ = "user_session"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("app_user.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(tz())
    revoked_at: Mapped[datetime | None] = mapped_column(tz())
    auth_method: Mapped[str] = mapped_column(String(16))  # local / oidc


class AuditLog(IdMixin, Base):
    """추가 전용. UPDATE·DELETE·TRUNCATE 는 DB 트리거가 막는다(마이그레이션 0001).

    해시 체인(``prev_hash``)은 P1이다 — 컬럼만 먼저 둔다.
    """

    __tablename__ = "audit_log"

    ts: Mapped[datetime] = mapped_column(tz(), server_default=text("now()"))
    actor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    event: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)


class AuthRequest(IdMixin, Base):
    """진행 중인 OIDC 로그인 한 건(state·nonce·PKCE verifier). 콜백에서 **한 번만** 꺼내 지운다.

    서버에 두는 이유: 쿠키에 담으면 서명 키를 따로 관리해야 하고, 재사용(replay)을 막으려면
    어차피 서버 상태가 필요하다. state 는 해시만 저장한다.
    """

    __tablename__ = "auth_request"

    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    nonce: Mapped[str] = mapped_column(String(128))
    code_verifier: Mapped[str] = mapped_column(String(128))
    return_to: Mapped[str] = mapped_column(String(512), default="/")
    expires_at: Mapped[datetime] = mapped_column(tz())
