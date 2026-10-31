"""Phase 16d — 플래너: 요약에 문서가 있을 때만 문서 규칙·kind=document (없으면 측정한 그대로)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ontobot.query import planner
from ontobot.query.plan import (
    DocumentPlannerOutput,
    FullPlannerOutput,
    FunctionPlannerOutput,
    PlannerOutput,
)


class _LLM:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.schemas: list[type[BaseModel]] = []

    async def structured[M: BaseModel](self, role: str, messages: Any, schema: type[M]) -> M:
        self.schemas.append(schema)
        return schema.model_validate(self.reply)


async def test_schema_and_rules_follow_the_summary() -> None:
    cases = [
        ({}, PlannerOutput),
        ({"functions": [{"name": "f"}]}, FunctionPlannerOutput),
        ({"documents": ["점검 기준"]}, DocumentPlannerOutput),
        ({"functions": [{"name": "f"}], "documents": ["점검 기준"]}, FullPlannerOutput),
    ]
    for extra, schema in cases:
        llm = _LLM({"kind": "not_query"})
        out = await planner.plan(llm, {"object_types": [], **extra}, "안녕")
        assert llm.schemas == [schema] and isinstance(out, FullPlannerOutput)
        system = planner.messages({"object_types": [], **extra}, "안녕")[0].content
        assert (planner.DOCUMENT_RULES in system) is ("documents" in extra)
    # 문서가 없을 때의 스키마는 kind=document 를 받지 않는다(녹화 키·측정 그대로).
    assert "document" not in PlannerOutput.model_json_schema()["properties"]["kind"]["enum"]
    llm = _LLM({"kind": "document"})
    out = await planner.plan(llm, {"object_types": [], "documents": ["점검 기준"]}, "주기는?")
    assert out.kind == "document" and out.plan is None
