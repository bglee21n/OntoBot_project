"""부하 측정(Phase 8, TRD 13.2 '동시 사용자') — 실행 중인 API 에 동시 사용자 단계별로 채팅을 보낸다.

    $env:ONTOBOT_LOAD_PASSWORD = "…"
    uv run --project backend python scripts/load_test.py --username loadtest --users 1,4,8

- 가상 사용자마다 따로 로그인한다(세션 쿠키 하나 = 사용자 하나). 질문은 골든셋의 정상 단일 질문
  (맥락·장애 주입·평가 역할이 없는 ``ok`` 문항 — Function 문항 포함)을 사용자마다 다른 순서로 돈다.
  ``--with-actions`` 면 미리보기를 만드는 Action 요청 문항(``expected_action.ready``)도 섞는다 —
  채팅이 실행 기록(미리보기)을 쓰므로 **평가·시험 DB 에서만** 쓴다. 실행(승인)은 하지 않는다.
- 지연은 요청을 보낸 때부터 SSE ``done`` 까지(사용자가 기다리는 시간). 첫 이벤트까지도 따로 잰다.
- 결과 상태(ok·empty·not_query·failed 사유)를 센다. 부하에서 ``failed(timeout)`` 이 느는지가
  동시 사용자 상한을 정하는 신호다 — 빨라도 실패가 늘면 그 단계는 통과가 아니다.
- 실행 전 ``/readyz`` 가 ``unavailable`` 이면 멈춘다(내려간 서비스를 재는 것은 의미가 없다).

결과는 ``docs/adr/data/load-<시각>.json`` 에 남긴다(ADR 에 근거로 적는다). 실제 모델이 응답하므로
개발 PC 에서는 GPU 하나를 나눠 쓴다 — 수치는 그 장비 기준이다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
API = "/api/v1"


@dataclass
class Sample:
    users: int
    user: int
    question: str
    outcome: str
    latency_ms: int
    first_event_ms: int | None


def questions(pack: str, *, with_actions: bool = False) -> list[str]:
    doc = yaml.safe_load((ROOT / "domain-packs" / pack / "golden.yaml").read_text(encoding="utf-8"))
    wanted = {"ok", "action_preview"} if with_actions else {"ok"}
    out = [
        it["question"]
        for it in doc["items"]
        if it.get("expected_status") in wanted
        and (
            it.get("expected_status") == "ok"
            or (it.get("expected_action") or {}).get("ready", True)
        )
        and not it.get("context")
        and it.get("fault", "none") == "none"
        and not it.get("test_role")
    ]
    if not out:
        raise SystemExit(f"{pack} 골든셋에 쓸 수 있는 질문이 없습니다")
    return out


def percentile(values: list[int], p: float) -> int | None:
    """최근접 순위(nearest-rank). 표본이 적을 때 보간으로 없는 값을 만들지 않는다."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * p // 100))  # ceil
    return ordered[int(rank) - 1]


async def ask(client: httpx.AsyncClient, question: str) -> tuple[str, int, int | None]:
    started = time.perf_counter()
    first: int | None = None
    outcome = "no_done"
    event = ""
    async with client.stream("POST", f"{API}/chat", json={"question": question}) as res:
        if res.status_code != 200:
            await res.aread()
            return (
                f"http_{res.status_code}",
                int((time.perf_counter() - started) * 1000),
                None,
            )
        async for line in res.aiter_lines():
            if line.startswith("event: "):
                event = line.removeprefix("event: ")
                if first is None:
                    first = int((time.perf_counter() - started) * 1000)
            elif line.startswith("data: "):
                data: dict[str, Any] = json.loads(line.removeprefix("data: "))
                if event == "grade":
                    outcome = str(data["status"])
                elif event == "action_preview":
                    outcome = "action_preview" if data.get("run_id") else "action_unready"
                elif event == "error":
                    outcome = f"failed:{data['reason']}"
                elif event == "done":
                    break
    return outcome, int((time.perf_counter() - started) * 1000), first


