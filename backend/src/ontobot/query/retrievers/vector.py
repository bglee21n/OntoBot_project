"""문서 청크 벡터 검색(Phase 16d, TRD 17.1 결정 11·12, ADR-0016).

- **보는 사람의 문서 그룹 안에서만** 찾는다(``rbac.document_groups`` 결과를 호출부가 넘긴다).
  색인이 끝난 문서(``indexed``)의 지금 설정 모델 벡터만 비교한다 — 다른 모델의 벡터와 섞지 않는다.
- 검색어는 **질문 문장 그대로**다(플래너가 검색어를 쓰지 않는다).
- 거리 식은 ``documents.vector_index.distance_sql`` 그대로 — 모델별 부분 HNSW 를 타려면 식과
  모델 조건이 인덱스와 글자까지 같아야 한다. 모델 조건도 바인드가 아니라 확인한 글자로 넣는다
  (부분 인덱스의 조건은 상수여야 맞춰진다).
- 점수(코사인 유사도)는 후보를 줄이는 데만 쓴다. "답 없음"은 답변·근거 대조가 정한다
  (ADR-0016 결정 4).
"""

from __future__ import annotations

from collections.abc import Set
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.documents.vector_index import distance_sql, model_predicate

TOP_K = 3  # ADR-0016: bge-m3 는 정답 청크가 모두 3위 안이었다
MIN_SCORE = 0.3  # 답 있는 질문의 최저 1위 점수 0.414 보다 넉넉히 낮게 — 분명히 무관한 것만 버린다


@dataclass(frozen=True)
class Hit:
    document_id: int
    chunk_no: int
    title: str
    text: str
    score: float

    @property
    def ref(self) -> str:
        return f"d:{self.document_id}/{self.chunk_no}"


def _vector(v: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


async def has_documents(db: AsyncSession, groups: Set[int], model: str) -> list[str]:
    """검색할 수 있는 문서의 제목(플래너 요약용, 최대 30개).

    없으면 빈 목록 — 플래너는 문서를 모른다.
    """
    if not groups:
        return []
    rows = await db.scalars(
        text(
            "SELECT d.title FROM document d WHERE d.group_id = ANY(:g) AND d.status = 'indexed' "
            "AND d.embedding_model = :m ORDER BY d.title, d.id LIMIT 30"
        ),
        {"g": list(groups), "m": model},
    )
    return list(rows)


async def search(
    db: AsyncSession,
    groups: Set[int],
    model: str,
    query_vector: list[float],
    *,
    k: int = TOP_K,
    min_score: float = MIN_SCORE,
) -> list[Hit]:
    if not groups:
        return []
    dims = len(query_vector)
    dist = distance_sql(model, dims)
    rows = await db.execute(
        text(
            f"SELECT c.document_id, c.chunk_no, c.text, d.title, {dist} AS dist "  # noqa: S608
            "FROM document_chunk c JOIN document d ON d.id = c.document_id "
            f"WHERE {model_predicate(model, 'c')} AND d.status = 'indexed' "
            "AND d.group_id = ANY(:g) "
            f"ORDER BY {dist} LIMIT :k"
        ),
        {"q": _vector(query_vector), "g": list(groups), "k": k},
    )
    hits = [Hit(r.document_id, r.chunk_no, r.title, r.text, 1 - float(r.dist)) for r in rows]
    return [h for h in hits if h.score >= min_score]
