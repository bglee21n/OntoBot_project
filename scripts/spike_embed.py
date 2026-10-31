"""Phase 16b 측정 — 임베딩 모델별로 샘플 문서 검색 문항을 돌려 잰다(ADR-0016, TRD 17.1 결정 11).

    uv run --project backend python scripts/spike_embed.py --models bge-m3 nomic-embed-text

문서(``domain-packs/sample/documents``)를 운영과 같은 추출·청크 코드로 자르고(``--max-chars`` 마다),
질문(``doc_queries.yaml``)과의 코사인 유사도로 순위를 매긴다. 정답 = ``expect`` 글을 **그대로** 담은
청크. 잰 것: hit@1·3·5, MRR, 답 있는/없는 질문의 1위 점수(16d 컷오프 근거), 차원, 지연.
``--prefix`` 를 주면 nomic 계열 권장 접두어(``search_query:``·``search_document:``)를
붙인 변형도 잰다.
DB 를 쓰지 않는다. 결과는 docs/adr/data/ 에 JSON 으로 남긴다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from ontobot.config import get_settings
from ontobot.documents import chunking, extract
from ontobot.llm import config as llm_config
from ontobot.llm.gateway import LLMGateway

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "domain-packs" / "sample"
ROLE = "embedder"
PREFIX = ("search_query: ", "search_document: ")


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _chunks(max_chars: int) -> list[tuple[str, str]]:
    """(문서 파일 이름, 청크 글) — 운영 경로와 같은 추출·청크."""
    out: list[tuple[str, str]] = []
    for path in sorted((PACK / "documents").glob("*.md")):
        if path.name == "README.md":
            continue
        _, text = extract.extract(path.name, path.read_bytes())
        out += [(path.name, c.text) for c in chunking.chunk(text, max_chars=max_chars)]
    return out


def _separation(answerable: list[float], unanswerable: list[float]) -> dict[str, Any]:
    """1위 점수 하나로 답 있음/없음을 가를 때 가장 잘 맞는 문턱과 그 정확도."""
    best = (0.0, 0.0)
    for t in sorted(set(answerable + unanswerable)):
        ok = sum(s >= t for s in answerable) + sum(s < t for s in unanswerable)
        acc = ok / (len(answerable) + len(unanswerable))
        if acc > best[0]:
            best = (acc, t)
    return {"accuracy": round(best[0], 3), "threshold": round(best[1], 4)}


async def _run(llm: LLMGateway, max_chars: int, prefix: bool) -> dict[str, Any]:
    queries = yaml.safe_load((PACK / "doc_queries.yaml").read_text(encoding="utf-8"))["queries"]
    chunks = _chunks(max_chars)
    qp, dp = PREFIX if prefix else ("", "")
    started = time.perf_counter()
    doc_vecs: list[list[float]] = []
    for i in range(0, len(chunks), 16):
        doc_vecs += await llm.embed(ROLE, [dp + t for _, t in chunks[i : i + 16]])
    doc_ms = (time.perf_counter() - started) * 1000
    doc_vecs = [_unit(v) for v in doc_vecs]
    rows: list[dict[str, Any]] = []
    q_ms: list[float] = []
    for item in queries:
        t0 = time.perf_counter()
        (qv,) = await llm.embed(ROLE, [qp + item["q"]])
        q_ms.append((time.perf_counter() - t0) * 1000)
        qv = _unit(qv)
        scores = [sum(a * b for a, b in zip(qv, d, strict=True)) for d in doc_vecs]
        order = sorted(range(len(chunks)), key=lambda j: -scores[j])
        row: dict[str, Any] = {"q": item["q"], "top1": round(scores[order[0]], 4)}
        if item.get("answerable", True):
            gold = {
                j
                for j, (name, text) in enumerate(chunks)
                if name == item["doc"] and item["expect"] in text
            }
            if not gold:
                raise SystemExit(f"정답 청크 없음(청크 경계?): {item['q']} @ {max_chars}")
            rank = next(r for r, j in enumerate(order, 1) if j in gold)
            row |= {"rank": rank}
        row["top1_doc"] = chunks[order[0]][0]
        rows.append(row)
    ans = [r for r in rows if "rank" in r]
    una = [r for r in rows if "rank" not in r]
    ans_top = [r["top1"] for r in ans]
    una_top = [r["top1"] for r in una]
    return {
        "max_chars": max_chars,
        "prefix": prefix,
        "chunks": len(chunks),
        "dims": len(doc_vecs[0]),
        "answerable": len(ans),
        "hit@1": sum(r["rank"] == 1 for r in ans),
        "hit@3": sum(r["rank"] <= 3 for r in ans),
        "hit@5": sum(r["rank"] <= 5 for r in ans),
        "mrr": round(statistics.mean(1 / r["rank"] for r in ans), 3),
        "top1_answerable": {"min": min(ans_top), "median": round(statistics.median(ans_top), 4)},
        "top1_unanswerable": {"max": max(una_top), "median": round(statistics.median(una_top), 4)},
        "separation": _separation(ans_top, una_top),
        "doc_ms_per_chunk": round(doc_ms / len(chunks), 1),
        "query_ms_p50": round(statistics.median(q_ms), 1),
        "misses": [r for r in ans if r["rank"] > 1],
        "unanswerable_rows": una,
    }


async def _measure(models: list[str], sizes: list[int], prefix: bool) -> list[dict[str, Any]]:
    cfg = llm_config.load(get_settings().llm_config_path)
    out: list[dict[str, Any]] = []
    for model in models:
        llm = LLMGateway(cfg, overrides={ROLE: model})
        await llm.embed(ROLE, ["예열"])  # 첫 호출의 모델 적재 시간을 지연에서 뺀다
        for size in sizes:
            for p in [False, True] if prefix else [False]:
                result = {"model": model} | await _run(llm, size, p)
                summary = {k: v for k, v in result.items() if not isinstance(v, list)}
                print(json.dumps(summary, ensure_ascii=False))
                out.append(result)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--max-chars", nargs="+", type=int, default=[800, 400])
    ap.add_argument("--prefix", action="store_true", help="검색 접두어 변형도 잰다")
    args = ap.parse_args()
    results = asyncio.run(_measure(args.models, args.max_chars, args.prefix))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = ROOT / "docs" / "adr" / "data" / f"0016-embed-{stamp}.json"
    report = {"measured_at": stamp, "results": results}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {path.relative_to(ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