async def virtual_user(
    base_url: str,
    username: str,
    password: str,
    users: int,
    index: int,
    qs: list[str],
    rounds: int,
    timeout_s: float,
) -> list[Sample]:
    samples: list[Sample] = []
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout_s) as client:
        res = await client.post(
            f"{API}/auth/local/login", json={"username": username, "password": password}
        )
        if res.status_code != 200:
            raise SystemExit(f"로그인 실패({res.status_code}): {res.text[:200]}")
        for r in range(rounds):
            # 사용자마다 다른 질문부터 — 모두 같은 질문을 동시에 보내면 캐시·순서 효과가 섞인다.
            q = qs[(index + r * users) % len(qs)]
            try:
                outcome, latency, first = await ask(client, q)
            except httpx.HTTPError as e:
                outcome, latency, first = (
                    f"client_{type(e).__name__}",
                    int(timeout_s * 1000),
                    None,
                )
            samples.append(Sample(users, index, q, outcome, latency, first))
            print(f"  [{users}명 #{index}] {latency:>6}ms {outcome:<24} {q}", flush=True)
    return samples


def summarize(samples: list[Sample]) -> dict[str, Any]:
    lat = [s.latency_ms for s in samples]
    first = [s.first_event_ms for s in samples if s.first_event_ms is not None]
    outcomes = Counter(s.outcome for s in samples)
    answered = sum(
        n
        for o, n in outcomes.items()
        if o in ("ok", "empty", "not_query", "action_preview", "action_unready")
    )
    return {
        "requests": len(samples),
        "outcomes": dict(outcomes),
        "failure_rate": round(1 - answered / len(samples), 3) if samples else None,
        "p50_ms": percentile(lat, 50),
        "p95_ms": percentile(lat, 95),
        "max_ms": max(lat) if lat else None,
        "mean_ms": int(statistics.mean(lat)) if lat else None,
        "first_event_p95_ms": percentile(first, 95),
    }


async def main() -> int:
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--username", required=True)
    ap.add_argument(
        "--password-env",
        default="ONTOBOT_LOAD_PASSWORD",
        help="비밀번호를 담은 환경변수 이름(명령줄에 비밀번호를 적지 않는다)",
    )
    ap.add_argument("--users", default="1,2,4", help="동시 사용자 단계(쉼표)")
    ap.add_argument("--rounds", type=int, default=4, help="사용자 한 명이 보내는 질문 수")
    ap.add_argument("--pack", default="sample")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--with-actions",
        action="store_true",
        help="Action 요청(미리보기) 문항도 섞는다 — 실행 기록이 생기므로 평가·시험 DB 에서만",
    )
    args = ap.parse_args()

    password = os.environ.get(args.password_env)
    if not password:
        raise SystemExit(f"환경변수 {args.password_env} 에 비밀번호를 넣어 주세요")
    steps = [int(x) for x in args.users.split(",") if x.strip()]
    qs = questions(args.pack, with_actions=args.with_actions)

    async with httpx.AsyncClient(base_url=args.base_url, timeout=10) as c:
        ready = await c.get("/readyz")
    readiness = ready.json()
    print(f"/readyz → {ready.status_code} {readiness['status']}")
    if readiness["status"] == "unavailable":
        print(json.dumps(readiness, ensure_ascii=False, indent=2))
        return 1

    result: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": args.base_url,
        "readiness": readiness,
        "rounds_per_user": args.rounds,
        "questions": len(qs),
        "with_actions": args.with_actions,
        "steps": [],
    }
    for users in steps:
        print(f"동시 사용자 {users}명 × {args.rounds}문항")
        t0 = time.perf_counter()
        batches = await asyncio.gather(
            *(
                virtual_user(
                    args.base_url,
                    args.username,
                    password,
                    users,
                    i,
                    qs,
                    args.rounds,
                    args.timeout,
                )
                for i in range(users)
            )
        )
        samples = [s for b in batches for s in b]
        wall = time.perf_counter() - t0
        summary = summarize(samples)
        summary["users"] = users
        summary["wall_s"] = round(wall, 1)
        summary["throughput_per_min"] = round(len(samples) / wall * 60, 1)
        print(json.dumps(summary, ensure_ascii=False))
        result["steps"].append({**summary, "samples": [asdict(s) for s in samples]})

    out = args.out or ROOT / "docs/adr/data" / f"load-{datetime.now():%Y%m%d-%H%M}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"결과: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
