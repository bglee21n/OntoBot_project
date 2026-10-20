"""면담자(TRD 12절) — 답이 주제에 충분한지 평가하고, 모호하면 되묻는 문장을 쓴다.

다음 질문을 **고르는** 일은 ``workshop.topics`` 의 상태기계가 한다(결정론적). 이 모듈은 판단 재료
(``sufficient``/``vague``)와 되묻는 문장만 낸다. 평가가 실패하면 호출부가 요구 요소만으로 판정한다.
"""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from ontobot.llm.types import Message
from ontobot.workshop.topics import Topic

ROLE = "interviewer"


class Assessment(BaseModel):
    verdict: Literal["sufficient", "vague"]
    follow_up: str = ""


class StructuredLLM(Protocol):
    async def structured(
        self, role: str, messages: list[Message], schema: type[Assessment]
    ) -> Assessment: ...


SYSTEM = """너는 업무 인터뷰를 진행하는 면담자다. 담당자의 답이 지금 주제에 대해 충분한지 판단한다.

- 답이 주제에 대해 구체적인 대상·항목·절차를 말하면 "sufficient".
- 답이 너무 짧거나("그냥 해요", "여러 가지요"), 주제와 무관하거나, 무엇을 가리키는지 알 수 없으면 "vague".
- "vague" 이면 follow_up 에 되묻는 질문 한 문장을 존댓말로 쓴다. 답에 없는 사실을 단정하지 않는다.
- "sufficient" 이면 follow_up 은 빈 문자열이다.

예시:
<topic>사용 데이터</topic><answer>이것저것 봐요.</answer>
{"verdict":"vague","follow_up":"주로 어떤 항목을 확인하시는지 하나만 예로 들어 주시겠어요?"}
"""


def messages(topic: Topic, question: str, answer: str) -> list[Message]:
    user = (
        f"<topic>{topic.title}</topic>\n<question>{question}</question>\n<answer>{answer}</answer>"
    )
    return [Message("system", SYSTEM), Message("user", user)]


async def assess(llm: StructuredLLM, topic: Topic, question: str, answer: str) -> Assessment:
    return await llm.structured(ROLE, messages(topic, question, answer), Assessment)
