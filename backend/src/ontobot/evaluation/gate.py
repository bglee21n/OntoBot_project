"""평가 게이트(TRD 13.1) — ``gate.yaml`` 의 기준으로 지표를 판정한다. 순수 함수.

불변식은 값이 없으면(분모 0 — 예: 화면에 보인 답이 없다) 위반이 없는 것으로 본다. 가안 기준은
값이 없으면 실패다 — 정답률을 잴 문항이 없다는 것은 게이트를 통과할 근거가 없다는 뜻이다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

GATE_FILE = Path(__file__).with_name("gate.yaml")


@dataclass(frozen=True)
class Check:
    metric: str
    kind: Literal["invariant", "threshold"]
    op: Literal["min", "max"]
    limit: float
    value: float | None
    passed: bool


@dataclass(frozen=True)
class GateResult:
    passed: bool
    checks: list[Check]
    skipped: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [asdict(c) for c in self.checks],
            "skipped": self.skipped,
        }


def load(path: Path = GATE_FILE) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def evaluate(
    metrics: dict[str, Any], cfg: dict[str, Any], *, skip: Iterable[str] = ()
) -> GateResult:
    skipped = sorted(set(skip))
    checks: list[Check] = []
    for kind, section in (("invariant", "invariants"), ("threshold", "thresholds")):
        for metric, rule in (cfg.get(section) or {}).items():
            if metric in skipped:
                continue
            if metric not in metrics:
                raise KeyError(f"gate.yaml 의 지표 '{metric}' 를 계산하지 않습니다")
            value = metrics[metric]
            for op in ("min", "max"):
                if op not in rule:
                    continue
                limit = float(rule[op])
                if value is None:
                    ok = kind == "invariant"
                else:
                    ok = value >= limit if op == "min" else value <= limit
                checks.append(Check(metric, kind, op, limit, value, ok))  # type: ignore[arg-type]
    return GateResult(all(c.passed for c in checks), checks, skipped)
