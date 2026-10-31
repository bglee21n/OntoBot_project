"""워크숍 게시(TRD 12절) — 모델러 승인 → 확정 요소로 새 draft 버전 + 골든 질의 초안.

- 새 draft 는 **현재 게시 버전을 부모로** 복사한 뒤 확정 요소를 더한다. 이후는 온톨로지 게시
  흐름(5.1) 그대로다 — 여기서 게시본을 만들지 않는다. 키 속성처럼 인터뷰가 모르는 것은 draft 의
  게시 전 검사가 알려 준다.
- 합치기는 이름의 **정확 일치**만 본다(유사도 병합 금지, PRD 5.8). 같은 이름의 타입은 같은 타입이다.
  같은 이름의 속성·링크가 정의까지 같으면 이미 있는 것으로 넘어가고, 다르면 게시를 막는다
  (조용히 덮지 않는다).
- 확정(``confirmed``/``edited``)된 골든 질의만 ``golden_item(source=workshop)`` 으로 복사한다.
  기대 답변은 현업이 확정한 문장이고, 채점용 ``required_values`` 는 비워 둔다(Phase 7 에서
  모델러가 채운다).
- Action 후보는 복사하지 않는다 — ``draft_element`` 에 남아 v1 Action Type 정의에 쓰인다.
- 여러 세션을 한 draft 로(Phase 18b, ``publish_many``): 세션 순서대로 확정 요소를 이어 같은
  ``merge`` 에 넣는다 — 세션끼리 같은 이름은 정의가 같을 때만 하나가 되고, 다르면 막힌다. 병합 후보
  보기는 ``workshop.merge``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.evaluation import GoldenItem, GoldenSet
from ontobot.db.models.workshop import DraftElement, InterviewSession
from ontobot.ontology import versioning
from ontobot.ontology.models import (
    Issue,
    LinkSource,
    LinkTypeDef,
    ObjectTypeDef,
    OntologyDocument,
)
from ontobot.security import audit
from ontobot.workshop import elements
from ontobot.workshop.elements import (
    LinkTypePayload,
    ObjectTypePayload,
    PayloadInvalid,
    PropertyPayload,
)

CONFIRMED = frozenset({"confirmed", "edited"})


class NotSubmitted(Exception):
    """제출되지 않았거나 이미 게시된 세션이다."""


class PublishBlocked(Exception):
    def __init__(self, issues: list[Issue]) -> None:
        super().__init__(f"{len(issues)} issue(s)")
        self.issues = issues


@dataclass
class Merged:
    document: OntologyDocument
    issues: list[Issue] = field(default_factory=list)
    added: dict[str, list[str]] = field(
        default_factory=lambda: {"object_type": [], "property": [], "link_type": []}
    )
    reused: list[str] = field(default_factory=list)


def merge(base: OntologyDocument, confirmed: list[tuple[int, str, dict[str, Any]]]) -> Merged:
    """``base`` 에 확정 요소 ``(element_id, kind, payload)`` 를 더한 문서. 순수 함수.

    타입 → 속성 → 링크 순서로 더한다(속성·링크가 같은 인터뷰의 새 타입을 가리킬 수 있다).
    """
    doc = base.model_copy(deep=True)
    out = Merged(doc)

    def bad(eid: int, message: str) -> None:
        out.issues.append(Issue(f"draft_element.{eid}", message))

    def payload_of[M: BaseModel](
        eid: int, kind: str, raw: dict[str, Any], model: type[M]
    ) -> M | None:
        try:
            return model.model_validate(elements.validate(kind, raw))
        except PayloadInvalid as e:
            bad(eid, "; ".join(e.errors))
            return None

    for eid, kind, raw in confirmed:
        if kind != "object_type" or (t := payload_of(eid, kind, raw, ObjectTypePayload)) is None:
            continue
        if doc.object_type(t.api_name) is not None:
            out.reused.append(t.api_name)
            continue
        doc.object_types.append(
            ObjectTypeDef(
                api_name=t.api_name,
                display_name=t.display_name,
                description=t.description,
                pk_property=t.pk_property,
                title_property=t.title_property,
            )
        )
        out.added["object_type"].append(t.api_name)

    for eid, kind, raw in confirmed:
        if kind != "property" or (p := payload_of(eid, kind, raw, PropertyPayload)) is None:
            continue
        owner = doc.object_type(p.object_type)
        if owner is None:
            bad(eid, f"속성 '{p.api_name}' 의 타입 '{p.object_type}' 이 없습니다")
            continue
        new = p.to_def()
        old = owner.property(p.api_name)
        if old is None:
            owner.properties.append(new)
            out.added["property"].append(f"{p.object_type}.{p.api_name}")
        elif (old.data_type, old.enum_values) != (new.data_type, new.enum_values):
            bad(
                eid,
                f"'{p.object_type}.{p.api_name}' 이 이미 다른 자료형({old.data_type})으로 있습니다",
            )
        else:
            out.reused.append(f"{p.object_type}.{p.api_name}")

    for eid, kind, raw in confirmed:
        if kind != "link_type" or (lt := payload_of(eid, kind, raw, LinkTypePayload)) is None:
            continue
        missing = [n for n in (lt.from_type, lt.to_type) if doc.object_type(n) is None]
        if missing:
            bad(eid, f"링크 '{lt.api_name}' 의 끝 타입이 없습니다: {', '.join(missing)}")
            continue
        old_link = next((x for x in doc.link_types if x.api_name == lt.api_name), None)
        if old_link is not None:
            if (old_link.from_type, old_link.to_type) == (lt.from_type, lt.to_type):
                out.reused.append(lt.api_name)
            else:
                bad(eid, f"링크 '{lt.api_name}' 이 이미 다른 타입 사이에 있습니다")
            continue
        source = (
            LinkSource(kind="fk_property", property=lt.fk_property)
            if lt.fk_property
            else LinkSource(kind="join_mapping", config={"origin": "workshop"})
        )
        doc.link_types.append(
            LinkTypeDef(
                api_name=lt.api_name,
                display_name=lt.display_name,
                from_type=lt.from_type,
                to_type=lt.to_type,
                cardinality=lt.cardinality,
                source=source,
            )
        )
        out.added["link_type"].append(lt.api_name)
    return out


@dataclass(frozen=True)
class Published:
    version_id: int
    golden_set_id: int | None
    golden_items: int
    added: dict[str, list[str]]
    reused: list[str]


async def publish(
    db: AsyncSession,
    session: InterviewSession,
    els: list[DraftElement],
    *,
    actor_id: int,
) -> Published:
    """세션 하나 — ``publish_many`` 의 한 세션짜리."""
    return await publish_many(db, [(session, els)], actor_id=actor_id)


async def publish_many(
    db: AsyncSession,
    sessions: list[tuple[InterviewSession, list[DraftElement]]],
    *,
    actor_id: int,
) -> Published:
    """세션들은 잠근 행이어야 한다(같은 세션을 두 번 게시하지 않게). 모두 제출된 상태여야 한다."""
    for session, _ in sessions:
        if session.status != "submitted":
            raise NotSubmitted(session.id)
    confirmed = [e for _, els in sessions for e in els if e.status in CONFIRMED]
    parent_id = await versioning.published_version_id(db)
    base = (
        versioning.to_document(await versioning.get_version(db, parent_id))
        if parent_id is not None
        else OntologyDocument()
    )
    merged = merge(base, [(e.id, e.kind, e.payload) for e in confirmed])
    if merged.issues:
        raise PublishBlocked(merged.issues)

    draft = await versioning.create_draft(db, actor_id=actor_id, parent_id=parent_id)
    await versioning.replace_document(db, draft.id, merged.document, actor_id=actor_id)

    ids = [session.id for session, _ in sessions]
    # 같은 질문(정확 일치)은 한 번만 — 여러 세션이 같은 질문을 확정할 수 있다.
    queries = list(
        {e.payload["question"]: e for e in confirmed if e.kind == "golden_query"}.values()
    )
    golden_set_id: int | None = None
    if queries:
        name = (
            f"workshop-session-{ids[0]}"
            if len(ids) == 1
            else "workshop-merge-" + "-".join(map(str, ids))
        )
        gs = GoldenSet(name=name, ontology_version_id=draft.id)
        db.add(gs)
        await db.flush()
        golden_set_id = gs.id
        for q in queries:
            answerable = bool(q.payload.get("answerable", True))
            db.add(
                GoldenItem(
                    golden_set_id=gs.id,
                    question=q.payload["question"],
                    answerable=answerable,
                    # 가안 — 답할 수 없는 질문은 조회 실패로 밝혀야 한다(Phase 7 확정).
                    expected_status="ok" if answerable else "failed",
                    expected_answer=q.payload.get("expected_answer"),
                    required_values=[],
                    forbidden_values=[],
                    source="workshop",
                )
            )
    for session, els in sessions:
        session.status = "published"
        session.published_version_id = draft.id
        audit.record(
            db,
            audit.WORKSHOP_PUBLISHED,
            actor_id=actor_id,
            target=f"interview_session:{session.id}",
            payload={
                "version_id": draft.id,
                "parent_id": parent_id,
                "merged_sessions": ids,
                "added": merged.added,
                "reused": merged.reused,
                "golden_set_id": golden_set_id,
                "golden_items": len(queries),
                "action_candidates": sum(
                    1 for e in els if e.kind == "action_candidate" and e.status in CONFIRMED
                ),
            },
        )
    await db.flush()
    return Published(draft.id, golden_set_id, len(queries), merged.added, merged.reused)
