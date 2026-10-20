"""요소 추출(TRD 12절) — 면담자의 답 하나에서 객체·속성·링크·Action·질문 후보를 구조화 출력으로 받는다.

- 입력은 **이번 답 하나**다. 근거 ``{utterance_id, quote}`` 의 발언 id 는 서버가 붙인다 — 모델은 그 답에서
  문장을 그대로 베낀 ``quote`` 만 낸다. 여러 발언을 주고 id 를 고르게 하면 작은 모델이 id 를 틀린다.
- 각 항목의 첫 필드가 ``quote`` 다. 구조화 출력은 스키마 순서대로 생성되므로 근거를 먼저 베끼고
  이름을 붙이게 된다. 예시의 키 순서도 같아야 한다(플래너에서 순서가 어긋나 필드를 비운 실측, ADR-0004).
- 이름 형식은 여기서 강제하지 않는다. 형식이 틀린 후보도 남겨 두고 확정 때 엄격히 검사한다
  (``workshop.elements``). 반쯤 맞는 출력을 고쳐 쓰지 않는다.
- 발언은 이미 마스킹돼 있다(``security.pii.mask_text``) — 모델·호출 기록·근거 모두 같은 마스킹 문장을 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from ontobot.llm.types import Message
from ontobot.workshop.evidence import normalize
from ontobot.workshop.topics import Topic

ROLE = "extractor"


class ObjectTypeCand(BaseModel):
    quote: str
    api_name: str
    display_name: str
    description: str = ""


class PropertyCand(BaseModel):
    quote: str
    object_type: str
    api_name: str
    display_name: str
    data_type: Literal["string", "int", "decimal", "bool", "date", "datetime", "enum"]
    enum_values: list[str] = Field(default_factory=list)
    description: str = ""


class LinkTypeCand(BaseModel):
    quote: str
    api_name: str
    display_name: str
    from_type: str
    to_type: str
    cardinality: Literal["one", "many"] = "many"


class ActionCand(BaseModel):
    quote: str
    name: str
    description: str = ""


class QuestionCand(BaseModel):
    quote: str
    question: str
    expected_answer: str = ""


class Extraction(BaseModel):
    object_types: list[ObjectTypeCand] = Field(default_factory=list)
    properties: list[PropertyCand] = Field(default_factory=list)
    link_types: list[LinkTypeCand] = Field(default_factory=list)
    actions: list[ActionCand] = Field(default_factory=list)
    questions: list[QuestionCand] = Field(default_factory=list)


class StructuredLLM(Protocol):
    async def structured(
        self, role: str, messages: list[Message], schema: type[Extraction]
    ) -> Extraction: ...


SYSTEM = """너는 업무 인터뷰의 답에서 데이터 모델 요소를 뽑는 분석가다. 답에 실제로 나온 것만 뽑는다.

뽑는 것:
- object_types: 업무에서 이름으로 불리며 여러 개가 있는 대상(시설·장비·요청 등).
  api_name 은 영어 단수 PascalCase(예: Facility), display_name 은 답에 나온 한국어 이름.
- properties: 대상마다 확인하는 항목. object_type 에는 그 대상의 api_name 을 쓴다(<known> 또는 이번에 뽑은 것).
  api_name 은 영어 snake_case(예: risk_grade), data_type 은 string int decimal bool date datetime enum 중 하나.
  답이 값의 목록("A, B, C 중 하나")을 말하면 data_type 은 enum, enum_values 에 그 값들을 답에 나온 그대로 적는다.
  그 밖에는 enum_values 는 빈 목록이다. 등급·개수·용량처럼 수로 비교하는 항목은 int 나 decimal, 날짜는 date 다.
- link_types: 두 대상 사이의 관계(예: 시설은 조직이 소유한다 → owned_by, from_type Facility, to_type Organization).
  api_name 은 영어 snake_case.
- actions: 담당자가 대상에 대해 하는 조치(예: 점검 요청, 사용 중지). name 은 한국어.
- questions: 담당자가 실제로 찾아보는 질문. 물음표가 없어도 "~을 자주 본다"처럼 찾아보는 내용을 말하면 하나씩 뽑는다.
  question 은 질문 문장(말한 질문이면 그대로, 아니면 질문 문장으로 바꿔 쓴다), expected_answer 는 답에서 기대 답을
  말했을 때만 적고 아니면 빈 문자열. quote 는 언제나 답에서 그대로 베낀다.

규칙:
- quote 는 <answer> 에서 그 요소의 근거가 되는 부분을 **한 글자도 바꾸지 않고** 그대로 베낀다. 요약하지 않는다.
- <answer> 에 근거가 없는 요소는 만들지 않는다. 일반 상식으로 채우지 않는다. 없으면 빈 목록이다.
- 인터뷰어의 질문(<question>)에만 나온 말은 근거가 아니다.

