"""Phase 22 파일럿 판단·자산화 내보내기 — 불변식 위반이면 계속 422, zip 에 객체 행 없음(실제 DB)."""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from ontobot.db.models.evaluation import EvalRun, GoldenItem, GoldenSet
from ontobot.db.session import session_scope
from ontobot.evaluation import golden
from ontobot.ontology import importer, registry
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration

BASE = "/api/v1/adoption"


async def _as(client: httpx.AsyncClient, user: tuple[str, str, int]) -> None:
    assert (await login(client, user[0], user[1])).status_code == 200


async def _run(*, invariant_ok: bool, gate: bool, mode: str = "live") -> int:
    """끝난 평가 실행 하나 — 나중에 넣은 것이 마지막 실행이다(id 순서)."""
    async with session_scope() as db:
        gs = GoldenSet(name=f"pilot-{uuid.uuid4().hex[:8]}")
        db.add(gs)
        await db.flush()
        now = datetime.now(UTC)
        checks = [
            {"metric": "permission_violations", "kind": "invariant", "op": "max", "limit": 0.0,
             "value": 0.0 if invariant_ok else 1.0, "passed": invariant_ok},
            {"metric": "accuracy", "kind": "threshold", "op": "min", "limit": 0.9, "value": 0.97,
             "passed": True},
        ]  # fmt: skip
        run = EvalRun(
            golden_set_id=gs.id,
            llm_config={"mode": mode},
            status="succeeded",
            metrics={"accuracy": 0.97, "gate": {"passed": gate, "checks": checks, "skipped": []}},
            gate_passed=gate,
            started_at=now,
            finished_at=now,
        )
        db.add(run)
        await db.commit()
        return run.id


async def test_decision_follows_the_last_real_run(
    client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    await _as(client, await make_user("modeler"))
    eid = (await client.post(f"{BASE}/engagements", json={"name": "파일럿"})).json()["id"]

    bad = await _run(invariant_ok=False, gate=False)
    p = (await client.get(f"{BASE}/engagements/{eid}/pilot")).json()
    assert p["run"]["run_id"] == bad and p["run"]["invariant_violations"] == [
        "permission_violations"
    ]
    assert p["allowed"]["go"] and p["allowed"]["no_go"] is None
    r = await client.post(
        f"{BASE}/engagements/{eid}/decisions", json={"decision": "go", "reason": "정답률 97%"}
    )
    assert r.status_code == 422 and r.json()["code"] == "decision_blocked"
    r = await client.post(
        f"{BASE}/engagements/{eid}/decisions", json={"decision": "no_go", "reason": "권한 위반"}
    )
    assert r.status_code == 201 and r.json()["eval_run_id"] == bad

    good = await _run(invariant_ok=True, gate=True)
    # 재생 실행은 판단 근거가 아니다(더 나중이어도).
    await _run(invariant_ok=False, gate=False, mode="replay")
    p = (await client.get(f"{BASE}/engagements/{eid}/pilot")).json()
    assert p["run"]["run_id"] == good and p["allowed"]["go"] is None
    assert [b["label"] for b in p["business"]] == ["조회에 드는 시간", "활용도"]
    r = await client.post(
        f"{BASE}/engagements/{eid}/decisions", json={"decision": "go", "reason": "게이트 통과"}
    )
    assert r.status_code == 201, r.text

    p = (await client.get(f"{BASE}/engagements/{eid}/pilot")).json()
    assert [d["decision"] for d in p["decisions"]] == ["go", "no_go"]
    detail = (await client.get(f"{BASE}/engagements/{eid}")).json()
    stage5 = {h["label"]: h["filled"] for h in detail["stages"][4]["handoffs"]}
    assert stage5["계속할지 판단"] is True


async def test_decide_and_export_need_owner(client: httpx.AsyncClient, make_user: MakeUser) -> None:
    owner, editor = await make_user("modeler"), await make_user("modeler")
    await _as(client, owner)
    eid = (await client.post(f"{BASE}/engagements", json={"name": "권한"})).json()["id"]
    await client.post(
        f"{BASE}/engagements/{eid}/members", json={"username": editor[0], "role": "editor"}
    )
    await _as(client, editor)
    detail = (await client.get(f"{BASE}/engagements/{eid}")).json()
    assert not detail["can_decide"] and not detail["can_export"]
    assert (await client.get(f"{BASE}/engagements/{eid}/pilot")).status_code == 200
    r = await client.post(
        f"{BASE}/engagements/{eid}/decisions", json={"decision": "no_go", "reason": "x"}
    )
    assert r.status_code == 403
    assert (await client.get(f"{BASE}/engagements/{eid}/export")).status_code == 403


async def test_export_zip_has_design_confirmed_golden_and_forms_without_people(
    client: httpx.AsyncClient, make_user: MakeUser, sample_published: None
) -> None:
    await _as(client, await make_user("modeler"))
    eid = (await client.post(f"{BASE}/engagements", json={"name": "자산화"})).json()["id"]
    url = f"{BASE}/engagements/{eid}/sheets"
    await client.put(
        f"{url}/stakeholders",
        json={
            "revision": 0,
            "payload": {"people": [{"name": "홍길동 과장", "username": "hong", "role": "user"}]},
        },
    )
    await client.put(
        f"{url}/observation",
        json={
            "revision": 0,
            "payload": {
                "log": [{"question": "고위험 시설은 몇 곳?"}, {"question": "강남 철탑 목록"}]
            },
        },
    )
    p = (await client.post(f"{BASE}/engagements/{eid}/send/golden/preview", json={})).json()
    applied = await client.post(
        f"{BASE}/engagements/{eid}/send/golden/apply", json={"fingerprint": p["fingerprint"]}
    )
    gs_id = applied.json()["golden_set_id"]
    # 한 문항만 기대 값을 확정한다.
    async with session_scope() as db:
        item = await db.scalar(
            select(GoldenItem).where(
                GoldenItem.golden_set_id == gs_id, GoldenItem.question == "고위험 시설은 몇 곳?"
            )
        )
        assert item is not None
        item.required_values = ["12"]
        await db.commit()

    r = await client.get(f"{BASE}/engagements/{eid}/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert f"adoption-{eid}-" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"ontology.yaml", "golden.yaml", "README.md", "adoption/engagement.json"} <= names
    assert {"adoption/stakeholders.json", "adoption/observation.json"} <= names
    assert not any(n.startswith("seed/") or n.endswith(".csv") for n in names)  # 객체 행 없음

    async with session_scope() as db:
        published = await registry.reload(db)
    assert published is not None
    doc = importer.parse(z.read("ontology.yaml").decode(), "yaml")
    assert doc.content_hash() == published.content_hash

    pack = golden.parse(z.read("golden.yaml").decode())
    assert [(i.question, i.required_values) for i in pack.items] == [
        ("고위험 시설은 몇 곳?", ["12"])
    ]

    people = json.loads(z.read("adoption/stakeholders.json"))["payload"]["people"]
    assert people == [{"role": "user"}]
    assert "홍길동" not in r.content.decode("latin-1") and "홍길동".encode() not in b"".join(
        z.read(n) for n in names
    )
