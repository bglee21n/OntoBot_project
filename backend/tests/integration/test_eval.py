"""Phase 7 — 평가 러너·장애 주입·권한·게이트, 평가 API(실제 DB, LLM 은 대본 가짜).

완료 기준(계획 Phase 7): 게이트 동작, 권한 침투·장애 주입 문항 전부 통과(실패 은폐 0, 권한 위반 0).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from ontobot.db.models.evaluation import EvalResult, GoldenSet
from ontobot.db.models.jobs import Job
from ontobot.db.session import session_scope
from ontobot.evaluation import golden, runner
from ontobot.jobs import handlers
from ontobot.llm.adapters import ollama
from ontobot.objects.store import upsert_objects
from ontobot.ontology import importer, registry, versioning
from tests.integration.conftest import MakeUser, login
from tests.integration.test_chat import _org, _owned_by
from tests.integration.test_pipeline import CFG, FACILITIES, SAMPLE, Script, _plan

pytestmark = pytest.mark.integration

ANSWER = "파이프 변전소 1의 위험등급은 4입니다. [[o:Facility/PT-1#risk_grade]]"


@pytest.fixture(scope="module")
async def schema() -> AsyncIterator[registry.PublishedSchema]:
    async with session_scope() as db:
        doc = importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")
        draft = await versioning.create_draft(db, actor_id=None, parent_id=None)
        await versioning.replace_document(db, draft.id, doc, actor_id=None)
        await versioning.publish(db, draft.id, actor_id=None)
        await upsert_objects(db, "Facility", FACILITIES)
        await db.commit()
        published = await registry.reload(db)
        assert published is not None
        yield published


def _pack(items: list[dict[str, Any]], roles: dict[str, list[str]] | None = None) -> golden.Pack:
    return golden.Pack.model_validate(
        {"name": f"t-{uuid.uuid4().hex[:8]}", "roles": roles or {}, "items": items}
    )


async def _run_pack(
    monkeypatch: pytest.MonkeyPatch, pack: golden.Pack, script: Script
) -> tuple[Any, dict[str, dict[str, Any]]]:
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(script.handler))
    cfg = runner.RunConfig(llm=CFG)
    async with session_scope() as db:
        gs, _ = await golden.load_pack(db, pack, domain_pack=None, ontology_version_id=None)
        run = await runner.create_run(db, gs.id, cfg)
        await db.commit()
    run = await runner.execute(run.id, cfg, graph_driver=None)
    async with session_scope() as db:
        rows = await db.scalars(select(EvalResult).where(EvalResult.run_id == run.id))
        by_key: dict[str, dict[str, Any]] = {}
        for r in rows:
            by_key[r.verdict["key"]] = {
                "status": r.status,
                "reason": r.failure_reason,
                "grade": r.grade,
                **r.verdict,
            }
    return run, by_key


async def test_runner_faults_roles_and_gate(monkeypatch, schema) -> None:
    org = await _org(None, "PT-1")
    pack = _pack(
        [
            {"id": "a1", "question": "위험등급 3 이상 파이프 시설", "expected_status": "ok",
             "required_values": ["4"]},
            {"id": "a2", "question": "파이프 시설", "fault": "executor_error",
             "expected_status": "failed", "expected_failure": "execution_error"},
            {"id": "a3", "question": f"{org} 소유 파이프 시설", "fault": "graph_down",
             "expected_status": "failed", "expected_failure": "graph_unavailable"},
            {"id": "a4", "question": "파이프 시설", "test_role": "eval_none", "answerable": False,
             "expected_status": "failed", "forbidden_values": ["파이프 변전소 1"]},
            {"id": "a5", "question": "점검원 이름", "answerable": False,
             "expected_status": "failed", "expected_failure": "plan_invalid"},
            {"id": "a6", "question": "파이프 시설", "test_role": "eval_fac",
             "expected_status": "ok", "required_values": ["파이프 변전소 1"]},
            {"id": "a7", "question": "파이프 시설", "fault": "timeout",
             "expected_status": "failed", "expected_failure": "timeout"},
        ],
        roles={"eval_none": [], "eval_fac": ["Facility"]},
    )  # fmt: skip
    script = Script(
        [
            _plan(filters=[{"property": "risk_grade", "op": "gte", "value": 3}]),  # a1
            _plan(),  # a2 — 실행하지 않고 실패
            _plan(traverse=[_owned_by(org)]),  # a3 — 죽은 드라이버로 실제 실행기
            _plan(),  # a4 — 읽을 수 없는 타입 → forbidden
            {"kind": "cannot_answer", "reason": "스키마에 없음"},  # a5
            _plan(),  # a6
            _plan(),  # a7
        ],
        ANSWER,
    )
    run, r = await _run_pack(monkeypatch, pack, script)
    assert run.status == "succeeded", run.metrics
    assert {k: v["correct"] for k, v in r.items()} == dict.fromkeys(r, True), r
    assert (r["a2"]["status"], r["a2"]["reason"]) == ("failed", "execution_error")
    assert (r["a3"]["status"], r["a3"]["reason"]) == ("failed", "graph_unavailable")
    assert (r["a4"]["status"], r["a4"]["reason"]) == ("failed", "forbidden")
    assert (r["a7"]["status"], r["a7"]["reason"]) == ("failed", "timeout")
    m = run.metrics
    assert m["fault_items"] == 3 and m["fault_cover_ups"] == 0
    assert m["permission_violations"] == 0 and m["harness_errors"] == 0
    assert m["accuracy"] == 1.0 and m["citation_rate"] == 1.0
    assert run.gate_passed is True
    assert run.llm_config["roles"]["planner"]["model"] == "fake"


async def test_cover_up_and_unanswerable_graded_fail_the_gate(monkeypatch, schema) -> None:
    pack = _pack(
        [
            # 장애를 주입했는데 계획 단계에서 끝나 failed 가 아니다 → 은폐로 센다
            {"id": "b1", "question": "안녕", "fault": "executor_error",
             "expected_status": "failed"},
            # 답할 수 없는 질문에 ① 로 답했다
            {"id": "b2", "question": "점검원", "answerable": False, "expected_status": "failed"},
        ]
    )  # fmt: skip
    script = Script([{"kind": "not_query"}, _plan()], ANSWER)
    run, r = await _run_pack(monkeypatch, pack, script)
    assert r["b1"]["cover_up"] and r["b2"]["unanswerable_graded"]
    assert run.metrics["fault_cover_ups"] == 1
    assert run.metrics["unanswerable_graded_rate"] == 1.0
    assert run.gate_passed is False
    failed = {c["metric"] for c in run.metrics["gate"]["checks"] if not c["passed"]}
    assert {"fault_cover_ups", "unanswerable_graded_rate"} <= failed


async def test_load_pack_updates_by_key_and_protects_results(monkeypatch, schema) -> None:
    pack = _pack([{"id": "c1", "question": "파이프 시설", "expected_status": "ok"}])
    await _run_pack(monkeypatch, pack, Script([_plan()], ANSWER))  # 결과가 붙게 한 번 돈다
    async with session_scope() as db:
        changed = pack.model_copy(
            update={"items": [pack.items[0].model_copy(update={"question": "바뀐 질문"})]}
        )
        gs, counts = await golden.load_pack(db, changed, domain_pack=None, ontology_version_id=None)
        assert counts == {"added": 0, "updated": 1, "removed": 0}
        assert (await golden.items(db, gs.id))[0].question == "바뀐 질문"
        await db.commit()
    async with session_scope() as db:
        emptied = pack.model_copy(update={"items": []})
        with pytest.raises(golden.GoldenConflict):
            await golden.load_pack(db, emptied, domain_pack=None, ontology_version_id=None)


async def test_eval_api(
    monkeypatch, schema, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    username, password, _ = await make_user("user")
    assert (await login(client, username, password)).status_code == 200
    assert (await client.get("/api/v1/golden-sets")).status_code == 403

    username, password, _ = await make_user("bot_builder")
    assert (await login(client, username, password)).status_code == 200
    name = f"api-{uuid.uuid4().hex[:8]}"
    r = await client.post("/api/v1/golden-sets", json={"name": name})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    assert (await client.post("/api/v1/golden-sets", json={"name": name})).status_code == 409
    r = await client.post(
        f"/api/v1/golden-sets/{sid}/items",
        json={"key": "k1", "question": "파이프 시설", "expected_status": "ok"},
    )
    assert r.status_code == 201, r.text
    item_id = r.json()["id"]
    r = await client.patch(
        f"/api/v1/golden-sets/{sid}/items/{item_id}",
        json={"required_values": ["파이프 변전소 1"]},
    )
    assert r.json()["required_values"] == ["파이프 변전소 1"]
    assert r.json()["question"] == "파이프 시설"  # 보내지 않은 필드는 그대로
    detail = (await client.get(f"/api/v1/golden-sets/{sid}")).json()
    assert detail["items"] == 1 and detail["item_list"][0]["key"] == "k1"

    r = await client.post("/api/v1/eval-runs", json={"golden_set_id": sid})
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    assert r.json()["status"] == "running"
    r = await client.post("/api/v1/eval-runs", json={"golden_set_id": sid})
    assert (r.status_code, r.json()["code"]) == (409, "eval_running")
    async with session_scope() as db:
        job = await db.scalar(
            select(Job).where(Job.kind == handlers.EVAL_RUN).order_by(Job.id.desc())
        )
        assert job is not None and job.payload["run_id"] == run_id

    # 워커가 할 일을 직접 돌린다(처리기 = 실제 설정 + 가짜 Ollama 전송층).
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(Script([_plan()], ANSWER).handler))
    out = await handlers.eval_run({"run_id": run_id})
    assert out == {"run_id": run_id, "gate_passed": True}
    d = (await client.get(f"/api/v1/eval-runs/{run_id}")).json()
    assert d["status"] == "succeeded" and d["gate_passed"] is True and d["results"] == 1
    assert d["result_list"][0]["verdict"]["correct"] is True
    assert {c["metric"] for c in d["gate_checks"]} >= {"citation_rate", "accuracy"}
    assert any(
        x["id"] == run_id
        for x in (await client.get(f"/api/v1/eval-runs?golden_set_id={sid}")).json()
    )

    # 결과가 붙은 문항·골든셋은 지울 수 없다
    r = await client.delete(f"/api/v1/golden-sets/{sid}/items/{item_id}")
    assert (r.status_code, r.json()["code"]) == (409, "golden_in_use")
    assert (await client.delete(f"/api/v1/golden-sets/{sid}")).status_code == 409
    async with session_scope() as db:
        assert await db.get(GoldenSet, sid) is not None


async def test_orphaned_running_run_is_closed_on_next_start(
    schema, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    """작업이 끝내 실패해 running 으로 남은 실행은 다음 실행 요청 때 failed 로 닫힌다."""
    username, password, _ = await make_user("modeler")
    assert (await login(client, username, password)).status_code == 200
    sid = (
        await client.post("/api/v1/golden-sets", json={"name": f"o-{uuid.uuid4().hex[:8]}"})
    ).json()["id"]
    await client.post(
        f"/api/v1/golden-sets/{sid}/items", json={"question": "q", "expected_status": "ok"}
    )
    first = (await client.post("/api/v1/eval-runs", json={"golden_set_id": sid})).json()["id"]
    async with session_scope() as db:
        job = await db.scalar(
            select(Job).where(Job.kind == handlers.EVAL_RUN).order_by(Job.id.desc())
        )
        assert job is not None and job.payload["run_id"] == first
        job.status = "failed"  # 워커가 재시도를 다 쓰고 포기했다
        await db.commit()
    r = await client.post("/api/v1/eval-runs", json={"golden_set_id": sid})
    assert r.status_code == 202, r.text
    old = (await client.get(f"/api/v1/eval-runs/{first}")).json()
    assert old["status"] == "failed" and old["finished_at"] is not None

    # 작업 없이 도는 실행(CLI)은 닫지 않는다
    async with session_scope() as db:
        cli = await runner.create_run(db, sid, runner.RunConfig(llm=CFG))
        await db.commit()
        cli_id = cli.id
    async with session_scope() as db:
        assert await runner.reap_orphans(db, sid) == []
        await db.rollback()
    assert (await client.get(f"/api/v1/eval-runs/{cli_id}")).json()["status"] == "running"
