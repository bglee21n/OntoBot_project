"""Phase 1.5 스파이크 — 플래너·답변 모델별로 골든 문항을 파이프라인 끝까지 돌려 잰다(ADR-0004).

    uv run --project backend python scripts/spike_plan.py --models gemma4:e2b qwen3:8b

잰 것: 구조화 출력 첫 시도 실패, 최종 plan_invalid, 상태 일치, 필수 값 포함, 등급 분포, 추정 문장,
대조 실패로 지운 인용, 단계별 지연. 결과는 docs/adr/data/ 에 JSON 으로 남긴다.

권한은 모든 타입 읽기로 둔다 — 권한 침투는 Phase 7 평가의 몫이다. ``.env`` 의 DB 를 읽기만 한다
(먼저 scripts/seed_domain_pack.py 로 시드).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from ontobot.chat import pipeline
from ontobot.config import get_settings
from ontobot.db.session import dispose_engine, session_scope
from ontobot.grounding.normalize import contains
from ontobot.llm import config as llm_config
from ontobot.llm.gateway import LLMGateway
from ontobot.ontology import registry

ROOT = Path(__file__).resolve().parents[1]


def _check(item: dict[str, Any], res: pipeline.PipelineResult) -> dict[str, Any]:
    status_ok = res.status == item["expected_status"] and (
        res.status != "failed"
        or res.failure_reason == item.get("expected_failure", res.failure_reason)
    )
    values = item.get("required_values", [])
    values_ok = all(contains(res.answer, v) for v in values)
    first = res.plan_attempts[0] if res.plan_attempts else {}
    return {
        "id": item["id"],
        "question": item["question"],
        "expected": item["expected_status"],
        "status": res.status,
        "failure_reason": res.failure_reason,
        "grade": res.grade,
        "status_ok": status_ok,
        "values_ok": values_ok,
        "correct": status_ok and values_ok,
        "first_structured_ok": first.get("structured_ok"),
        "first_valid": bool(first.get("structured_ok")) and not first.get("errors"),
        "plan_attempts": res.plan_attempts,
        "conditions": res.conditions,
        "plan": res.plan,
        "answer": res.answer,
        "raw_answer": res.raw_answer,
        "removed_citations": res.removed_citations,
        "estimated": len(res.estimated_sentences),
        "fact_sentences": res.fact_sentences,
        "verified_sentences": res.verified_sentences,
        "latency_ms": res.stage_latency_ms,
    }


async def run_model(model: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cfg = llm_config.load(get_settings().llm_config_path)
    out = []
    for item in items:
        llm = LLMGateway(cfg, overrides={"planner": model, "answerer": model})
        async for db in session_scope():
            schema = registry.current() or await registry.reload(db)
            readable = {t.api_name for t in schema.document.object_types} if schema else set()
            started = time.perf_counter()
            res = await pipeline.run(
                db,
                llm,
                schema,
                readable,
                item["question"],
                grade2_enabled=get_settings().grade2_enabled,
            )
            await db.rollback()
        row = _check(item, res)
        row["total_ms"] = int((time.perf_counter() - started) * 1000)
        mark = "O" if row["correct"] else "X"
        print(
            f"  [{mark}] {item['id']} {res.status}/{res.failure_reason or res.grade} "
            f"{row['total_ms']}ms  {res.conditions or ''}",
            flush=True,
        )
        out.append(row)
    return out


def summarize(model: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [r for r in rows if r["expected"] == "ok"]
    queried = [r for r in rows if r["expected"] != "not_query"]
    facts = sum(r["fact_sentences"] for r in rows)
    return {
        "model": model,
        "items": len(rows),
        "correct": sum(r["correct"] for r in rows),
        "answerable_correct": f"{sum(r['correct'] for r in answerable)}/{len(answerable)}",
        "first_structured_fail": sum(r["first_structured_ok"] is False for r in rows),
        "first_plan_invalid": sum(not r["first_valid"] for r in queried),
        "final_plan_invalid": sum(r["failure_reason"] == "plan_invalid" for r in rows),
        "grades": {g: sum(r["grade"] == g for r in rows) for g in (1, 2, 3)},
        "estimated_ratio": round(sum(r["estimated"] for r in rows) / facts, 3) if facts else None,
        "removed_citations": sum(len(r["removed_citations"]) for r in rows),
        "p50_ms": int(statistics.median(r["total_ms"] for r in rows)),
        "max_ms": max(r["total_ms"] for r in rows),
    }


async def main(models: list[str]) -> int:
    items = yaml.safe_load((ROOT / "domain-packs/sample/golden.yaml").read_text(encoding="utf-8"))[
        "items"
    ]
    summaries, details = [], {}
    try:
        for model in models:
            print(f"== {model}", flush=True)
            rows = await run_model(model, items)
            details[model] = rows
            summaries.append(summarize(model, rows))
    finally:
        await dispose_engine()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "docs" / "adr" / "data" / f"0004-spike-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"summaries": summaries, "details": details}, ensure_ascii=False, indent=2, default=str
        ),
        encoding="utf-8",
    )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="+", required=True)
    sys.exit(asyncio.run(main(parser.parse_args().models)))
