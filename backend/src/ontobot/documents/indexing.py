"""청크 임베딩(Phase 16a, 워커 작업 ``document_embed``).

모델 호출은 길다 — DB 트랜잭션을 쥔 채 부르지 않는다(묶음마다: 읽기 세션 닫음 → 임베딩 → 쓰기 세션).
아직 벡터가 없거나 **지금 설정의 모델과 다른** 청크만 다시 만든다(모델이 섞인 문서는 검색에서
비교할 수 없다). 공급자에 닿지 못하면 오류를 문서에 남기고 예외를 올려 작업 큐가 다시 시도하게 한다.
다 만들면 그 모델의 벡터 인덱스(``vector_index``)를 확인한 뒤 ``indexed`` 로 표시한다 —
인덱스 DDL 이 실패해도 작업이 다시 돌며(만들 청크가 없으니) 인덱스만 다시 시도한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select

from ontobot.db.models.documents import Document, DocumentChunk
from ontobot.db.session import get_engine, session_scope
from ontobot.documents.vector_index import ensure_vector_index
from ontobot.llm.gateway import LLMGateway, LlmUnavailable

ROLE = "embedder"
BATCH = 16


async def embed_document(document_id: int, llm: LLMGateway) -> dict[str, Any]:
    model = llm.model_for(ROLE)
    done = 0
    while True:
        async with session_scope() as db:
            doc = await db.get(Document, document_id)
            if doc is None:  # 그사이 지웠다
                return {"document_id": document_id, "deleted": True}
            rows = (
                await db.execute(
                    select(DocumentChunk.id, DocumentChunk.text)
                    .where(
                        DocumentChunk.document_id == document_id,
                        or_(
                            DocumentChunk.embedding.is_(None),
                            DocumentChunk.embedding_model.is_distinct_from(model),
                        ),
                    )
                    .order_by(DocumentChunk.chunk_no)
                    .limit(BATCH)
                )
            ).all()
        if not rows:
            break
        try:
            vectors = await llm.embed(ROLE, [t for _, t in rows])
        except LlmUnavailable as e:
            async with session_scope() as db:
                doc = await db.get(Document, document_id)
                if doc is not None:
                    doc.error = f"임베딩 실패: {e}"
                    await db.commit()
            raise
        async with session_scope() as db:
            for (chunk_id, _), vec in zip(rows, vectors, strict=True):
                c = await db.get(DocumentChunk, chunk_id)
                if c is not None:
                    c.embedding, c.embedding_model = vec, model
            await db.commit()
        done += len(rows)
    async with session_scope() as db:
        dims = await db.scalar(
            select(func.vector_dims(DocumentChunk.embedding))
            .where(DocumentChunk.document_id == document_id, DocumentChunk.embedding_model == model)
            .limit(1)
        )
    if dims:
        await ensure_vector_index(get_engine(), model, int(dims))
    async with session_scope() as db:
        doc = await db.get(Document, document_id)
        if doc is None:
            return {"document_id": document_id, "deleted": True}
        doc.status, doc.error = "indexed", None
        doc.embedding_model, doc.indexed_at = model, datetime.now(UTC)
        await db.commit()
    return {"document_id": document_id, "embedded": done, "model": model}
