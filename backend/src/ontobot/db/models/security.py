"""보안 — 사용자·역할·타입 권한·세션·감사 로그(TRD 6.6, 9절)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz


class AppUser(IdMixin, Base):
    __tablename__ = "app_user"
    __table_args__ = (
        CheckConstraint("status IN ('active','disabled')", name="status"),
        CheckConstraint("kind IN ('person','service')", name="kind"),
    )

    # OIDC 사용자는 oidc_sub, 로컬 계정은 username + password_hash 를 가진다.
    oidc_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
    username: Mapped[str | None] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    # Action 알림(메일·SMS) 연락처 — PII. 화면·로그·감사에서는 마스킹한다.
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    # 서비스 계정(Phase 17b, TRD 17.1 결정 14)은 로그인하지 않는다 — API 키로만 부른다. 역할·데이터
    # 그룹은 사람 계정과 같은 규칙으로 받는다(권한 판정은 rbac 하나).
    kind: Mapped[str] = mapped_column(String(16), default="person", server_default="person")


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


class DataGroup(IdMixin, Base):
    """데이터 권한 그룹(Phase 14) — 역할은 화면·기능, 그룹은 데이터 권한이다.

    사용자의 데이터 권한은 소속 그룹 권한의 **합집합**이다(허용만, 거부 규칙 없음).
    ``external_id`` 가 있으면 SSO(OIDC) 그룹 클레임과 **정확히** 같은 값으로 소속을 맞춘다
    (설정 ``oidc_groups_claim``).
    """

    __tablename__ = "data_group"

    name: Mapped[str] = mapped_column(String(64), unique=True)
    display_name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(255))
    external_id: Mapped[str | None] = mapped_column(String(255), unique=True)


class GroupMember(Base):
    __tablename__ = "group_member"

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("data_group.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )


class GroupTypePermission(IdMixin, Base):
    """그룹 × 객체 타입 — 읽기·생성·편집·삭제. 판정은 ``security.rbac`` 에서만 한다.

    편집은 Action(편집 레이어)이 쓰고, 생성·삭제는 데이터 그래프 편집(Phase 15 이후)이 쓴다.
    """

    __tablename__ = "group_type_permission"
    __table_args__ = (Index("uq_group_type_permission", "group_id", "object_type", unique=True),)

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("data_group.id", ondelete="CASCADE")
    )
    object_type: Mapped[str] = mapped_column(String(64))
    can_read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    can_create: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    can_edit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class GroupLinkPermission(IdMixin, Base):
    """그룹 × 링크 타입 — 연결(링크를 만들고 끊기). 데이터 그래프 편집(Phase 15 이후)이 쓴다."""

    __tablename__ = "group_link_permission"
    __table_args__ = (Index("uq_group_link_permission", "group_id", "link_type", unique=True),)

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("data_group.id", ondelete="CASCADE")
    )
    link_type: Mapped[str] = mapped_column(String(64))
    can_link: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class GroupPropertyHide(IdMixin, Base):
    """그룹이 숨기는 속성(Phase 14b). 기본은 "타입을 읽으면 모두 보임"이고, 여기 적은 것만 숨긴다.

    합집합 규칙: 속성은 그 타입을 읽게 해 주는 **모든** 그룹이 숨길 때만 숨겨진다.
    pk·제목 속성은 숨기지 못한다(근거·대화 맥락·그래프가 기대는 식별자다 — 관리 규칙이 막는다).
    """

    __tablename__ = "group_property_hide"
    __table_args__ = (
        Index("uq_group_property_hide", "group_id", "object_type", "property", unique=True),
    )

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("data_group.id", ondelete="CASCADE")
    )
    object_type: Mapped[str] = mapped_column(String(64))
    property: Mapped[str] = mapped_column(String(64))


class GroupRowPolicy(IdMixin, Base):
    """그룹의 행 정책(Phase 14b) — QueryPlan ``Filter`` 문법의 조건 목록(AND).

    ``applies_to`` read 는 조회(컴파일러가 대상·탐색 끝에 덧붙인다), write 는 Action 대상
    (미리보기·승인 때 다시 확인). 합집합 규칙: 그 권한을 주는 그룹마다 정책을 OR 로 묶고, 정책이
    없는 그룹이 하나라도 있으면 제한하지 않는다.
    """

    __tablename__ = "group_row_policy"
    __table_args__ = (
        CheckConstraint("applies_to IN ('read','write')", name="applies_to"),
        Index("uq_group_row_policy", "group_id", "object_type", "applies_to", unique=True),
    )

    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("data_group.id", ondelete="CASCADE")
    )
    object_type: Mapped[str] = mapped_column(String(64))
    applies_to: Mapped[str] = mapped_column(String(8))
    filters: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)


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

    해시 체인(Phase 14c, 마이그레이션 0014): 넣을 때 DB 트리거가 ``seq``·``prev_hash``·``hash`` 를
    매긴다
    (앱은 넣지 않는다). 검증은 ``security.audit_chain``.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("uq_audit_log_seq", "seq", unique=True),)

    ts: Mapped[datetime] = mapped_column(tz(), server_default=text("now()"))
    actor_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    event: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    seq: Mapped[int | None] = mapped_column(BigInteger)
    hash: Mapped[str | None] = mapped_column(String(64))


class AuditChainHead(Base):
    """감사 체인의 머리(한 줄) — 마지막 순번과 해시. 트리거만 고친다. 꼬리 삭제를 잡는 데 쓴다."""

    __tablename__ = "audit_chain_head"
    __table_args__ = (CheckConstraint("id = 1", name="single"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(BigInteger, server_default="0")
    hash: Mapped[str | None] = mapped_column(String(64))


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


class ApiKey(IdMixin, Base):
    """공개 REST API 키(Phase 17b, TRD 17.1 결정 14) — 서비스 계정 하나에 매인다.

    키 원문은 발급 응답에 한 번만 나가고, DB 에는 SHA-256 만 둔다(세션 토큰과 같다). ``prefix`` 는
    화면·감사에서 키를 가리키는 앞부분(비밀 아님). 스코프는 ``query``·``objects:read``·
    ``actions:request`` 중 몇 개 — 판정은 ``security.rbac.has_scope`` 하나.
    """

    __tablename__ = "api_key"

    name: Mapped[str] = mapped_column(String(64))
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("app_user.id", ondelete="CASCADE"), index=True
    )
    scopes: Mapped[list[str]] = mapped_column(JSONB)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    expires_at: Mapped[datetime | None] = mapped_column(tz())
    revoked_at: Mapped[datetime | None] = mapped_column(tz())
    last_used_at: Mapped[datetime | None] = mapped_column(tz())
