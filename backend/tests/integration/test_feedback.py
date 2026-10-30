"""Phase 15 — 답 피드백 → 골든 문항 후보(PRD 5.6 P1).

완료 기준(계획 Phase 15): 싫어요 → 골든 후보 → 사람이 기대값을 적어 옮긴다.
남의 답에는 남길 수 없다.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import select

from ontobot.db.models.chat import Conversation, Feedback, Message
from ontobot.db.models.evaluation import GoldenItem
from ontobot.db.models.security import AuditLog
from ontobot.db.session import session_scope
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration


async def _exchange(user_id: int, question: str) -> int:
    """질문 → 답 한 쌍을 넣고 답 메시지 id 를 돌려준다."""
    async with session_scope() as db:
        conv = Conversation(user_id=user_id, title=question)
        db.add(conv)
        await db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content=question))
        await db.flush()
        answer = Message(
            conversation_id=conv.id,
            role="assistant",
            content="파이프 시설은 없습니다.",
            status="empty",
            meta={"conditions": "시설 중 유형이 파이프"},
        )
        db.add(answer)
        await db.flush()
        answer_id = answer.id
        await db.commit()
    return answer_id


async def test_feedback_to_golden_candidate(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    question = f"파이프 시설 {uuid.uuid4().hex[:6]}"
    username, password, uid = await make_user("user")
    answer_id = await _exchange(uid, question)
    other_id = await _exchange((await make_user("user"))[2], "남의 질문")

    assert (await login(client, username, password)).status_code == 200
    r = await client.put(f"/api/v1/messages/{answer_id}/feedback", json={"rating": "up"})
    assert r.status_code == 200, r.text
    r = await client.put(
        f"/api/v1/messages/{answer_id}/feedback",
        json={"rating": "down", "comment": "있어요. 담당 010-1234-5678"},
    )
    assert r.json() == {
        "message_id": answer_id,
        "rating": "down",
        "comment": "있어요. 담당 ***",  # 의견은 마스킹 저장
    }
    # 남의 답·질문 메시지에는 남길 수 없다(없는 답과 같은 말).
    r = await client.put(f"/api/v1/messages/{other_id}/feedback", json={"rating": "down"})
    assert (r.status_code, r.json()["code"]) == (404, "message_not_found")
    r = await client.put(f"/api/v1/messages/{answer_id - 1}/feedback", json={"rating": "down"})
    assert r.status_code == 404
    async with session_scope() as db:
        conv_id = (await db.get(Message, answer_id)).conversation_id  # type: ignore[union-attr]
    msgs = (await client.get(f"/api/v1/conversations/{conv_id}/messages")).json()
    assert [m["feedback"] for m in msgs] == [None, "down"]
    assert (await client.get("/api/v1/feedback")).status_code == 403

    username, password, builder = await make_user("bot_builder")
    assert (await login(client, username, password)).status_code == 200
    listed = (await client.get("/api/v1/feedback")).json()
    mine = next(f for f in listed if f["message_id"] == answer_id)
    assert mine["question"] == question
    assert mine["answer_status"] == "empty"
    assert mine["conditions"] == "시설 중 유형이 파이프"
    assert mine["status"] == "open"

    r = await client.post("/api/v1/golden-sets", json={"name": f"fb-{uuid.uuid4().hex[:8]}"})
    sid = r.json()["id"]
    r = await client.post(
        f"/api/v1/feedback/{mine['id']}/promote",
        json={
            "golden_set_id": sid,
            "question": question,
            "expected_status": "ok",
            "required_values": ["파이프 변전소 1"],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "feedback"
    item_id = r.json()["id"]
    r = await client.post(f"/api/v1/feedback/{mine['id']}/dismiss")
    assert (r.status_code, r.json()["code"]) == (409, "feedback_handled")
    listed = (await client.get("/api/v1/feedback")).json()
    assert all(f["id"] != mine["id"] for f in listed)  # 기본 목록은 열린 것만

    async with session_scope() as db:
        fb = await db.get(Feedback, mine["id"])
        assert fb is not None and fb.status == "promoted" and fb.golden_item_id == item_id
        assert fb.handled_by == builder
        item = await db.get(GoldenItem, item_id)
        assert item is not None and item.required_values == ["파이프 변전소 1"]
        logged = await db.scalar(
            select(AuditLog).where(AuditLog.target == f"feedback:{mine['id']}")
        )
        assert logged is not None and logged.payload["status"] == "promoted"


async def test_dismissed_feedback_leaves_no_item(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    username, password, uid = await make_user("user")
    answer_id = await _exchange(uid, "넘길 질문")
    assert (await login(client, username, password)).status_code == 200
    await client.put(f"/api/v1/messages/{answer_id}/feedback", json={"rating": "down"})

    username, password, _ = await make_user("modeler")
    assert (await login(client, username, password)).status_code == 200
    listed = (await client.get("/api/v1/feedback")).json()
    fid = next(f["id"] for f in listed if f["message_id"] == answer_id)
    assert (await client.post(f"/api/v1/feedback/{fid}/dismiss")).status_code == 204
    listed = (await client.get("/api/v1/feedback", params={"status": "dismissed"})).json()
    assert any(f["id"] == fid and f["golden_item_id"] is None for f in listed)
    assert (await client.post("/api/v1/feedback/999999999/dismiss")).status_code == 404
