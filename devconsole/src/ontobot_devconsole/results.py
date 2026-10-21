"""작업 출력에서 결과 뽑기 — pytest 요약, 평가 게이트 표, 실패 문항. 순수 함수."""

from __future__ import annotations

import json
import re
from typing import Any

_PYTEST_SUMMARY = re.compile(r"=*\s*(?P<body>(?:\d+ \w+(?:, )?)+) in [\d.]+s")
_PYTEST_COUNT = re.compile(r"(\d+) (passed|failed|errors?|skipped|deselected|xfailed|xpassed)")
_FAILED = re.compile(r"^(?:FAILED|ERROR) (\S+)")
_GATE_LINE = re.compile(
    r"^\s*(통과|실패) \[(invariant|threshold)\] (\S+) (min|max) ([\d.]+) — 값 (\S+)"
)
_GATE_RESULT = re.compile(r"^게이트: (통과|실패)")
_EVAL_ITEM = re.compile(r"^\s*\[(O|X)\] (\S+)\s+(\S+)")


def pytest_summary(lines: list[str]) -> dict[str, Any] | None:
    """출력의 pytest 요약 줄들(작업 하나에 여러 번 — 백엔드·콘솔)을 더한다."""
    totals: dict[str, int] = {}
    failed: list[str] = []
    seen = False
    for line in lines:
        m = _PYTEST_SUMMARY.search(line)
        if m and ("passed" in line or "failed" in line or "error" in line):
            seen = True
            for n, kind in _PYTEST_COUNT.findall(m.group("body")):
                key = "errors" if kind.startswith("error") else kind
                totals[key] = totals.get(key, 0) + int(n)
        f = _FAILED.match(line)
        if f:
            failed.append(f.group(1))
    if not seen:
        return None
    return {**totals, "failed_tests": failed[:50]}


def eval_summary(lines: list[str]) -> dict[str, Any] | None:
    checks = []
    gate: bool | None = None
    items: list[dict[str, str]] = []
    misses: int | None = None
    for line in lines:
        m = _GATE_LINE.match(line)
        if m:
            ok, kind, metric, op, limit, value = m.groups()
            checks.append(
                {
                    "metric": metric,
                    "kind": kind,
                    "op": op,
                    "limit": limit,
                    "value": value,
                    "ok": ok == "통과",
                }
            )
            continue
        g = _GATE_RESULT.match(line)
        if g:
            gate = g.group(1) == "통과"
            continue
        it = _EVAL_ITEM.match(line)
        if it:
            items.append({"key": it.group(2), "ok": str(it.group(1) == "O"), "result": it.group(3)})
            continue
        if '"cassette_misses"' in line:
            try:
                misses = int(line.split(":")[1].strip().rstrip(","))
            except (IndexError, ValueError):
                pass
    if gate is None and not checks:
        return None
    return {
        "gate_passed": gate,
        "checks": checks,
        "failed_items": [i["key"] for i in items if i["ok"] == "False"],
        "items": len(items),
        "cassette_misses": misses,
    }


def summarize(kind: str, lines: list[str]) -> dict[str, Any] | None:
    """작업 종류에 맞는 결과. 모르면 None."""
    if kind.startswith("task:eval") and kind != "task:eval-prepare":
        return eval_summary(lines)
    if kind.startswith("task:") or kind == "check-all":
        return pytest_summary(lines)
    return None


def last_json(lines: list[str]) -> dict[str, Any] | None:
    for line in reversed(lines):
        if line.startswith("{"):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
    return None
