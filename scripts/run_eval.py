"""평가 실행(TRD 13.1) — 도메인 팩 골든셋을 적재하고 운영 파이프라인으로 돌려 게이트를 판정한다.

    uv run --project backend python scripts/run_eval.py --pack sample                # 실제 모델
    uv run --project backend python scripts/run_eval.py --pack sample --mode record  # + 녹화
    uv run --project backend python scripts/run_eval.py --pack sample --mode replay  # 녹화 응답

종료 코드: 게이트 통과 0, 실패 1, 실행 오류 2. 결과는 ``eval_run``·``eval_result`` 에 남고,
``--out`` 이면 JSON 으로도 쓴다. ``.env`` 의 DB 를 쓴다(먼저 scripts/seed_domain_pack.py 와
scripts/graph_rebuild.py).

- replay: 카세트에 없는 요청은 실패(``llm_unavailable``)다. 놓친 요청이 하나라도 있으면 게이트
  실패 — 프롬프트·데이터가 바뀐 것이니 실제 모델로 다시 녹화(record)하고 게이트를 다시 통과시킨다.
  지연(p95_ms)은 녹화 응답에서 의미가 없어 보지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ontobot.config import get_settings
from ontobot.db.models.evaluation import EvalResult, EvalRun, GoldenItem
from ontobot.db.session import dispose_engine, session_scope
from ontobot.evaluation import gate, golden, replay, runner
from ontobot.jobs.handlers import graph_driver
from ontobot.llm.config import load as load_llm_config
from ontobot.ontology import registry
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]


def _line(r: EvalResult, item: GoldenItem) -> str:
    v = r.verdict
    flags = [
        name
        for name, on in (
            ("권한위반", v.get("violation")),
            ("실패은폐", v.get("cover_up")),
            ("답없음①②", v.get("unanswerable_graded")),
            ("장치오류", v.get("error")),
        )
        if on
    ]
    detail = (
        v.get("missing_values")
        or v.get("present_absent_values")
        or v.get("missing_conditions")
    )
    mark = "O" if v.get("correct") else "X"
    return (
        f"  [{mark}] {item.key:<4} {r.status}/{r.failure_reason or r.grade}"
        f" {' '.join(flags)} {detail or ''}"
    ).rstrip()


def _report(run: EvalRun, rows: list[tuple[EvalResult, GoldenItem]]) -> None:
    for r, item in rows:
        print(_line(r, item))
    m = run.metrics or {}
    print(
        json.dumps(
            {k: v for k, v in m.items() if k != "gate"}, ensure_ascii=False, indent=1
        )
    )
    for c in m.get("gate", {}).get("checks", []):
        mark = "통과" if c["passed"] else "실패"
        print(
            f"  {mark} [{c['kind']}] {c['metric']} {c['op']} {c['limit']} — 값 {c['value']}"
        )
    print(f"게이트: {'통과' if run.gate_passed else '실패'} (eval_run {run.id})")


def _dump(path: str, run: EvalRun, rows: list[tuple[EvalResult, GoldenItem]]) -> None:
    body: dict[str, Any] = {
        "run_id": run.id,
        "llm_config": run.llm_config,
        "metrics": run.metrics,
        "gate_passed": run.gate_passed,
        "results": [
            {
                "key": item.key,
                "question": item.question,
                "status": r.status,
                "failure_reason": r.failure_reason,
                "grade": r.grade,
                "conditions": r.conditions,
                "answer": r.answer,
                "verdict": r.verdict,
                "stats": r.stats,
            }
            for r, item in rows
        ],
    }
    Path(path).write_text(
        json.dumps(body, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print("저장:", path)


async def main(args: argparse.Namespace) -> int:
    s = get_settings()
    pack_dir = ROOT / "domain-packs" / args.pack
    pack = golden.parse((pack_dir / "golden.yaml").read_text(encoding="utf-8"))
    overrides = dict(kv.split("=", 1) for kv in args.override)
    if args.model:
        overrides.update({"planner": args.model, "answerer": args.model})
    cfg = runner.RunConfig(
        llm=load_llm_config(s.llm_config_path),
        overrides=overrides,
        grade2_enabled=s.grade2_enabled,
        mode=args.mode,
    )
    gate_cfg = gate.load()
    skip: tuple[str, ...] = ()
    if args.mode == "replay":
        # 녹화 응답에서는 지연이 의미 없고, 놓친 요청이 있으면 결과가 녹화 당시와 다르다.
        skip = ("p95_ms",)
        gate_cfg = {
            **gate_cfg,
            "invariants": {**gate_cfg["invariants"], "cassette_misses": {"max": 0}},
        }
    cassette_path = (
        Path(args.cassette) if args.cassette else pack_dir / "llm_cassette.json"
    )
    driver = graph_driver()
    try:
        async with session_scope() as db:
            schema = await registry.reload(db)
            gs, counts = await golden.load_pack(
                db,
                pack,
                domain_pack=args.pack,
                ontology_version_id=schema.version_id if schema else None,
            )
            run = await runner.create_run(db, gs.id, cfg)
            await db.commit()
        print(
            f"골든셋 '{gs.name}' 적재 {counts} · 실행 {run.id} · 모드 {args.mode}",
            flush=True,
        )
        with replay.use(args.mode, cassette_path) as cassette:
            # 문서 색인도 녹화·재생을 거친다(빈 DB 에서 도는 CI 재생이 같은 임베딩을 받게).
            n = await runner.reindex_documents(cfg.llm)
            if n:
                print(f"문서 {n}건 다시 색인", flush=True)
            run = await runner.execute(
                run.id,
                cfg,
                graph_driver=driver,
                gate_cfg=gate_cfg,
                skip=skip,
                extra=(lambda: {"cassette_misses": cassette.misses})
                if cassette
                else None,
            )
        async with session_scope() as db:
            result = await db.execute(
                select(EvalResult, GoldenItem)
                .join(GoldenItem, GoldenItem.id == EvalResult.item_id)
                .where(EvalResult.run_id == run.id)
                .order_by(GoldenItem.key)
            )
            rows = [(r, item) for r, item in result.all()]
        _report(run, rows)
        if args.out:
            _dump(args.out, run, rows)
    finally:
        if driver is not None:
            await driver.close()
        await dispose_engine()
    if run.status != "succeeded":
        return 2
    return 0 if run.gate_passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", default="sample")
    parser.add_argument("--mode", choices=["live", "record", "replay"], default="live")
    parser.add_argument(
        "--cassette", help="기본: domain-packs/<pack>/llm_cassette.json"
    )
    parser.add_argument(
        "--model", help="planner·answerer 모델 덮어쓰기(모델 교체 시험)"
    )
    parser.add_argument("--override", nargs="*", default=[], help="역할=모델 …")
    parser.add_argument("--out", help="결과 JSON 경로")
    sys.exit(asyncio.run(main(parser.parse_args())))
