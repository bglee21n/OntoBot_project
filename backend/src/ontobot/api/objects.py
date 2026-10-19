"""근거 카드(TRD 14.1 객체 묶음) — 인용을 누르면 그 객체의 **지금** 값과
마지막 동기화 시각을 보여 준다.

값은 답변 때 저장해 둔 것이 아니라 열 때 다시 읽는다. 그사이 값이 바뀌었으면 카드가 그것을
보여 주고, 사용자는 마지막 동기화 시각으로 언제 값인지 안다.

읽을 수 없는 타입과 없는 객체는 같은 404 로 거른다(존재 여부도 정보다).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from ontobot.api.deps import Db, User
from ontobot.api.errors import ProblemError
from ontobot.objects.reader import resolve
from ontobot.ontology import registry
from ontobot.query.records import display
from ontobot.security import rbac

router = APIRouter(prefix="/objects", tags=["objects"])


class FieldOut(BaseModel):
    name: str
    display_name: str
    value: str  # 표시 문자열(답변의 데이터 블록과 같은 표기)


class ObjectCard(BaseModel):
    type: str
    type_display_name: str
    pk: str
    title: str
    fields: list[FieldOut]
    synced_at: datetime | None


@router.get("/{object_type}/{pk}", response_model=ObjectCard)
async def card(object_type: str, pk: str, db: Db, user: User) -> ObjectCard:
    schema = registry.current()
    t = schema.document.object_type(object_type) if schema is not None else None
    readable = await rbac.readable_types(db, user.id)
    if t is None or object_type not in readable:
        raise ProblemError(404, "객체를 찾을 수 없습니다", code="object_not_found")
    found = await resolve(db, object_type, [pk], declared=[p.api_name for p in t.properties])
    row = found.get(pk)
    if row is None:
        raise ProblemError(404, "객체를 찾을 수 없습니다", code="object_not_found")
    return ObjectCard(
        type=t.api_name,
        type_display_name=t.display_name,
        pk=row.pk,
        title=str(row.properties.get(t.title_property) or row.pk),
        fields=[
            FieldOut(
                name=p.api_name,
                display_name=p.display_name,
                value=display(p, row.properties.get(p.api_name)),
            )
            for p in t.properties
        ],
        synced_at=row.synced_at,
    )
