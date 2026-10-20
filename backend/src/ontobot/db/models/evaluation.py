"""평가 — 골든셋·문항·실행·결과(TRD 6.7, v0.2 정정 2·3)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Integer, String, Text
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


class GoldenItem(IdMixin, Base):
    __tablename__ = "golden_item"
    __table_args__ = (
        CheckConstraint("fault IN ('none','executor_error','graph_down','timeout')", name="fault"),
        CheckConstraint("source IN ('manual','workshop')", name="source"),
    )

    golden_set_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("golden_set.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    test_role: Mapped[str | None] = mapped_column(String(64))
    answerable: Mapped[bool] = mapped_column(Boolean, default=True)
    expected_status: Mapped[str] = mapped_column(String(32))
    expected_grade: Mapped[int | None] = mapped_column(Integer)
    # 현업 담당자가 확정한 기대 답변(워크숍 골든 질의, TRD 12절). 채점은 required_values 로 한다.
    expected_answer: Mapped[str | None] = mapped_column(Text)
    required_values: Mapped[list[str]] = mapped_column(JSONB, default=list)
    forbidden_values: Mapped[list[str]] = mapped_column(JSONB, default=list)
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
