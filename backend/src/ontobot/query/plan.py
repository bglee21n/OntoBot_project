"""QueryPlan(TRD 7.2) — 플래너 LLM 이 내놓는 구조. 검증 전에는 아무것도 믿지 않는다.

- 연산자는 고정 집합이다. 표현식은 받지 않는다.
- ``traverse`` 는 대상(target)을 **좁히는** 조건 하나의 뜻으로만 쓴다. 답이 되는 타입은 언제나
  ``target`` 이다. 항목 하나 = "대상에서 이 링크로 한 번 건너간 객체가 조건을 만족한다"이고,
  항목끼리는 AND 다(TRD 7.2). 여러 홉을 잇는 경로는 아직 표현하지 않는다.
- ``context_refs`` 는 대화 맥락(직전 답의 객체)을 가리킨다. 맥락에 없는 식별자는 검증기가 거부한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Op = Literal["eq", "ne", "gt", "gte", "lt", "lte", "in", "contains", "between", "is_null"]
Scalar = str | int | float | bool | None
AggFn = Literal["count", "sum", "avg", "min", "max"]

MAX_LIMIT = 100
MAX_TRAVERSE = 3
DEFAULT_LIMIT = 20
# 집계 결과를 정렬할 때 쓰는 예약 이름(그룹 열이 아니라 집계 값 자체).
AGG_VALUE = "value"


class _Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Filter(_Plan):
    property: str
    op: Op
    value: Scalar | list[Scalar] = None


class Traverse(_Plan):
    link: str
    direction: Literal["out", "in"] = "out"
    to: str
    filters: list[Filter] = Field(default_factory=list)


class ContextRef(_Plan):
    type: str
    pk: str


class Aggregate(_Plan):
    fn: AggFn
    property: str | None = None
    group_by: list[str] = Field(default_factory=list)


class Sort(_Plan):
    property: str
    dir: Literal["asc", "desc"] = "asc"


class QueryPlan(_Plan):
    target: str
    filters: list[Filter] = Field(default_factory=list)
    traverse: list[Traverse] = Field(default_factory=list)
    context_refs: list[ContextRef] = Field(default_factory=list)
    aggregate: Aggregate | None = None
    sort: list[Sort] = Field(default_factory=list)
    limit: int | None = None


class PlannerOutput(_Plan):
    """질의가 아니면(인사·잡담) ``not_query``, 스키마로 표현할 수 없으면 ``cannot_answer``.

    ``cannot_answer`` 는 정직한 출구다. 그것이 없으면 모델은 표현할 수 없는 조건을 **빼고**
    계획을 낸다 — 스파이크에서 "서울 지역 시설"이 지역 조건 없이 전 지역 시설 20건으로 나갔다.
    파이프라인은 이것을 ``failed(plan_invalid)`` 로 밝힌다(TRD 7.3).
    """

    kind: Literal["query", "not_query", "cannot_answer"]
    plan: QueryPlan | None = None
    reason: str | None = None
