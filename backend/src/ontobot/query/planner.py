"""플래너 — 질문을 QueryPlan 으로(TRD 7.1 2단계).

LLM 은 SQL 도 Cypher 도 쓰지 않는다(TRD 5.3). 권한으로 거른 스키마 요약만 보고 계획 JSON 을 낸다.
예시는 샘플 도메인 팩과 **다른 가상의 타입**으로 든다 — 평가 대상 스키마로 예시를 들면 모델이
예시를 베끼는 것과 스키마를 이해하는 것을 구분할 수 없다.

Action 규칙(``ACTION_RULES``)은 요약에 ``action_types`` 가 있을 때만 붙인다 — Action 이 없는 배포의
프롬프트는 측정한 그대로다. 예시의 키 순서는 ``PlannerOutput``·``ActionProposal`` 필드 순서와 같다
(구조화 출력은 스키마 순서대로 생성된다 — 순서가 어긋나면 앞 필드를 비운 채 지나간다).

Function 도 같다. 요약에 ``functions`` 가 있을 때만 ``FUNCTION_RULES`` 를 붙이고 구조화 출력 스키마를
``FunctionPlannerOutput``(``kind: function``)으로 바꾼다. 없으면 스키마·프롬프트 모두 측정한 그대로다.
문서(Phase 16d)도 같다. 요약에 ``documents``(읽을 수 있는 색인된 문서 제목)가 있을 때만 ``DOCUMENT_RULES`` 를
붙이고 스키마에 ``kind: document`` 를 더한다. 호출부는 언제나 ``FullPlannerOutput`` 을 받는다.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from pydantic import BaseModel

from ontobot.llm.types import Message
from ontobot.query.plan import (
    AGG_VALUE,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MAX_TRAVERSE,
    DocumentPlannerOutput,
    FullPlannerOutput,
    FunctionPlannerOutput,
    PlannerOutput,
)


class StructuredLLM(Protocol):
    async def structured[M: BaseModel](
        self, role: str, messages: list[Message], schema: type[M]
    ) -> M: ...


SYSTEM = f"""너는 사용자 질문을 데이터 조회 계획(JSON)으로 바꾸는 플래너다.

규칙:
- 아래 <schema> 에 있는 객체 타입·속성 이름(name)만 쓴다. 이름을 지어내지 않는다.
- 답이 되는 타입을 target 에 둔다. 조건은 filters 에 둔다.
- aggregate 는 질문이 개수·합계·평균·최댓값·최솟값을 물을 때만 쓴다. 목록·이름·속성 값을 묻는 질문에는 aggregate 를 쓰지 않는다(null).
- 식별자(pk) 속성이나 다른 타입을 가리키는 코드 속성(refers_to 가 있는 속성)에는 질문에 그 코드가 그대로 있을 때만 조건을 건다.
  이름을 코드 자리에 넣지 않는다. 다른 타입의 이름으로 좁힐 때는 refers_to 에 적힌 링크로 traverse 한다.
- 연산자: eq ne gt gte lt lte in contains between is_null. 표현식·SQL 을 쓰지 않는다.
- 값 형식: int·decimal 은 숫자, date 는 YYYY-MM-DD, datetime 은 ISO 8601, enum 은 목록의 값 그대로.
  in 은 값 목록, between 은 [하한, 상한].
- "~이전"은 lt, "~이후"는 gt, "~이상"은 gte, "~이하"는 lte, "~넘는"·"초과"는 gt, "미만"은 lt.
- 집계 결과를 정렬하려면 sort.property 에 "{AGG_VALUE}" 를 쓴다.
- 다른 타입의 객체(이름 등)로 대상을 좁힐 때는 traverse 를 쓴다. 항목 하나 = 대상에서 <schema> 의 링크 하나를
  건너간 객체에 거는 조건이다. 대상이 링크의 from 이면 direction "out", to 이면 "in". to 에는 건너간 쪽 타입을 쓴다.
  traverse 의 filters 는 건너간 쪽 타입의 속성이다. 항목은 최대 {MAX_TRAVERSE}개이고 모두 만족해야 한다(AND).
- 질문이 "그 시설", "이 중", "위 결과"처럼 직전 답을 가리키면 <context> 의 객체를 context_refs 에 {{"type","pk"}} 로 넣는다.
  <context> 에 없는 식별자를 지어내지 않는다. context_refs 는 범위를 좁힐 뿐이다 — "그 중 위험등급 4 이상"이면
  context_refs 와 함께 위험등급 조건을 filters 에 반드시 넣는다. 질문의 조건을 하나도 빼지 않는다.
- 목록 질문의 limit 은 질문이 말한 개수, 없으면 {DEFAULT_LIMIT}. 최대 {MAX_LIMIT}.
- 질문의 조건을 스키마의 속성으로 표현할 수 없으면 그 조건을 **빼고 계획을 내지 않는다**.
  대신 {{"kind": "cannot_answer", "reason": "표현할 수 없는 조건"}} 을 낸다. 조건을 빼면 다른 답이 된다.
