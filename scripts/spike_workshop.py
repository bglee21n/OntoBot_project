"""Phase 6 측정 — 면담자·추출기 모델별로 샘플 인터뷰 대본을 돌려 잰다(ADR-0008).

    uv run --project backend python scripts/spike_workshop.py --models gemma4:e4b

잰 것: 구조화 출력 첫 시도 실패·최종 실패, 면담자 평가 일치, 종류별 기대 개수·이름 충족,
근거 확인 통과율(``evidence_ok``), 확정 검증 통과율(이름 형식 — 사람이 고치지 않고 확정할 수 있는
비율), 자료형 판단, 골든 질의 기대 답변, 지연.
DB 를 쓰지 않는다. 결과는 docs/adr/data/ 에 JSON 으로 남긴다.
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
from ontobot.config import get_settings
from ontobot.llm import config as llm_config
from ontobot.llm.gateway import LLMGateway
from ontobot.llm.structured import StructuredOutputError
from ontobot.workshop import elements, evidence, extractor, interviewer, topics

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "domain-packs" / "sample" / "interview.yaml"


def _expectations(item: dict[str, Any], found: list[extractor.Candidate]) -> list[str]:
    """기대를 어긴 항목의 설명 목록(빈 목록 = 통과)."""
    misses: list[str] = []
    for kind, rule in (item.get("expect") or {}).items():
        mine = [c for c in found if c.kind == kind]
        if len(mine) < rule.get("min", 0):
            misses.append(f"{kind}: {len(mine)} < {rule['min']}")
        names = " ".join(str(c.payload.get("api_name", "")).lower() for c in mine)
        misses += [
            f"{kind}: '{n}' 없음" for n in rule.get("names", []) if n not in names
        ]
    for display, allowed in (item.get("expect_types") or {}).items():
        p = next(
            (
                c
                for c in found
                if c.kind == "property" and c.payload.get("display_name") == display
            ),
            None,
        )
        if p is None:
            misses.append(f"property '{display}' 없음")
        elif p.payload.get("data_type") not in allowed:
            misses.append(f"'{display}' 자료형 {p.payload.get('data_type')}")
    for value in item.get("expect_answer_contains", []):
        answers = [str(c.payload.get("expected_answer") or "") for c in found]
        if not any(value in a for a in answers):
            misses.append(f"기대 답변에 '{value}' 없음")
    return misses


async def _one(llm: LLMGateway, item: dict[str, Any]) -> dict[str, Any]:
    topic = topics.topic(item["topic"])
    question = topic.opening
    started = len(llm.calls)
    t0 = time.perf_counter()
    found: list[extractor.Candidate] = []
    extract_error = None
    try:
        found = await extractor.extract(llm, topic, question, item["text"], [])
    except StructuredOutputError as e:
        extract_error = str(e)
    t1 = time.perf_counter()
    verdict = None
    try:
        verdict = (await interviewer.assess(llm, topic, question, item["text"])).verdict
    except StructuredOutputError:
        pass
    t2 = time.perf_counter()
    calls = llm.calls[started:]
    ev_ok = [evidence.verify(c.quote, item["text"]) for c in found]
    schema_kinds = [
        c for c in found if c.kind in ("object_type", "property", "link_type")
    ]
    valid = 0
    for c in schema_kinds:
        try:
            elements.validate(c.kind, c.payload)
            valid += 1
        except elements.PayloadInvalid:
            pass
    misses = _expectations(item, found)
    return {
        "id": item["id"],
        "topic": item["topic"],
        "first_attempt_failures": sum(
            1 for i, c in enumerate(calls) if not c.ok and (i == 0 or calls[i - 1].ok)
        ),
        "extract_error": extract_error,
        "verdict": verdict,
        "verdict_ok": verdict == item.get("expect_verdict", verdict),
        "found": [
            {"kind": c.kind, "payload": c.payload, "quote": c.quote} for c in found
        ],
        "evidence_ok": sum(ev_ok),
        "evidence_total": len(ev_ok),
        "schema_valid": valid,
        "schema_total": len(schema_kinds),
        "misses": misses,
        "extract_ms": int((t1 - t0) * 1000),
        "assess_ms": int((t2 - t1) * 1000),
    }


async def run(models: list[str]) -> dict[str, Any]:
    items = yaml.safe_load(SCRIPT.read_text(encoding="utf-8"))["answers"]
    cfg = llm_config.load(get_settings().llm_config_path)
    report: dict[str, Any] = {"at": datetime.now(UTC).isoformat(), "models": {}}
    for model in models:
        llm = LLMGateway(cfg, overrides={"extractor": model, "interviewer": model})
        rows = [await _one(llm, item) for item in items]
        ev_total = sum(r["evidence_total"] for r in rows)
        sc_total = sum(r["schema_total"] for r in rows)
        summary = {
            "items": len(rows),
            "expectations_met": sum(1 for r in rows if not r["misses"]),
            "verdict_ok": sum(1 for r in rows if r["verdict_ok"]),
            "first_attempt_failures": sum(r["first_attempt_failures"] for r in rows),
            "extract_errors": sum(1 for r in rows if r["extract_error"]),
            "evidence_ok_rate": round(sum(r["evidence_ok"] for r in rows) / ev_total, 3)
            if ev_total
            else None,
            "schema_valid_rate": round(
                sum(r["schema_valid"] for r in rows) / sc_total, 3
            )
            if sc_total
            else None,
            "p50_turn_ms": int(
                statistics.median(r["extract_ms"] + r["assess_ms"] for r in rows)
            ),
            "max_turn_ms": max(r["extract_ms"] + r["assess_ms"] for r in rows),
        }
        report["models"][model] = {"summary": summary, "rows": rows}
        print(model, json.dumps(summary, ensure_ascii=False))
        for r in rows:
            if r["misses"] or not r["verdict_ok"] or r["extract_error"]:
                print(
                    "  ", r["id"], r["verdict"], r["misses"], r["extract_error"] or ""
                )
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    args = ap.parse_args()
    report = asyncio.run(run(args.models))
    out = ROOT / "docs" / "adr" / "data"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"0008-workshop-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("저장:", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
