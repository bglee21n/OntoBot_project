"""Phase 18c — 샘플 데이터 시연: draft 의 격리 스키마에 행을 넣고 운영 파이프라인으로 묻는다.

운영 ``object`` 표와 섞이지 않아야 하고, 요청이 끝나면 ``search_path`` 가 풀려야 한다.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import text

from ontobot.db.session import session_scope
from ontobot.llm.adapters import ollama
from ontobot.objects.reader import resolve
from ontobot.objects.store import upsert_objects
from ontobot.ontology import versioning
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration
API = "/api/v1/workshop/demo"


async def _draft(*, drop_title: bool = False) -> int:
    async with session_scope() as db:
        published = await versioning.published_version_id(db)
        assert published is not None
        doc = versioning.to_document(await versioning.get_version(db, published))
        if drop_title:  # 제목 속성을 지우면 게시 전 검사에 걸린다
            types = [
                t.model_copy(
                    update={
                        "properties": [p for p in t.properties if p.api_name != t.title_property]
                    }
                )
                if t.api_name == "Facility"
                else t
                for t in doc.object_types
            ]
            doc = doc.model_copy(update={"object_types": types})
        draft = await versioning.create_draft(db, actor_id=None, parent_id=published)
        await versioning.replace_document(db, draft.id, doc, actor_id=None)
        await db.commit()
        return draft.id


def _fake(pk: str, answer: str) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "format" in body:
            plan = {
                "kind": "query",
                "plan": {
                    "target": "Facility",
                    "filters": [{"property": "facility_id", "op": "eq", "value": pk}],
                },
            }
            return httpx.Response(200, json={"message": {"content": json.dumps(plan)}})
        return httpx.Response(200, json={"message": {"content": answer}})

    return httpx.MockTransport(handler)


async def test_demo_rows_are_isolated_and_answered_by_the_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    sample_published: None,
    client: httpx.AsyncClient,
    make_user: MakeUser,
) -> None:
    tag = uuid.uuid4().hex[:6].upper()
    demo_pk, public_pk = f"DEMO-{tag}", f"PUB-{tag}"
    async with session_scope() as db:
        await upsert_objects(
            db, "Facility", [(public_pk, {"facility_id": public_pk, "name": "운영 시설"})]
        )
        await db.commit()
    version_id = await _draft()
    username, password, _ = await make_user("modeler")
    assert (await login(client, username, password)).status_code == 200

    status = (await client.get(f"{API}/{version_id}")).json()
    assert (status["ready"], status["exists"]) == (True, False)
    facility = next(t for t in status["types"] if t["api_name"] == "Facility")
    assert facility["pk_property"] == "facility_id" and "risk_grade" in facility["columns"]

    # 잘못된 CSV — 오류를 모두 모아 표 전체를 받지 않는다.
    bad = "facility_id,name,risk_grade,color\nX-1,가,높음,red\n,나,2,blue\n"
    r = await client.post(f"{API}/{version_id}/rows", json={"object_type": "Facility", "csv": bad})
    assert (r.status_code, r.json()["code"]) == (422, "demo_rows_invalid")
    assert any("color" in e for e in r.json()["errors"])

    csv = (
        "facility_id,name,facility_type,risk_grade,owner_org_id\n"
        f"{demo_pk},시연 변전소,변전소,4,DORG-{tag}\n"
    )
    r = await client.post(f"{API}/{version_id}/rows", json={"object_type": "Facility", "csv": csv})
    assert r.status_code == 200 and r.json()["changed"] == 1, r.text
    links = r.json()["links"]
    org = f"org_id,name\nDORG-{tag},시연 지사\n"
    r = await client.post(
        f"{API}/{version_id}/rows", json={"object_type": "Organization", "csv": org}
    )
    assert r.status_code == 200, r.text
    assert links + r.json()["links"] == 1  # FK 링크 규칙 — 시연 스키마의 link 에
    status = (await client.get(f"{API}/{version_id}")).json()
    assert {t["api_name"]: t["rows"] for t in status["types"]}["Facility"] == 1

    # 운영 표에는 없다. 시연 요청이 끝난 뒤 search_path 는 원래대로다.
    async with session_scope() as db:
        assert await resolve(db, "Facility", [demo_pk], declared=["name"]) == {}
        assert public_pk in await resolve(db, "Facility", [public_pk], declared=["name"])
        path = await db.scalar(text("SELECT current_setting('search_path')"))
        assert "demo_v" not in path

    # 운영 파이프라인 그대로 — 시연 행으로 근거 대조까지(①).
    answer = f"시연 변전소의 위험등급은 4입니다. [[o:Facility/{demo_pk}#risk_grade]]"
    monkeypatch.setattr(ollama, "transport", _fake(demo_pk, answer))
    r = await client.post(f"{API}/{version_id}/ask", json={"question": f"{demo_pk} 위험등급은?"})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["grade"]) == ("ok", 1)
    assert [c["ref"] for c in r.json()["citations"]] == [f"o:Facility/{demo_pk}#risk_grade"]
    # 운영 행은 시연에서 보이지 않는다.
    monkeypatch.setattr(ollama, "transport", _fake(public_pk, "x"))
    r = await client.post(f"{API}/{version_id}/ask", json={"question": f"{public_pk} 위험등급은?"})
    assert r.json()["status"] == "empty"

    # 지우면 스키마째 없어진다.
    assert (await client.delete(f"{API}/{version_id}")).status_code == 204
    assert (await client.get(f"{API}/{version_id}")).json()["exists"] is False
    r = await client.post(f"{API}/{version_id}/ask", json={"question": "아무거나"})
    assert (r.status_code, r.json()["code"]) == (404, "demo_not_found")


async def test_demo_needs_a_draft_that_passes_checks(
    sample_published: None, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    version_id = await _draft(drop_title=True)
    username, password, _ = await make_user("modeler")
    assert (await login(client, username, password)).status_code == 200
    status = (await client.get(f"{API}/{version_id}")).json()
    assert status["ready"] is False and status["issues"]
    r = await client.post(
        f"{API}/{version_id}/rows", json={"object_type": "Facility", "csv": "facility_id\nA\n"}
    )
    assert (r.status_code, r.json()["code"]) == (409, "demo_not_ready")
    plain, pw, _ = await make_user("user")
    assert (await login(client, plain, pw)).status_code == 200
    assert (await client.get(f"{API}/{version_id}")).status_code == 403
