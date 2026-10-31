"""Phase 16c — 문서 ↔ 객체 링크: 정확 일치 후보·확정/거부·권한(찾는 사람·보는 사람)."""

from __future__ import annotations

import uuid

import httpx
import pytest

from ontobot.db.session import session_scope
from ontobot.objects.store import upsert_objects
from ontobot.security import admin
from tests.integration.conftest import MakeUser, add_group, login

pytestmark = pytest.mark.integration


async def test_scan_confirm_and_permissions(
    sample_published: None, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    tag = uuid.uuid4().hex[:8]
    f1, f2, org = f"LF1-{tag}", f"LF2-{tag}", f"LO-{tag}"
    async with session_scope() as db:
        await upsert_objects(
            db,
            "Facility",
            [
                (f1, {"facility_id": f1, "name": f"링크시설일-{tag}", "risk_grade": 4}),
                (f2, {"facility_id": f2, "name": f"링크시설이-{tag}", "risk_grade": 2}),
            ],
        )
        await upsert_objects(
            db, "Organization", [(org, {"org_id": org, "name": f"링크조직-{tag}"})]
        )
        await db.commit()
    curator, cpw, cid = await make_user("modeler")
    reader, rpw, rid = await make_user("user")
    async with session_scope() as db:
        # 문서 그룹: 시설만 읽는다(조직은 못 읽는다).
        gid = await add_group(db, f"lk_{tag}", ["Facility"], edit=False)
        await admin.set_members(db, actor_id=None, group_id=gid, user_ids=[cid, rid], via="test")
        await db.commit()

    body = (
        f"# 점검 메모\n\n링크시설일-{tag} 은 이번 분기 점검 대상이다.\n\n"
        f"설비 번호 {f2} 도 함께 본다. 링크조직-{tag} 이 맡는다.\n\n"
        f"링크시설일-{tag} 의 접지 저항을 다시 잰다. 링크시설삼-{tag} 은 없다."
    ).encode()
    assert (await login(client, curator, cpw)).status_code == 200
    r = await client.post(
        "/api/v1/documents",
        data={"group_id": str(gid)},
        files={"file": (f"memo-{tag}.md", body, "text/markdown")},
    )
    assert r.status_code == 201, r.text
    doc_id = r.json()["id"]

    r = await client.post(f"/api/v1/documents/{doc_id}/links/scan")
    assert r.status_code == 200, r.text
    assert r.json() | {"scanned_objects": 0} == {
        "found": 2,
        "added": 2,
        "removed": 0,
        "scanned_objects": 0,
        "truncated": False,
    }
    links = (await client.get(f"/api/v1/documents/{doc_id}/links")).json()
    got = {x["object_pk"]: (x["match_kind"], x["occurrences"], x["status"]) for x in links}
    # 조직은 찾는 사람이 읽을 수 없어 후보가 아니다. 없는 시설 이름은 맞을 객체가 없다.
    assert got == {f1: ("title", 2, "candidate"), f2: ("pk", 1, "candidate")}
    by_pk = {x["object_pk"]: x for x in links}
    assert by_pk[f1]["title"] == f"링크시설일-{tag}" and by_pk[f1]["type_display_name"] == "시설"

    put = client.put
    assert (
        await put(
            f"/api/v1/documents/{doc_id}/links/{by_pk[f1]['id']}", json={"status": "confirmed"}
        )
    ).status_code == 204
    assert (
        await put(
            f"/api/v1/documents/{doc_id}/links/{by_pk[f2]['id']}", json={"status": "rejected"}
        )
    ).status_code == 204
    # 다시 찾아도 확정·거부는 그대로, 새로 생긴 것 없음.
    r = (await client.post(f"/api/v1/documents/{doc_id}/links/scan")).json()
    assert (r["found"], r["added"], r["removed"]) == (2, 0, 0)
    links = (await client.get(f"/api/v1/documents/{doc_id}/links")).json()
    assert {x["object_pk"]: x["status"] for x in links} == {f1: "confirmed", f2: "rejected"}
    # 다른 문서의 링크 id 로는 바꿀 수 없다.
    assert (
        await put(
            f"/api/v1/documents/{doc_id + 100000}/links/{by_pk[f1]['id']}",
            json={"status": "rejected"},
        )
    ).status_code == 404

    # 객체 쪽 — 확정된 문서만.
    rel = (await client.get(f"/api/v1/objects/Facility/{f1}/documents")).json()
    assert [(d["document_id"], d["match_text"]) for d in rel] == [(doc_id, f"링크시설일-{tag}")]
    assert (await client.get(f"/api/v1/objects/Facility/{f2}/documents")).json() == []

    # 일반 사용자(같은 그룹) — 확정된 링크만 보고, 후보 찾기·확정은 못 한다.
    assert (await login(client, reader, rpw)).status_code == 200
    links = (await client.get(f"/api/v1/documents/{doc_id}/links")).json()
    assert [x["object_pk"] for x in links] == [f1]
    assert (await client.post(f"/api/v1/documents/{doc_id}/links/scan")).status_code == 403

    # 시설은 읽지만 문서 그룹 밖 — 객체의 관련 문서가 비어 있다.
    outsider, opw, oid = await make_user("user")
    async with session_scope() as db:
        g2 = await add_group(db, f"lk2_{tag}", ["Facility"], edit=False)
        await admin.set_members(db, actor_id=None, group_id=g2, user_ids=[oid], via="test")
        await db.commit()
    assert (await login(client, outsider, opw)).status_code == 200
    assert (await client.get(f"/api/v1/objects/Facility/{f1}/documents")).json() == []
    assert (await client.get(f"/api/v1/documents/{doc_id}/links")).status_code == 404
    # 시설을 읽을 수 없으면 객체 자체가 없다.
    nobody, npw, _ = await make_user("user")
    assert (await login(client, nobody, npw)).status_code == 200
    assert (await client.get(f"/api/v1/objects/Facility/{f1}/documents")).status_code == 404
