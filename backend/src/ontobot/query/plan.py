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


class ActionParamValue(_Plan):
    name: str
    value: Scalar = None


class ActionProposal(_Plan):
    """채팅에서 Action 을 요청하는 말(TRD 17.1-5) — 실행이 아니라 **제안**이다.

    ``target`` 은 대상 객체를 가리키는 말 그대로(질문에 적힌 식별자나 이름, 또는 맥락의 pk)다.
    서버가 그 타입의 pk·제목과 **정확히** 맞춰 본다(``actions.params``). 파라미터는 이름·값 목록이다
    — 사전(dict)으로 두면 구조화 출력 스키마가 키를 열어 두어 작은 모델이 이름을 지어낸다.
    """

    action_type: str
    target: str | None = None
    params: list[ActionParamValue] = Field(default_factory=list)


class PlannerOutput(_Plan):
    """질의가 아니면(인사·잡담) ``not_query``, 스키마로 표현할 수 없으면 ``cannot_answer``.

    ``cannot_answer`` 는 정직한 출구다. 그것이 없으면 모델은 표현할 수 없는 조건을 **빼고**
    계획을 낸다 — 스파이크에서 "서울 지역 시설"이 지역 조건 없이 전 지역 시설 20건으로 나갔다.
    파이프라인은 이것을 ``failed(plan_invalid)`` 로 밝힌다(TRD 7.3).

    데이터를 바꾸거나 요청·알림하는 말이면 ``action`` — 조회하지 않고 미리보기까지만 간다.
    """

    kind: Literal["query", "action", "not_query", "cannot_answer"]
    plan: QueryPlan | None = None
    action: ActionProposal | None = None
    reason: str | None = None


class FunctionArg(_Plan):
    name: str
    value: Scalar = None


class FunctionCall(_Plan):
    """정의된 Function 을 부르는 말(PRD 5.10).

    인자는 이름·값 목록이다(``ActionProposal`` 과 같은 이유).
    """

    name: str
    args: list[FunctionArg] = Field(default_factory=list)


class FunctionPlannerOutput(_Plan):
    """요약에 Function 이 있을 때의 플래너 출력.

    ``PlannerOutput`` 에 ``kind: function`` 을 더한 것이다.

    따로 둔 이유: Function 이 없는 배포의 구조화 출력 스키마(와 녹화 키)를 측정한 그대로 남긴다.
    필드 순서가 생성 순서다 — ``function`` 은 ``plan`` 다음이다(프롬프트 예시도 같은 순서).
    """

    kind: Literal["query", "function", "action", "not_query", "cannot_answer"]
    plan: QueryPlan | None = None
    function: FunctionCall | None = None
    action: ActionProposal | None = None
    reason: str | None = None

    @classmethod
    def lift(cls, out: PlannerOutput) -> FunctionPlannerOutput:
        return cls.model_validate(out.model_dump())


class DocumentPlannerOutput(_Plan):
    """요약에 문서(``documents``)가 있을 때의 플래너 출력 — ``kind: document`` 를 더한다(Phase 16d).

    문서 질문에는 계획이 없다. 검색은 질문 문장 그대로 한다 — 모델이 검색어를 지어 쓰게 하지 않는다.
    따로 둔 이유는 ``FunctionPlannerOutput`` 과 같다
    (문서가 없는 배포의 스키마·녹화 키를 그대로 둔다).
    """

    kind: Literal["query", "document", "action", "not_query", "cannot_answer"]
    plan: QueryPlan | None = None
    action: ActionProposal | None = None
    reason: str | None = None


class FullPlannerOutput(_Plan):
    """Function 과 문서가 모두 있을 때의 출력.

    호출부가 받는 모양이기도 하다(나머지는 여기로 옮겨 담는다).
    """

    kind: Literal["query", "function", "document", "action", "not_query", "cannot_answer"]
    plan: QueryPlan | None = None
    function: FunctionCall | None = None
    action: ActionProposal | None = None
    reason: str | None = None

    @classmethod
    def lift(cls, out: BaseModel) -> FullPlannerOutput:
        return cls.model_validate(out.model_dump())
