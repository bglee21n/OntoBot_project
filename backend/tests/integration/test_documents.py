"""Phase 16a — 문서 등록·청크·임베딩 작업·그룹 권한(실제 DB, 임베딩은 가짜 Ollama)."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from sqlalchemy import select, text

from ontobot.db.models.documents import DocumentChunk
from ontobot.db.models.jobs import Job
from ontobot.db.session import session_scope
from ontobot.documents import vector_index
from ontobot.documents.indexing import embed_document
from ontobot.llm.adapters import ollama
from ontobot.llm.config import LlmConfig
from ontobot.llm.gateway import LLMGateway, LlmUnavailable
from ontobot.security import admin
from tests.integration.conftest import MakeUser, login

pytestmark = pytest.mark.integration

CFG = LlmConfig.model_validate(
    {
        "providers": {"local": {"adapter": "ollama", "base_url": "http://fake-ollama"}},
        "roles": {"embedder": {"provider": "local", "model": "fake-embed"}},
    }
)


def _fake_embed(seen: list[list[str]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/embed"
        body = json.loads(request.content)
        seen.append(body["input"])
        # 글 길이로 정한 3차원 벡터 — 결정적이다.
        return httpx.Response(
            200, json={"embeddings": [[float(len(t)), 1.0, 0.0] for t in body["input"]]}
        )

    return httpx.MockTransport(handler)


async def _curator_in_group(make_user: MakeUser) -> tuple[str, str, int, int]:
    username, password, uid = await make_user("modeler")
    name = f"doc_{uuid.uuid4().hex[:8]}"
    async with session_scope() as db:
        g = await admin.create_group(db, actor_id=None, name=name, display_name=name)
        await admin.set_members(db, actor_id=None, group_id=g.id, user_ids=[uid], via="test")
        await db.commit()
        gid = g.id
    return username, password, uid, gid


async def test_upload_chunk_embed_and_group_permission(
    monkeypatch, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    username, password, uid, gid = await _curator_in_group(make_user)
    assert (await login(client, username, password)).status_code == 200
    groups = (await client.get("/api/v1/documents/groups")).json()
    assert gid in {g["id"] for g in groups}

    body = (
        "점검 기준\n\n"
        + "\n\n".join(f"{i}항. 위험등급 {i} 은 정기 점검 대상이다." for i in range(40))
    ).encode()
    tag = uuid.uuid4().hex[:6]
    files = {"file": (f"기준-{tag}.txt", body, "text/plain")}
    r = await client.post("/api/v1/documents", data={"group_id": str(gid)}, files=files)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "pending" and doc["title"] == f"기준-{tag}.txt"
    assert doc["chunks"] >= 2 and doc["embedded"] == 0
    # 같은 파일을 같은 그룹에 다시 넣으면 409.
    r = await client.post("/api/v1/documents", data={"group_id": str(gid)}, files=files)
    assert (r.status_code, r.json()["code"]) == (409, "document_exists")
    r = await client.post(
        "/api/v1/documents",
        data={"group_id": str(gid)},
        files={"file": ("a.hwp", b"x", "application/octet-stream")},
    )
    assert (r.status_code, r.json()["code"]) == (422, "document_unreadable")
    async with session_scope() as db:
        job = await db.scalar(
            select(Job).where(Job.kind == "document_embed").order_by(Job.id.desc())
        )
        assert job is not None and job.payload == {"document_id": doc["id"]}

    # 워커 작업: 청크마다 벡터, 문서는 indexed.
    seen: list[list[str]] = []
    monkeypatch.setattr(ollama, "transport", _fake_embed(seen))
    result = await embed_document(doc["id"], LLMGateway(CFG))
    assert result["embedded"] == doc["chunks"] and result["model"] == "fake-embed"
    detail = (await client.get(f"/api/v1/documents/{doc['id']}")).json()
    assert detail["status"] == "indexed" and detail["embedded"] == doc["chunks"]
    assert detail["embedding_model"] == "fake-embed"
    chunks = detail["chunk_list"]
    assert all(c["embedded"] for c in chunks)
    whole = "\n\n".join(c["text"] for c in chunks)
    assert "위험등급 39 은 정기 점검 대상이다." in whole
    assert [t for batch in seen for t in batch] == [c["text"] for c in chunks]
    # 다시 돌려도 이미 만든 청크는 다시 부르지 않는다.
    seen.clear()
    await embed_document(doc["id"], LLMGateway(CFG))
    assert seen == []
    async with session_scope() as db:
        nearest = await db.scalar(
            text(
                "SELECT chunk_no FROM document_chunk WHERE document_id = :d "
                "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 1"
            ),
            {"d": doc["id"], "q": f"[{len(chunks[0]['text'])},1,0]"},
        )
        assert nearest is not None
        # 모델별 부분 HNSW 인덱스 — 검색 식(distance_sql)이 그 인덱스를 탄다.
        name = vector_index.index_name("fake-embed")
        indexdef = await db.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"), {"n": name}
        )
        assert indexdef is not None and "hnsw" in indexdef and "fake-embed" in indexdef
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        plan = (
            await db.execute(
                text(
                    "EXPLAIN SELECT chunk_no FROM document_chunk "
                    "WHERE embedding_model = 'fake-embed' "
                    f"ORDER BY {vector_index.distance_sql('fake-embed', 3)} LIMIT 3"
                ),
                {"q": "[1,1,0]"},
            )
        ).scalars()
        assert name in "\n".join(plan)

    # 다른 그룹 사용자에게는 없는 문서다.
    other = await make_user("modeler")
    assert (await login(client, other[0], other[1])).status_code == 200
    assert doc["id"] not in {d["id"] for d in (await client.get("/api/v1/documents")).json()}
    assert (await client.get(f"/api/v1/documents/{doc['id']}")).status_code == 404
    r = await client.post("/api/v1/documents", data={"group_id": str(gid)}, files=files)
    assert (r.status_code, r.json()["code"]) == (403, "forbidden_group")
    # 일반 사용자는 넣을 수 없다(역할).
    plain = await make_user("user")
    assert (await login(client, plain[0], plain[1])).status_code == 200
    assert (
        await client.post("/api/v1/documents", data={"group_id": str(gid)}, files=files)
    ).status_code == 403

    # 문서가 있는 그룹은 지울 수 없다.
    async with session_scope() as db:
        with pytest.raises(admin.AdminError) as e:
            await admin.delete_group(db, actor_id=uid, group_id=gid)
        assert e.value.code == "group_has_documents"

    # 다시 색인 → 벡터를 지우고 작업을 넣는다. 삭제하면 청크도 사라진다.
    assert (await login(client, username, password)).status_code == 200
    r = await client.post(f"/api/v1/documents/{doc['id']}/reindex")
    assert r.json()["status"] == "pending" and r.json()["embedded"] == 0
    assert (await client.delete(f"/api/v1/documents/{doc['id']}")).status_code == 204
    async with session_scope() as db:
        left = await db.scalar(
            select(DocumentChunk.id).where(DocumentChunk.document_id == doc["id"])
        )
        assert left is None


async def test_embedding_failure_is_recorded_and_retried(
    monkeypatch, client: httpx.AsyncClient, make_user: MakeUser
) -> None:
    username, password, _, gid = await _curator_in_group(make_user)
    assert (await login(client, username, password)).status_code == 200
    r = await client.post(
        "/api/v1/documents",
        data={"group_id": str(gid), "title": "실패 문서"},
        files={"file": ("f.md", f"# 제목 {uuid.uuid4()}".encode(), "text/markdown")},
    )
    doc = r.json()
    assert doc["title"] == "실패 문서" and doc["media_type"] == "text/markdown"
    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(lambda _: httpx.Response(503)))
    with pytest.raises(LlmUnavailable):  # 작업 큐가 다시 시도한다
        await embed_document(doc["id"], LLMGateway(CFG))
    detail = (await client.get(f"/api/v1/documents/{doc['id']}")).json()
    assert detail["status"] == "pending" and detail["error"].startswith("임베딩 실패")
