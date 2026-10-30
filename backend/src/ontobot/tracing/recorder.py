"""트레이스 기록(TRD 7.1 8단계, 6.5) — 질의 한 건 = ``trace`` 한 행.

SQL·Cypher 에는 값이 없다(값과 속성 키는 전부 바인드). 그래서 문장 자체는 그대로 남기고, 바인드
값은 남기지 않는다. LLM 호출의 프롬프트·응답은 게이트웨이가 이미 **마스킹한** 것만 받는다(TRD 9.2,
``llm.gateway``). 계획(plan)의 조건 값도 같은 PII 값·패턴으로 가린다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.chat.pipeline import PipelineResult
from ontobot.db.models.chat import Trace
from ontobot.llm.types import CallRecord
from ontobot.security.pii import mask_payload


def _call(c: CallRecord) -> dict[str, Any]:
    return {
        "role": c.role,
        "model": c.model,
        "latency_ms": c.latency_ms,
        "ok": c.ok,
        "error": c.error,
        "messages": c.messages,  # 게이트웨이가 마스킹한 사본
        "output": c.output,
        **(c.extra or {}),
    }


async def record(
    db: AsyncSession,
    *,
    user_id: int | None,
    message_id: int | None,
    result: PipelineResult,
    llm_calls: Sequence[CallRecord] = (),
    pii_values: Iterable[str] = (),
) -> Trace:
    trace = Trace(
        user_id=user_id,
        message_id=message_id,
        plan=mask_payload(
            {"plan": result.plan, "attempts": result.plan_attempts}, list(pii_values)
        ),
        sql_masked=result.sql,
        cypher_masked=result.cypher,
        status=result.status,
        failure_reason=result.failure_reason,
        grade=result.grade,
        row_count=result.row_count,
        fact_sentences=result.fact_sentences,
        verified_sentences=result.verified_sentences,
        removed_citations=result.removed_citations,
        stage_latency_ms=result.stage_latency_ms,
        llm_calls=[_call(c) for c in llm_calls],
    )
    db.add(trace)
    await db.flush()
    return trace
