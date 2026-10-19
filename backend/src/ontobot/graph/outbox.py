"""그래프 아웃박스 쓰기(TRD 5.2) — 링크·객체를 바꾸는 **같은 트랜잭션**에서 부른다.

이벤트와 payload 모양의 정본이 여기다. 투영기(``graph.projector``)가 같은 모양을 읽는다.

- ``node_upsert`` {type, pk, title}
- ``node_delete`` {type, pk}
- ``link_upsert`` · ``link_delete`` {link_type, from_type, from_pk, to_type, to_pk}

이 모듈은 Neo4j 를 모른다(SQL 만 쓴다).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

Event = Literal["node_upsert", "node_delete", "link_upsert", "link_delete"]


async def enqueue(db: AsyncSession, event: Event, payloads: Iterable[Mapping[str, Any]]) -> int:
    rows = [{"e": event, "p": json.dumps(dict(p), ensure_ascii=False)} for p in payloads]
    if rows:
        await db.execute(
            text("INSERT INTO graph_outbox (event, payload) VALUES (:e, CAST(:p AS jsonb))"), rows
        )
    return len(rows)