예시(평가 도메인과 다른 가상의 업무 — 예시를 베끼는지 이해하는지 가리려고 다른 도메인을 쓴다):
<answer>민원을 부서별로 배정하고, 민원마다 접수일과 처리 상태를 봅니다. 상태는 접수, 처리 중, 완료 중 하나예요. 기한이 지나면 독촉 알림을 보냅니다. 부서별 미처리 민원 목록도 자주 봐요.</answer>
{"object_types":[{"quote":"민원을 부서별로 배정하고","api_name":"Complaint","display_name":"민원","description":"접수된 민원"},
{"quote":"민원을 부서별로 배정하고","api_name":"Department","display_name":"부서","description":"민원을 맡는 부서"}],
"properties":[{"quote":"민원마다 접수일과 처리 상태를 봅니다","object_type":"Complaint","api_name":"received_on","display_name":"접수일","data_type":"date","enum_values":[],"description":""},
{"quote":"상태는 접수, 처리 중, 완료 중 하나예요","object_type":"Complaint","api_name":"status","display_name":"처리 상태","data_type":"enum","enum_values":["접수","처리 중","완료"],"description":""}],
"link_types":[{"quote":"민원을 부서별로 배정하고","api_name":"assigned_to","display_name":"담당 부서","from_type":"Complaint","to_type":"Department","cardinality":"one"}],
"actions":[{"quote":"기한이 지나면 독촉 알림을 보냅니다","name":"독촉 알림","description":"처리 기한이 지났을 때"}],
"questions":[{"quote":"부서별 미처리 민원 목록도 자주 봐요","question":"부서별 미처리 민원 목록 보여줘","expected_answer":""}]}
"""


def messages(topic: Topic, question: str, answer: str, known_types: list[str]) -> list[Message]:
    known = "\n".join(f"- {t}" for t in known_types) or "(없음)"
    user = (
        f"<topic>{topic.title}</topic>\n<known>\n{known}\n</known>\n"
        f"<question>{question}</question>\n<answer>{answer}</answer>"
    )
    return [Message("system", SYSTEM), Message("user", user)]


@dataclass(frozen=True)
class Candidate:
    kind: str
    payload: dict[str, Any]
    quote: str


def candidates(out: Extraction) -> list[Candidate]:
    """구조화 출력 → 종류별 payload(``workshop.elements`` 의 필드 이름)."""
    found: list[Candidate] = []
    for t in out.object_types:
        found.append(
            Candidate(
                "object_type",
                {
                    "api_name": t.api_name,
                    "display_name": t.display_name,
                    "description": t.description or None,
                },
                t.quote,
            )
        )
    for p in out.properties:
        found.append(
            Candidate(
                "property",
                {
                    "object_type": p.object_type,
                    "api_name": p.api_name,
                    "display_name": p.display_name,
                    "data_type": p.data_type,
                    "enum_values": p.enum_values or None,
                    "description": p.description or None,
                },
                p.quote,
            )
        )
    for lt in out.link_types:
        found.append(
            Candidate(
                "link_type",
                {
                    "api_name": lt.api_name,
                    "display_name": lt.display_name,
                    "from_type": lt.from_type,
                    "to_type": lt.to_type,
                    "cardinality": lt.cardinality,
                },
                lt.quote,
            )
        )
    for a in out.actions:
        found.append(
            Candidate(
                "action_candidate", {"name": a.name, "description": a.description or None}, a.quote
            )
        )
    for q in out.questions:
        found.append(
            Candidate(
                "golden_query",
                {
                    "question": q.question,
                    "expected_answer": q.expected_answer or None,
                    "answerable": True,
                },
                q.quote,
            )
        )
    return found


# 골든 질의는 "평소 찾아보는 질문" 주제에서만 받는다. 다른 주제에서는 모델이 면담자의 질문을
# 질문 후보로 베꼈다(Phase 6 실측 — quote 는 답에서 맞게 베껴 근거 확인으로는 못 잡는다).
GOLDEN_TOPIC = "queries"


def keep(c: Candidate, topic: Topic, agent_question: str) -> bool:
    """결정론적 거르기. 인터뷰 에이전트의 질문은 평가셋이 아니다(PRD 5.8)."""
    if c.kind != "golden_query":
        return True
    if topic.key != GOLDEN_TOPIC:
        return False
    q = normalize(str(c.payload.get("question") or ""))
    asked = normalize(agent_question)
    return bool(q) and q not in asked and asked not in q


async def extract(
    llm: StructuredLLM, topic: Topic, question: str, answer: str, known_types: list[str]
) -> list[Candidate]:
    out = await llm.structured(ROLE, messages(topic, question, answer, known_types), Extraction)
    return [c for c in candidates(out) if keep(c, topic, question)]
