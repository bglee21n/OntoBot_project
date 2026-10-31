"""청크 임베딩(Phase 16a, 워커 작업 ``document_embed``).

모델 호출은 길다 — DB 트랜잭션을 쥔 채 부르지 않는다(묶음마다: 읽기 세션 닫음 → 임베딩 → 쓰기 세션).
아직 벡터가 없거나 **지금 설정의 모델과 다른** 청크만 다시 만든다(모델이 섞인 문서는 검색에서
비교할 수 없다). 공급자에 닿지 못하면 오류를 문서에 남기고 예외를 올려 작업 큐가 다시 시도하게 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from ontobot.db.models.documents import Document, DocumentChunk
from ontobot.db.session import session_scope
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
        doc = await db.get(Document, document_id)
        if doc is None:
            return {"document_id": document_id, "deleted": True}
        doc.status, doc.error = "indexed", None
        doc.embedding_model, doc.indexed_at = model, datetime.now(UTC)
        await db.commit()
    return {"document_id": document_id, "embedded": done, "model": model}