- 인사·잡담처럼 데이터 조회가 아닌 말이면 {{"kind": "not_query"}} 를 낸다.

예시(가상의 스키마 — Ticket 타입, priority:int, status:enum[open,closed], opened_on:date, team_id:string /
Team 타입, name:string / 링크 assigned_to: Ticket→Team):
질문: "우선순위 4 이상인 열린 티켓 몇 개야?"
{{"kind":"query","plan":{{"target":"Ticket","filters":[{{"property":"priority","op":"gte","value":4}},
{{"property":"status","op":"eq","value":"open"}}],"aggregate":{{"fn":"count","property":null,"group_by":[]}}}}}}
질문: "상태별 티켓 수"
{{"kind":"query","plan":{{"target":"Ticket","aggregate":{{"fn":"count","property":null,"group_by":["status"]}},
"sort":[{{"property":"{AGG_VALUE}","dir":"desc"}}]}}}}
질문: "우선순위가 높은 순으로 열린 티켓 10개 보여줘"
{{"kind":"query","plan":{{"target":"Ticket","filters":[{{"property":"status","op":"eq","value":"open"}}],
"aggregate":null,"sort":[{{"property":"priority","dir":"desc"}}],"limit":10}}}}
질문: "결제팀에 배정된 열린 티켓 목록"
{{"kind":"query","plan":{{"target":"Ticket","filters":[{{"property":"status","op":"eq","value":"open"}}],
"traverse":[{{"link":"assigned_to","direction":"out","to":"Team","filters":[{{"property":"name","op":"eq","value":"결제팀"}}]}}]}}}}
질문: "우선순위 5 티켓이 배정된 팀은?"
{{"kind":"query","plan":{{"target":"Team","traverse":[{{"link":"assigned_to","direction":"in","to":"Ticket",
"filters":[{{"property":"priority","op":"eq","value":5}}]}}]}}}}
질문(<context> 에 Ticket/T-7, Ticket/T-9 가 있을 때): "그 중 열린 것만"
{{"kind":"query","plan":{{"target":"Ticket","filters":[{{"property":"status","op":"eq","value":"open"}}],
"context_refs":[{{"type":"Ticket","pk":"T-7"}},{{"type":"Ticket","pk":"T-9"}}]}}}}
질문: "고마워!"
{{"kind":"not_query"}}
"""


ACTION_RULES = """
Action(데이터를 바꾸거나 요청·알림을 보내는 일):
- 질문이 <schema> 의 action_types 중 하나를 해 달라는 말(바꿔줘·올려줘·내려줘·요청해줘·보내줘)이면
  kind 를 "action" 으로 하고 action 에 action_type·target·params 를 넣는다(plan 은 쓰지 않는다).
  무엇을 묻는 조회 질문에는 action 을 쓰지 않는다.
- action_type 은 action_types 의 name 그대로 쓴다. 맞는 Action 이 없으면 cannot_answer 를 낸다.
- target 은 대상 객체를 가리키는 말을 질문에 적힌 그대로(식별자나 이름) 쓴다. "그 시설"처럼 직전 답을 가리키면
  <context> 의 pk 를 쓴다. 질문과 <context> 에 없는 대상을 지어내지 않는다. 대상을 알 수 없으면 null.
- params 의 name 은 그 Action 의 params 이름이다. value 는 질문에 있는 값만 넣는다(int·decimal 은 숫자,
  date 는 YYYY-MM-DD, enum 은 목록의 값 그대로). 질문에 없는 값은 넣지 않는다 — 지어내지 않는다.
- Action 은 여기서 실행되지 않는다. 사용자가 미리보기를 보고 승인한다.

예시(가상의 Action — escalate_ticket: 대상 Ticket, params priority:int(필수)·note:string(선택) /
close_ticket: 대상 Ticket, params resolution:enum[fixed,wontfix](필수)):
질문: "T-12 티켓 우선순위를 5로 올려줘"
{"kind":"action","action":{"action_type":"escalate_ticket","target":"T-12",
"params":[{"name":"priority","value":5}]}}
질문: "로그인 오류 티켓은 wontfix 로 닫아줘"
{"kind":"action","action":{"action_type":"close_ticket","target":"로그인 오류",
"params":[{"name":"resolution","value":"wontfix"}]}}
질문(<context> 에 Ticket/T-7 이 있을 때): "그 티켓 우선순위 바꿔줘"
{"kind":"action","action":{"action_type":"escalate_ticket","target":"T-7","params":[]}}
"""


FUNCTION_RULES = """
Function(정의된 계산 — 비율·지표처럼 조회 하나로 나오지 않는 값):
- 질문이 <schema> 의 functions 중 하나가 계산하는 값을 물으면 kind 를 "function" 으로 하고 function 에 name·args 를
  넣는다(plan 은 쓰지 않는다). 목록·개수·합계·평균처럼 plan 하나로 표현되는 질문에는 function 을 쓰지 않는다.
