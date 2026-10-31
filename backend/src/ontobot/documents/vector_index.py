"""청크 벡터 인덱스(Phase 16b, ADR-0016) — 임베딩 모델마다 부분 HNSW 인덱스 하나.

``document_chunk.embedding`` 은 차원 없는 ``vector`` 라 그대로는 인덱스를 걸 수 없다.
모델의 차원으로 캐스팅한 식(``embedding::vector(N)``)에 ``WHERE embedding_model = '<모델>'``
부분 인덱스를 건다. 모델 이름은 설정(``config/llm.yaml``)에서 오므로 마이그레이션에 고정할 수
없어 런타임 DDL 이다(``objects.indexes`` 와 같은 방식, ``CONCURRENTLY IF NOT EXISTS`` 로 멱등).

검색(16d)은 이 인덱스를 타려면 **같은 식**(``distance_sql``)과 같은 모델 조건을 써야 한다.
DDL 은 값을 바인드할 수 없으므로 모델 이름은 형식을 확인한 뒤에만 넣는다.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
MAX_DIMS = 2000  # pgvector HNSW 가 vector 형에 거는 차원 상한


def _check(model: str, dims: int) -> None:
    if not MODEL_RE.match(model) or not 0 < dims <= MAX_DIMS:
        raise ValueError(f"벡터 인덱스를 만들 수 없는 모델·차원: {model!r} {dims}")


def index_name(model: str) -> str:
    return f"ix_chunk_vec_{hashlib.sha256(model.encode()).hexdigest()[:20]}"


def distance_sql(model: str, dims: int, param: str = "q") -> str:
    """코사인 거리 식 — 인덱스 식과 글자까지 같아야 플래너가 인덱스를 쓴다."""
    _check(model, dims)
    return f"(embedding::vector({dims})) <=> CAST(:{param} AS vector({dims}))"


async def ensure_vector_index(engine: AsyncEngine, model: str, dims: int) -> str:
    _check(model, dims)
    name = index_name(model)
    ddl = (
        f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{name}" ON document_chunk '
        f"USING hnsw ((embedding::vector({dims})) vector_cosine_ops) "
        f"WHERE embedding_model = '{model}'"
    )
    # CONCURRENTLY 는 트랜잭션 안에서 돌 수 없다 — 자동 커밋 연결을 쓴다.
    async with engine.connect() as conn:
        auto = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await auto.execute(text(ddl))
    return name
