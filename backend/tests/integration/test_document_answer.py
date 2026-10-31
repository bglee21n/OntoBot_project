"""Phase 16d — 문서 질문: 플래너 kind=document → 그룹 안 벡터 검색 → 답변 → 청크 원문 대조.

임베딩·플래너·답변은 가짜 Ollama 다. 임베딩은 낱말로 정한 3차원 벡터(결정적).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from ontobot.chat import pipeline, templates
from ontobot.db.session import session_scope
from ontobot.documents import store
from ontobot.documents.indexing import embed_document
from ontobot.llm.adapters import ollama
from ontobot.llm.config import LlmConfig
from ontobot.llm.gateway import LLMGateway
from ontobot.ontology import registry
from ontobot.security import admin
from tests.integration.conftest import MakeUser

pytestmark = pytest.mark.integration

CFG = LlmConfig.model_validate(
    {
        "providers": {"local": {"adapter": "ollama", "base_url": "http://fake-ollama"}},
        "roles": {
            "planner": {"provider": "local", "model": "fake"},
            "answerer": {"provider": "local", "model": "fake"},
            "embedder": {"provider": "local", "model": "fake-embed"},
        },
    }
)


def _vec(t: str) -> list[float]:
    return [1.0 if "점검" in t else 0.0, 1.0 if "교육" in t else 0.0, 0.1]


class Fake:
    def __init__(self, planner: dict[str, Any], answer: str) -> None:
        self.planner, self.answer = planner, answer
        self.planner_requests: list[dict[str, Any]] = []
        self.answer_requests: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path == "/api/embed":
            return httpx.Response(200, json={"embeddings": [_vec(t) for t in body["input"]]})
        if "format" in body:
            self.planner_requests.append(body)
            content = json.dumps(self.planner, ensure_ascii=False)
        else:
            self.answer_requests.append(body)
            content = self.answer
        return httpx.Response(200, json={"message": {"content": content}})


async def _docs(make_user: MakeUser) -> tuple[int, int, int, str]:
    """그룹 A: 점검 문서·교육 문서, 그룹 B: 점검 문서(보는 사람이 속하지 않는다)."""
    _, _, uid = await make_user("modeler")
    tag = uuid.uuid4().hex[:8]
    async with session_scope() as db:
        ga = await admin.create_group(db, actor_id=None, name=f"da_{tag}", display_name="A")
        gb = await admin.create_group(db, actor_id=None, name=f"db_{tag}", display_name="B")
        inspect = await store.create(
            db,
            title=f"점검 기준 {tag}",
            filename="a.md",
            data=f"# 점검 기준 {tag}\n\n위험등급 3 시설은 반기마다 1회 정기 점검한다.".encode(),
            group_id=ga.id,
            actor_id=uid,
        )
        await store.create(
            db,
            title=f"교육 규정 {tag}",
            filename="b.md",
            data=f"신규 작업자는 16시간 교육을 받는다. {tag}".encode(),
            group_id=ga.id,
            actor_id=uid,
        )
        other = await store.create(
            db,
            title=f"남의 점검 문서 {tag}",
            filename="c.md",
            data=f"점검은 매월 3회 한다. {tag}".encode(),
            group_id=gb.id,
            actor_id=uid,
        )
        await db.commit()
        ids = (inspect.id, other.id, ga.id)
    return ids[0], ids[1], ids[2], tag


async def _index(monkeypatch: pytest.MonkeyPatch, fake: Fake, *doc_ids: int) -> None:
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(fake.handler))
    for d in doc_ids:
        await embed_document(d, LLMGateway(CFG))


async def _run(schema: registry.PublishedSchema, question: str, groups: set[int]) -> Any:
    async with session_scope() as db:
        return await pipeline.run(
            db, LLMGateway(CFG), schema, {"Facility"}, question, documents=groups
        )


async def test_document_question_is_searched_in_my_groups_and_grounded(
    monkeypatch: pytest.MonkeyPatch, sample_published: None, make_user: MakeUser
) -> None:
    schema = registry.current()
    assert schema is not None
    inspect_id, other_id, group_a, tag = await _docs(make_user)
    fake = Fake(
        {"kind": "document"},
        f"위험등급 3 시설은 반기마다 1회 정기 점검합니다. [[d:{inspect_id}/0]]",
    )
    async with session_scope() as db:
        ids = [inspect_id, other_id] + [
            d.id for d in await store.listing(db, {group_a}) if d.id != inspect_id
        ]
    await _index(monkeypatch, fake, *ids)

    res = await _run(schema, "위험등급 3 시설 점검 주기는?", {group_a})
    assert (res.status, res.grade) == ("ok", 1), res
    assert [c["ref"] for c in res.citations] == [f"d:{inspect_id}/0#chunk"]
    assert res.citations[0]["values"][0]["display"].endswith("반기마다 1회 정기 점검한다.")
    assert res.plan == {"document": {"query": "위험등급 3 시설 점검 주기는?"}}
    assert res.row_count == 1  # 교육 문서는 점수가 낮아 빠지고, 남의 그룹 문서는 찾지 않는다
    assert res.conditions == templates.DOCUMENT_CONDITIONS
    # 플래너는 내 그룹의 색인된 문서 제목만 본다. 답변 블록에도 남의 문서가 없다.
    system = fake.planner_requests[0]["messages"][0]["content"]
    assert f"점검 기준 {tag}" in system and f"남의 점검 문서 {tag}" not in system
    assert '"document"' in json.dumps(fake.planner_requests[0]["format"])
    block = fake.answer_requests[0]["messages"][1]["content"]
    assert f"d:{inspect_id}/0 | 점검 기준 {tag} |" in block and f"d:{other_id}/" not in block

    # 지어낸 숫자 — 청크에 "3회"가 없다(남의 문서에는 있지만 찾지 않았다) → ③ 문서 거부 문구
    fake.answer = f"위험등급 3 시설은 매월 3회 점검합니다. [[d:{inspect_id}/0]]"
    res = await _run(schema, "위험등급 3 시설 점검 주기는?", {group_a})
    assert (res.status, res.grade) == ("ok", 3)
    assert res.answer == templates.DOCUMENT_UNGROUNDED
    assert [r["reason"] for r in res.removed_citations] == ["value_mismatch"]
    # 남의 문서 청크를 인용 — 검색 결과에 없으니 unknown_ref
    fake.answer = f"점검은 매월 3회 합니다. [[d:{other_id}/0]]"
    res = await _run(schema, "점검 주기는?", {group_a})
    assert res.grade == 3 and [r["reason"] for r in res.removed_citations] == ["unknown_ref"]


async def test_no_documents_keeps_the_measured_planner_prompt(
    monkeypatch: pytest.MonkeyPatch, sample_published: None
) -> None:
    schema = registry.current()
    assert schema is not None
    fake = Fake({"kind": "not_query"}, "")
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(fake.handler))
    res = await _run(schema, "안녕", set())
    assert res.status == "not_query"
    body = fake.planner_requests[0]
    assert "documents" not in body["messages"][0]["content"]
    assert '"document"' not in json.dumps(body["format"])
    # 그룹은 있지만 색인된 문서가 없어도 같다.
    async with session_scope() as db:
        g = await admin.create_group(
            db, actor_id=None, name=f"de_{uuid.uuid4().hex[:8]}", display_name="E"
        )
        await db.commit()
    fake.planner_requests.clear()
    await _run(schema, "안녕", {g.id})
    assert '"document"' not in json.dumps(fake.planner_requests[0]["format"])
