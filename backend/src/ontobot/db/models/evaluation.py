"""평가 — 골든셋·문항·실행·결과(TRD 6.7, v0.2 정정 2·3)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ontobot.db.models.base import Base, IdMixin, tz


class GoldenSet(IdMixin, Base):
    __tablename__ = "golden_set"

    name: Mapped[str] = mapped_column(String(128), unique=True)
    domain_pack: Mapped[str | None] = mapped_column(String(64))
    ontology_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ontology_version.id")
    )
    # 평가 전용 역할 → 읽을 수 있는 타입 목록. 문항의 test_role 이 여기 있으면 이 권한으로 돈다
    # (DB 역할을 만들지 않는다 — 평가가 운영 권한 표를 바꾸면 안 된다). 없으면 DB 역할을 본다.
    roles: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # 평가 전용 역할의 속성 숨김·행 정책(Phase 14b) — {역할: {hidden: {타입: [속성]}, rows: {타입:
    # [조건]}}}.
    restrictions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class GoldenItem(IdMixin, Base):
    __tablename__ = "golden_item"
    __table_args__ = (
        CheckConstraint("fault IN ('none','executor_error','graph_down','timeout')", name="fault"),
        CheckConstraint("source IN ('manual','workshop')", name="source"),
        UniqueConstraint("golden_set_id", "key", name="uq_golden_item_set_key"),
    )

    golden_set_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("golden_set.id", ondelete="CASCADE"), index=True
    )
    # 도메인 팩 문항 id(q01 …). 팩을 다시 적재할 때 같은 문항을 찾는 키다.
    key: Mapped[str | None] = mapped_column(String(64))
    question: Mapped[str] = mapped_column(Text)
    test_role: Mapped[str | None] = mapped_column(String(64))
    answerable: Mapped[bool] = mapped_column(Boolean, default=True)
    expected_status: Mapped[str] = mapped_column(String(32))
    expected_grade: Mapped[int | None] = mapped_column(Integer)
    # 현업 담당자가 확정한 기대 답변(워크숍 골든 질의, TRD 12절). 채점은 required_values 로 한다.
    expected_answer: Mapped[str | None] = mapped_column(Text)
    required_values: Mapped[list[str]] = mapped_column(JSONB, default=list)
    forbidden_values: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # failed 일 때 기대 사유(plan_invalid·forbidden …). 비우면 사유는 보지 않는다.
    expected_failure: Mapped[str | None] = mapped_column(String(32))
    # 답에 있으면 안 되는 값(조건이 빠져 걸러져야 할 객체가 남았는지).
    absent_values: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # 조회 조건 문장에 있어야 할 조각(조건 누락 감시).
    required_conditions: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # 대화 맥락으로 줄 직전 답의 객체 [{type, pk}].
    context: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    # Action 요청 문항의 기대 제안 {action_type, target_pk, params, ready}(Phase 11).
    expected_action: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(Text)
    fault: Mapped[str] = mapped_column(String(32), default="none", server_default="none")
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")


class EvalRun(IdMixin, Base):
    __tablename__ = "eval_run"
    __table_args__ = (CheckConstraint("status IN ('running','succeeded','failed')", name="status"),)

    golden_set_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("golden_set.id"))
    ontology_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ontology_version.id")
    )
    llm_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16))
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    gate_passed: Mapped[bool | None] = mapped_column(Boolean)
    started_at: Mapped[datetime] = mapped_column(tz())
    finished_at: Mapped[datetime | None] = mapped_column(tz())


class EvalResult(IdMixin, Base):
    __tablename__ = "eval_result"

    run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("eval_run.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("golden_item.id"))
    status: Mapped[str | None] = mapped_column(String(16))
    failure_reason: Mapped[str | None] = mapped_column(String(32))
    grade: Mapped[int | None] = mapped_column(Integer)
    answer: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    verdict: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    conditions: Mapped[str | None] = mapped_column(Text)
    # 단계별 지연·사실 문장 수 등 지표 계산 재료(TRD 13.2). 트레이스 표와 따로 둔다.
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class OpsTarget(IdMixin, Base):
    """PRD 7절 성공 지표의 목표 수치 — 관리자가 화면에서 정한다(행이 없으면 제안 기본값).

    평가 게이트(``evaluation/gate.yaml``)의 불변식·가안과 별개다 — 게이트는 CI 에서 DB 없이도 같은
    기준으로 돌아야 하므로 파일이 정본이고, 이 표는 운영 화면에서 측정값과 견주는 목표다.
    """

    __tablename__ = "ops_target"

    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[Decimal] = mapped_column(Numeric)
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = mapped_column(
        tz(), server_default=func.now(), onupdate=func.now()
    )