- name 은 functions 의 name 그대로 쓴다. args 의 name 은 그 Function 의 params 이름이다.
  value 는 질문에 있는 값만 넣는다(int·decimal 은 숫자, enum 은 목록의 값 그대로). 질문에 없는 선택 인자는
  넣지 않는다 — 기본값(default)이 쓰인다. 필수 인자의 값이 질문에 없으면 cannot_answer 를 낸다.
- function 을 고르기 전에 질문의 조건(지역·조직·이름·기간·유형 등)을 모두 찾고, 조건마다 그 Function 의 params 중
  받을 것이 있는지 본다. 하나라도 받을 params 가 없으면 그 Function 을 쓰지 않는다 — 조건을 빼고 부르면 다른 질문의
  답이 된다. 그 조건을 받는 다른 Function 도, plan 으로도 표현할 수 없으면 cannot_answer 를 낸다.

예시(가상의 Function — close_rate: 팀별 종료 비율, params min_priority:int(선택, 기본 1) /
team_backlog: 팀 미처리 지표, params team:string(필수)):
질문: "팀별 티켓 종료 비율 알려줘"
{"kind":"function","function":{"name":"close_rate","args":[]}}
질문: "우선순위 3 이상 티켓의 팀별 종료 비율은?"
{"kind":"function","function":{"name":"close_rate","args":[{"name":"min_priority","value":3}]}}
질문: "결제팀 미처리 지표 보여줘"
{"kind":"function","function":{"name":"team_backlog","args":[{"name":"team","value":"결제팀"}]}}
질문: "지난달 팀별 티켓 종료 비율은?"
{"kind":"cannot_answer","reason":"close_rate 에 기간 인자가 없음"}
질문: "부산 사무소 티켓의 팀별 종료 비율은?"
{"kind":"cannot_answer","reason":"close_rate 에 사무소 인자가 없음"}
"""


DOCUMENT_RULES = """
문서(<schema> 의 documents — 업무 기준·지침·절차·계획 문서의 제목):
- 질문이 규정·기준·절차·주기·기한·방법처럼 문서에 적힌 내용을 묻고, 객체 타입의 속성으로는 답할 수 없으면
  {"kind":"document"} 를 낸다(plan 은 쓰지 않는다). 문서 검색은 질문 문장 그대로 한다.
- 객체의 목록·개수·속성 값(위험등급 값, 설치일 등)을 묻는 질문은 document 가 아니라 query 다.
- 문서에도 스키마에도 없을 것 같은 내용이라도 문서 내용을 묻는 말이면 document 를 낸다 — 답이 없으면 답변 단계가 없다고 말한다.

예시(가상의 문서 "티켓 처리 지침"이 있을 때):
질문: "긴급 티켓은 몇 시간 안에 답해야 해?"
{"kind":"document"}
질문: "티켓을 닫기 전에 무엇을 확인하나요?"
{"kind":"document"}
질문: "우선순위 5 티켓 몇 개야?"
{"kind":"query","plan":{"target":"Ticket","filters":[{"property":"priority","op":"eq","value":5}],
"aggregate":{"fn":"count","property":null,"group_by":[]}}}
"""


def _context_block(context: list[dict[str, str]]) -> str:
    lines = [f"{c['type']}/{c['pk']} — {c.get('title') or c['pk']}" for c in context]
    return "<context>\n직전 답의 객체(type/pk — 제목):\n" + "\n".join(lines) + "\n</context>"


def messages(
    summary: dict[str, Any],
    question: str,
    errors: list[str] | None = None,
    context: list[dict[str, str]] | None = None,
) -> list[Message]:
    schema = json.dumps(summary, ensure_ascii=False)
    system = SYSTEM + (ACTION_RULES if summary.get("action_types") else "")
    system += FUNCTION_RULES if summary.get("functions") else ""
    system += DOCUMENT_RULES if summary.get("documents") else ""
    system += f"\n<schema>\n{schema}\n</schema>"
    if context:
        # 맥락은 식별자와 (지금 다시 읽은) 제목뿐이다 — 값은 조회 단계에서 다시 읽는다(TRD 7.1 1단계).
        system += "\n" + _context_block(context)
    msgs = [Message("system", system), Message("user", question)]
    if errors:
        msgs.append(
            Message(
                "user",
                "직전 계획이 검증에 실패했습니다. 오류를 고친 계획을 다시 내세요.\n- "
                + "\n- ".join(errors),
            )
        )
    return msgs


async def plan(
    llm: StructuredLLM,
    summary: dict[str, Any],
    question: str,
    errors: list[str] | None = None,
    context: list[dict[str, str]] | None = None,
) -> FullPlannerOutput:
    msgs = messages(summary, question, errors, context)
    functions, documents = bool(summary.get("functions")), bool(summary.get("documents"))
    schemas: dict[tuple[bool, bool], type[BaseModel]] = {
        (False, False): PlannerOutput,
        (True, False): FunctionPlannerOutput,
        (False, True): DocumentPlannerOutput,
        (True, True): FullPlannerOutput,
    }
    schema = schemas[(functions, documents)]
    return FullPlannerOutput.lift(await llm.structured("planner", msgs, schema))
