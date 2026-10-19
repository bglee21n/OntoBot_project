"""트레이스 기록(TRD 7.1 8단계, 6.5) — 질의 한 건 = ``trace`` 한 행.

SQL·Cypher 에는 값이 없다(값과 속성 키는 전부 바인드). 그래서 문장 자체는 그대로 남겨도 되지만,
바인드 값은 남기지 않는다 — PII 가 들어 있을 수 있다. LLM 호출도 같은 이유로 프롬프트·출력 본문을
남기지 않고 역할·모델·지연·토큰·성공 여부만 남긴다. 본문을 마스킹해 남기는 것은 Phase 5
(``security.pii`` 가역 토큰화와 함께)다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.chat.pipeline import PipelineResult
from ontobot.db.models.chat import Trace
from ontobot.llm.types import CallRecord


def _call(c: CallRecord) -> dict[str, Any]:
    return {
        "role": c.role,
        "model": c.model,
        "latency_ms": c.latency_ms,
        "ok": c.ok,
        "error": c.error,
        **{k: v for k, v in (c.extra or {}).items() if k in {"prompt_tokens", "completion_tokens"}},
    }


async def record(
    db: AsyncSession,
    *,
    user_id: int | None,
    message_id: int | None,
    result: PipelineResult,
    llm_calls: Sequence[CallRecord] = (),
) -> Trace:
    trace = Trace(
        user_id=user_id,
        message_id=message_id,
        plan={"plan": result.plan, "attempts": result.plan_attempts},
        sql_masked=result.sql,
        cypher_masked=result.cypher,
        status=result.status,
        failure_reason=result.failure_reason,
        grade=result.grade,
        fact_sentences=result.fact_sentences,
        verified_sentences=result.verified_sentences,
        removed_citations=result.removed_citations,
        stage_latency_ms=result.stage_latency_ms,
        llm_calls=[_call(c) for c in llm_calls],
    )
    db.add(trace)
    await db.flush()
    return trace
