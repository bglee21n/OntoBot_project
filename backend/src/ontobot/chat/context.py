"""대화 맥락(TRD 7.1 1단계, PRD 5.2) — **식별자만** 저장하고 값은 매번 다시 읽는다.

값을 저장해 두면 그사이 동기화로 바뀐 값을 옛 값 그대로 답하게 된다. 그래서 맥락에는 (타입, pk) 만
남기고, 플래너에 보여 줄 제목은 지금 다시 읽는다. 읽을 권한이 없어진 타입·삭제된 객체는 맥락에서
조용히 빠진다(없는 것을 가리키는 맥락은 쓸모가 없다).

맥락은 직전 ``ok`` 객체 목록 답의 객체로 바꿔 쓴다. 집계·0건·실패 답은 맥락을 바꾸지 않는다 —
"그 중"이 가리킬 객체 목록이 없기 때문이다.
"""

from __future__ import annotations

from collections.abc import Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.chat import ConversationContext
from ontobot.objects.reader import resolve
from ontobot.ontology.models import OntologyDocument

MAX_REFS = 20


async def load(db: AsyncSession, conversation_id: int) -> list[dict[str, str]]:
    row = await db.scalar(
        select(ConversationContext).where(ConversationContext.conversation_id == conversation_id)
    )
    return list(row.refs) if row is not None else []


async def save(db: AsyncSession, conversation_id: int, refs: list[dict[str, str]]) -> None:
    row = await db.scalar(
        select(ConversationContext).where(ConversationContext.conversation_id == conversation_id)
    )
    clean = [{"type": r["type"], "pk": r["pk"]} for r in refs[:MAX_REFS]]
    if row is None:
        db.add(ConversationContext(conversation_id=conversation_id, refs=clean))
    else:
        row.refs = clean


async def with_titles(
    db: AsyncSession, doc: OntologyDocument, readable: Set[str], refs: list[dict[str, str]]
) -> list[dict[str, str]]:
    """플래너에 줄 맥락 — 읽을 수 있고 아직 있는 객체만, 지금 제목과 함께."""
    out: list[dict[str, str]] = []
    by_type: dict[str, list[str]] = {}
    for r in refs:
        if r["type"] in readable:
            by_type.setdefault(r["type"], []).append(r["pk"])
    for type_name, pks in by_type.items():
        t = doc.object_type(type_name)
        if t is None:
            continue
        title_prop = None
        if t.title_property:
            p = t.property(t.title_property)
            # PII 제목은 플래너에 싣지 않는다(외부 공급자로 갈 수 있다, TRD 9.2) — pk 로 대신한다.
            title_prop = t.title_property if p is not None and not p.pii_kind else None
        found = await resolve(db, type_name, pks, declared=[title_prop] if title_prop else [])
        for pk in pks:
            row = found.get(pk)
            if row is not None:
                title = str(row.properties.get(title_prop) or pk) if title_prop else pk
                out.append({"type": type_name, "pk": pk, "title": title})
    return out
