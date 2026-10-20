"""Phase 6 — 인터뷰 워크숍 API 왕복(실제 DB, LLM 은 대본 가짜).

완료 기준(계획 Phase 6): 인터뷰 1회로 draft 버전과 골든 질의 초안이 만들어지고, 근거 없는 요소는
그대로 확정되지 않는다.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from ontobot.db.models.evaluation import GoldenItem
from ontobot.db.models.security import AuditLog
from ontobot.db.models.workshop import InterviewUtterance
from ontobot.db.session import session_scope
from ontobot.llm.adapters import ollama
from ontobot.ontology import versioning
from ontobot.workshop import topics
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration

API = "/api/v1/workshop"


class Script:
    """역할별 대본 — 형식(JSON 스키마)에 ``verdict`` 가 있으면 면담자, 아니면 추출기."""

    def __init__(self) -> None:
        self.extract: list[Any] = []
        self.assess: list[Any] = []
        self.down = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("down")
        body = json.loads(request.content)
        is_assess = "verdict" in body["format"].get("properties", {})
        queue = self.assess if is_assess else self.extract
        item = queue.pop(0) if queue else ({"verdict": "sufficient"} if is_assess else {})
        content = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
        return httpx.Response(200, json={"message": {"content": content}})


@pytest.fixture
def script(monkeypatch: pytest.MonkeyPatch) -> Script:
    s = Script()
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(s.handler))
    return s


async def _as(client: httpx.AsyncClient, make_user: MakeUser, *roles: str) -> int:
    username, password, uid = await make_user(*roles)
    assert (await login(client, username, password)).status_code == 200
    return uid


async def _say(client: httpx.AsyncClient, sid: int, text: str) -> dict[str, Any]:
    r = await client.post(f"{API}/sessions/{sid}/utterances", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def _uniq() -> str:
    return "W" + uuid.uuid4().hex[:8]


async def test_interview_to_draft_and_golden_items(
    client: httpx.AsyncClient, script: Script, make_user: MakeUser
) -> None:
    type_name = _uniq()  # 게시 버전과 겹치지 않는 타입 이름
    await _as(client, make_user, "domain_expert")
    r = await client.post(f"{API}/sessions", json={"title": "설비 담당 인터뷰"})
    assert r.status_code == 201, r.text
    sess = r.json()
    sid = sess["id"]
    assert sess["utterances"][0]["text"] == topics.TOPICS[0].opening
    assert sess["current_topic"] == "role"

    # 1) 역할 — 모호함 → 되묻기. 전화번호는 마스킹돼 저장된다.
    script.assess.append({"verdict": "vague", "follow_up": "어떤 시설을 관리하시나요?"})
    turn = await _say(client, sid, "이것저것 해요. 연락은 010-1234-5678 로 주세요.")
    assert (turn["decision"], turn["question"]["text"]) == (
        "follow_up",
        "어떤 시설을 관리하시나요?",
    )
    assert "010-1234-5678" not in turn["answer"]["text"]

    # 2) 역할 재답 → 업무 흐름으로
    turn = await _say(client, sid, "변전소 점검을 담당합니다.")
    assert (turn["decision"], turn["topic"]) == ("next", "workflow")

    # 3) 업무 흐름 — 타입 하나(근거 일치), 링크 하나(근거 불일치 = 바꿔 쓴 말)
    answer3 = f"매일 {type_name} 시설 목록을 보고 점검 일정을 잡아요."
    script.extract.append(
        {
            "object_types": [
                {
                    "quote": f"{type_name} 시설 목록을 보고",
                    "api_name": type_name,
                    "display_name": "시설",
                    "description": "점검 대상",
                }
            ],
            "link_types": [
                {
                    "quote": "시설은 지역에 속한다",  # 발언에 없다
                    "api_name": "located_in_x",
                    "display_name": "위치",
                    "from_type": type_name,
                    "to_type": type_name,
                    "cardinality": "one",
                }
            ],
        }
    )
    turn = await _say(client, sid, answer3)
    assert (turn["decision"], turn["topic"]) == ("next", "decisions")
    added = {e["kind"]: e for e in turn["added"]}
    assert added["object_type"]["evidence_ok"] is True
    assert added["link_type"]["evidence_ok"] is False
    assert added["object_type"]["evidence"][0]["utterance_id"] == turn["answer"]["id"]

    # 4) 의사결정 — Action 후보. 추출 출력이 두 번 깨지면 경고로 밝히고 진행한다.
    script.extract.extend(["not json", "still not json"])
    turn = await _say(client, sid, "등급이 높으면 점검을 요청합니다.")
    assert turn["warnings"] == ["extraction_failed"]
    assert turn["topic"] == "data"

    # 5) 사용 데이터 — 속성. 같은 타입을 또 말하면 새 요소 없이 근거만 붙는다.
    script.extract.append(
        {
            "object_types": [{"quote": "시설마다", "api_name": type_name, "display_name": "시설"}],
            "properties": [
                {
                    "quote": "위험등급을 봅니다",
                    "object_type": type_name,
                    "api_name": "risk_grade",
                    "display_name": "위험등급",
                    "data_type": "int",
                }
            ],
        }
    )
    turn = await _say(client, sid, "시설마다 위험등급을 봅니다.")
    assert [e["kind"] for e in turn["added"]] == ["property"]
    assert [e["kind"] for e in turn["updated"]] == ["object_type"]
    assert len(turn["updated"][0]["evidence"]) == 2

    # 6) 예외 → 7) 질문(골든 질의) → 끝
    turn = await _say(client, sid, "태풍이 오면 전 시설을 긴급 점검합니다.")
    assert turn["topic"] == "queries"
    script.extract.append(
        {
            "questions": [
                {
                    "quote": "위험등급 4 이상인 시설은 몇 개야?",
                    "question": "위험등급 4 이상인 시설은 몇 개야?",
                    "expected_answer": "",
                }
            ]
        }
    )
    turn = await _say(client, sid, "위험등급 4 이상인 시설은 몇 개야? 이걸 제일 자주 봐요.")
    assert turn["finished"] is True and turn["question"]["text"] == topics.CLOSING
    r = await client.post(f"{API}/sessions/{sid}/utterances", json={"text": "더 할 말"})
    assert (r.status_code, r.json()["code"]) == (409, "session_closed")

    # 검토
    els = (await client.get(f"{API}/sessions/{sid}/elements")).json()
    by_kind = {e["kind"]: e for e in els}
    assert set(by_kind) == {"object_type", "link_type", "property", "golden_query"}

    r = await client.post(f"{API}/sessions/{sid}/submit")
    assert (r.status_code, r.json()["code"]) == (409, "unreviewed_elements")

    link = by_kind["link_type"]
    r = await client.patch(f"{API}/elements/{link['id']}", json={"status": "confirmed"})
    assert (r.status_code, r.json()["code"]) == (409, "evidence_required")
    r = await client.patch(f"{API}/elements/{link['id']}", json={"status": "rejected"})
    assert r.json()["status"] == "rejected"

    for kind in ("object_type", "property"):
        r = await client.patch(
            f"{API}/elements/{by_kind[kind]['id']}", json={"status": "confirmed"}
        )
        assert r.status_code == 200, r.text

    gq = by_kind["golden_query"]
    r = await client.patch(f"{API}/elements/{gq['id']}", json={"status": "confirmed"})
    assert (r.status_code, r.json()["code"]) == (422, "expected_answer_required")
    edited = {**gq["payload"], "expected_answer": "7개"}
    r = await client.patch(
        f"{API}/elements/{gq['id']}", json={"status": "confirmed", "payload": edited}
    )
    assert r.json()["status"] == "edited"

    r = await client.post(f"{API}/sessions/{sid}/submit")
    assert r.status_code == 200 and r.json()["status"] == "submitted"

    # 현업은 게시하지 못한다 — 모델러가 게시한다.
    assert (await client.post(f"{API}/sessions/{sid}/publish")).status_code == 403
    await _as(client, make_user, "modeler")
    r = await client.post(f"{API}/sessions/{sid}/publish")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["added"]["object_type"] == [type_name]
    assert out["added"]["property"] == [f"{type_name}.risk_grade"]
    assert out["golden_items"] == 1

    async with session_scope() as db:
        draft = await versioning.get_version(db, out["version_id"])
        assert draft.status == "draft"
        doc = versioning.to_document(draft)
        t = doc.object_type(type_name)
        assert t is not None and [p.api_name for p in t.properties] == ["risk_grade"]
        assert all(lt.api_name != "located_in_x" for lt in doc.link_types)  # 거절한 요소
        items = list(
            await db.scalars(
                select(GoldenItem).where(GoldenItem.golden_set_id == out["golden_set_id"])
            )
        )
        assert [(i.question, i.expected_answer, i.source) for i in items] == [
            ("위험등급 4 이상인 시설은 몇 개야?", "7개", "workshop")
        ]
        stored = list(
            await db.scalars(
                select(InterviewUtterance.text).where(InterviewUtterance.session_id == sid)
            )
        )
        assert not any("010-1234-5678" in s for s in stored)
        events = list(
            await db.scalars(
                select(AuditLog.event)
                .where(AuditLog.target == f"interview_session:{sid}")
                .order_by(AuditLog.id)
            )
        )
        assert events == ["workshop.session_started", "workshop.submitted", "workshop.published"]

    # 게시 뒤에는 요소도 세션도 바뀌지 않는다.
    r = await client.patch(f"{API}/elements/{gq['id']}", json={"status": "rejected"})
    assert (r.status_code, r.json()["code"]) == (409, "element_locked")
    r = await client.post(f"{API}/sessions/{sid}/publish")
    assert (r.status_code, r.json()["code"]) == (409, "not_submitted")


async def test_sessions_are_private_and_modeler_cannot_answer(
    client: httpx.AsyncClient, script: Script, make_user: MakeUser
) -> None:
    await _as(client, make_user, "domain_expert")
    sid = (await client.post(f"{API}/sessions", json={})).json()["id"]

    await _as(client, make_user, "domain_expert")  # 다른 현업
    assert (await client.get(f"{API}/sessions/{sid}")).status_code == 404
    assert all(s["id"] != sid for s in (await client.get(f"{API}/sessions")).json())

    await _as(client, make_user, "modeler")
    assert (await client.get(f"{API}/sessions/{sid}")).status_code == 200
    r = await client.post(f"{API}/sessions/{sid}/utterances", json={"text": "대신 답"})
    assert (r.status_code, r.json()["code"]) == (403, "not_interviewee")

    await _as(client, make_user, "user")  # 워크숍 역할 없음
    assert (await client.get(f"{API}/sessions")).status_code == 403


async def test_llm_down_fails_the_turn_without_saving_the_answer(
    client: httpx.AsyncClient, script: Script, make_user: MakeUser
) -> None:
    await _as(client, make_user, "domain_expert")
    sid = (await client.post(f"{API}/sessions", json={})).json()["id"]
    script.down = True
    r = await client.post(f"{API}/sessions/{sid}/utterances", json={"text": "변전소를 관리합니다"})
    assert (r.status_code, r.json()["code"]) == (503, "llm_unavailable")
    detail = (await client.get(f"{API}/sessions/{sid}")).json()
    assert [u["speaker"] for u in detail["utterances"]] == ["agent"]
    assert detail["current_topic"] == "role"


async def test_publish_blocked_on_conflicting_property(
    client: httpx.AsyncClient, script: Script, make_user: MakeUser
) -> None:
    """확정 속성이 없는 타입을 가리키면 게시를 막고 아무것도 만들지 않는다."""
    await _as(client, make_user, "domain_expert")
    sid = (await client.post(f"{API}/sessions", json={})).json()["id"]
    await _say(client, sid, "변전소 점검 담당입니다.")
    script.extract.append(
        {
            "properties": [
                {
                    "quote": "점검일을 봐요",
                    "object_type": _uniq(),
                    "api_name": "checked_on",
                    "display_name": "점검일",
                    "data_type": "date",
                }
            ]
        }
    )
    turn = await _say(client, sid, "점검일을 봐요.")
    prop = turn["added"][0]
    assert (
        await client.patch(f"{API}/elements/{prop['id']}", json={"status": "confirmed"})
    ).status_code == 200
    assert (await client.post(f"{API}/sessions/{sid}/submit")).status_code == 200

    await _as(client, make_user, "modeler")
    r = await client.post(f"{API}/sessions/{sid}/publish")
    assert (r.status_code, r.json()["code"]) == (409, "publish_blocked")
    assert r.json()["issues"][0]["path"] == f"draft_element.{prop['id']}"
    assert (await client.get(f"{API}/sessions/{sid}")).json()["status"] == "submitted"
