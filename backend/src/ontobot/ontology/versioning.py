"""draft 생성·편집·게시(TRD 6.1). 게시된 버전은 수정하지 않는다 — 고치려면 새 draft 를 만든다.

모든 함수는 호출자의 트랜잭션 안에서 돈다(커밋은 라우터 의존성 ``get_db`` 가 한다).
감사 기록도 같은 트랜잭션에 넣어 변경과 기록이 함께 남거나 함께 사라지게 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import BigInteger, delete, func, insert, literal, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ontobot.db.models.ontology import (
    ActionType,
    FunctionType,
    LinkType,
    ObjectType,
    OntologyLayout,
    OntologyVersion,
    PropertyType,
)
from ontobot.ontology import impact
from ontobot.ontology.models import (
    ActionTypeDef,
    FunctionDef,
    Issue,
    LinkSource,
    LinkTypeDef,
    ObjectTypeDef,
    OntologyDocument,
    PropertyDef,
    check_document,
)
from ontobot.security import audit


class VersionNotFound(Exception):
    pass


class VersionNotEditable(Exception):
    """draft 가 아닌 버전을 바꾸려 했다."""


class ElementNotFound(Exception):
    pass


class ElementConflict(Exception):
    """같은 이름의 요소가 이미 있다."""


class PublishBlocked(Exception):
    """교차 검사에 걸려 게시할 수 없다.

    frozen dataclass 로 만들지 말 것 — raise 할 때 ``__traceback__`` 대입이 실패한다.
    """

    def __init__(self, issues: list[Issue]) -> None:
        super().__init__(f"{len(issues)} issue(s)")
        self.issues = issues


# 버전 번호 발급과 게시는 동시에 둘이 돌면 유일 제약 위반(500)으로 끝난다. 트랜잭션 범위
# advisory lock 으로 줄을 세운다 — 커밋·롤백 때 저절로 풀린다.
_VERSION_LOCK_KEY = 0x0B70_0001


async def _lock_versions(db: AsyncSession) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _VERSION_LOCK_KEY})


# ---------------------------------------------------------------- 읽기


async def get_version(db: AsyncSession, version_id: int) -> OntologyVersion:
    version = await db.scalar(
        select(OntologyVersion)
        .where(OntologyVersion.id == version_id)
        .options(
            selectinload(OntologyVersion.object_types).selectinload(ObjectType.properties),
            selectinload(OntologyVersion.link_types),
            selectinload(OntologyVersion.action_types),
            selectinload(OntologyVersion.functions),
        )
        .execution_options(populate_existing=True)
    )
    if version is None:
        raise VersionNotFound(version_id)
    return version


async def published_version_id(db: AsyncSession) -> int | None:
    return await db.scalar(select(OntologyVersion.id).where(OntologyVersion.status == "published"))


def to_document(version: OntologyVersion) -> OntologyDocument:
    """DB 행 → 정의 문서. 교차 참조는 검사하지 않는다(draft 는 중간 상태일 수 있다)."""
    return OntologyDocument(
        object_types=[
            ObjectTypeDef(
                api_name=t.api_name,
                display_name=t.display_name,
                description=t.description,
                pk_property=t.pk_property,
                title_property=t.title_property,
                properties=[prop_def(p) for p in t.properties],
                deprecated=t.deprecated,
            )
            for t in version.object_types
        ],
        link_types=[
            LinkTypeDef(
                api_name=lt.api_name,
                display_name=lt.display_name,
                from_type=lt.from_type,
                to_type=lt.to_type,
                cardinality=lt.cardinality,  # type: ignore[arg-type]
                source=LinkSource.model_validate(lt.source),
                deprecated=lt.deprecated,
            )
            for lt in version.link_types
        ],
        action_types=[action_def(a) for a in version.action_types],
        functions=[function_def(f) for f in version.functions],
    )


def action_def(a: ActionType) -> ActionTypeDef:
    return ActionTypeDef.model_validate(
        {"api_name": a.api_name, "display_name": a.display_name, **a.definition}
    )


def _action_type_row(a: ActionTypeDef) -> ActionType:
    return ActionType(
        api_name=a.api_name,
        display_name=a.display_name,
        definition=a.model_dump(mode="json", exclude={"api_name", "display_name"}),
    )


def function_def(f: FunctionType) -> FunctionDef:
    return FunctionDef.model_validate(
        {"api_name": f.api_name, "display_name": f.display_name, **f.definition}
    )


def _function_row(f: FunctionDef) -> FunctionType:
    return FunctionType(
        api_name=f.api_name,
        display_name=f.display_name,
        definition=f.model_dump(mode="json", exclude={"api_name", "display_name"}),
    )


def prop_def(p: PropertyType) -> PropertyDef:
    return PropertyDef(
        api_name=p.api_name,
        display_name=p.display_name,
        description=p.description,
        data_type=p.data_type,  # type: ignore[arg-type]
        enum_values=p.enum_values,
        nullable=p.nullable,
        indexed=p.indexed,
        pii_kind=p.pii_kind,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------- draft 생성·통째 교체


async def create_draft(
    db: AsyncSession, *, actor_id: int | None, parent_id: int | None
) -> OntologyVersion:
    """새 draft. ``parent_id`` 가 있으면 그 버전의 정의를 복사한다(게시본을 고치는 유일한 길)."""
    doc = OntologyDocument()
    if parent_id is not None:
        doc = to_document(await get_version(db, parent_id))
    await _lock_versions(db)
    last_no = await db.scalar(select(func.coalesce(func.max(OntologyVersion.version_no), 0)))
    next_no = int(last_no or 0) + 1
    # 자식 행은 생성자에서 함께 넘긴다. flush 뒤에 컬렉션을 건드리면 비동기 세션에서
    # 지연 로딩이 일어나 MissingGreenlet 으로 터진다.
    version = OntologyVersion(
        version_no=next_no,
        status="draft",
        parent_id=parent_id,
        created_by=actor_id,
        object_types=[_object_type_row(t) for t in doc.object_types],
        link_types=[_link_type_row(lt) for lt in doc.link_types],
        action_types=[_action_type_row(a) for a in doc.action_types],
        functions=[_function_row(f) for f in doc.functions],
    )
    db.add(version)
    await db.flush()
    if parent_id is not None:
        await _copy_layout(db, parent_id, version.id)
    audit.record(
        db,
        audit.DRAFT_CREATED,
        actor_id=actor_id,
        target=f"ontology_version:{version.id}",
        payload={
            "version_no": next_no,
            "parent_id": parent_id,
            "object_types": [t.api_name for t in doc.object_types],
        },
    )
    return await get_version(db, version.id)


async def replace_document(
    db: AsyncSession, version_id: int, doc: OntologyDocument, *, actor_id: int | None
) -> OntologyVersion:
    """임포트 — draft 의 정의를 문서로 통째 바꾼다. 문서는 이미 교차 검사를 통과했다."""
    version = await _draft(db, version_id)
    before = {t.api_name for t in version.object_types}
    version.object_types.clear()
    version.link_types.clear()
    version.action_types.clear()
    version.functions.clear()
    await db.flush()
    _write_document(version, doc)
    await db.flush()
    after = {t.api_name for t in doc.object_types}
    audit.record(
        db,
        audit.DRAFT_IMPORTED,
        actor_id=actor_id,
        target=f"ontology_version:{version_id}",
        payload={"added_types": sorted(after - before), "removed_types": sorted(before - after)},
    )
    return await get_version(db, version_id)


def _write_document(version: OntologyVersion, doc: OntologyDocument) -> None:
    for t in doc.object_types:
        version.object_types.append(_object_type_row(t))
    for lt in doc.link_types:
        version.link_types.append(_link_type_row(lt))
    for a in doc.action_types:
        version.action_types.append(_action_type_row(a))
    for f in doc.functions:
        version.functions.append(_function_row(f))


def _object_type_row(t: ObjectTypeDef) -> ObjectType:
    return ObjectType(
        api_name=t.api_name,
        display_name=t.display_name,
        description=t.description,
        pk_property=t.pk_property,
        title_property=t.title_property,
        properties=[PropertyType(**p.model_dump()) for p in t.properties],
        deprecated=t.deprecated,
    )


def _link_type_row(lt: LinkTypeDef) -> LinkType:
    return LinkType(
        api_name=lt.api_name,
        display_name=lt.display_name,
        from_type=lt.from_type,
        to_type=lt.to_type,
        cardinality=lt.cardinality,
        source=lt.source.model_dump(mode="json", exclude_none=True),
        deprecated=lt.deprecated,
    )


# ---------------------------------------------------------------- 요소 편집 (draft 만)


async def _draft(db: AsyncSession, version_id: int) -> OntologyVersion:
    version = await get_version(db, version_id)
    if version.status != "draft":
        raise VersionNotEditable(version_id)
    return version


def _edited(db: AsyncSession, actor_id: int, version_id: int, op: str, name: str) -> None:
    # '신규 객체 타입 구성 소요 시간'(TRD 13.2)이 이 기록으로 타입이 처음 나타난 시각을 찾는다.
    audit.record(
        db,
        audit.DRAFT_EDITED,
        actor_id=actor_id,
        target=f"ontology_version:{version_id}",
        payload={"op": op, "name": name},
    )


def _object_type(version: OntologyVersion, type_id: int) -> ObjectType:
    t = next((t for t in version.object_types if t.id == type_id), None)
    if t is None:
        raise ElementNotFound(f"object_type:{type_id}")
    return t


async def add_object_type(
    db: AsyncSession, version_id: int, t: ObjectTypeDef, *, actor_id: int
) -> ObjectType:
    version = await _draft(db, version_id)
    if any(x.api_name == t.api_name for x in version.object_types):
        raise ElementConflict(t.api_name)
    row = _object_type_row(t)
    version.object_types.append(row)
    await db.flush()
    _edited(db, actor_id, version_id, "object_type.create", t.api_name)
    return row


async def update_object_type(
    db: AsyncSession,
    version_id: int,
    type_id: int,
    *,
    actor_id: int,
    display_name: str | None = None,
    description: str | None = None,
    pk_property: str | None = None,
    title_property: str | None = None,
    deprecated: bool | None = None,
) -> ObjectType:
    """``api_name`` 은 바꾸지 않는다 — 매핑·권한·링크가 그 문자열로 타입을 가리킨다."""
    version = await _draft(db, version_id)
    row = _object_type(version, type_id)
    for field, value in (
        ("display_name", display_name),
        ("description", description),
        ("pk_property", pk_property),
        ("title_property", title_property),
        ("deprecated", deprecated),
    ):
        if value is not None:
            setattr(row, field, value)
    await db.flush()
    _edited(db, actor_id, version_id, "object_type.update", row.api_name)
    return row


async def delete_object_type(
    db: AsyncSession, version_id: int, type_id: int, *, actor_id: int
) -> None:
    version = await _draft(db, version_id)
    row = _object_type(version, type_id)
    version.object_types.remove(row)
    await db.flush()
    _edited(db, actor_id, version_id, "object_type.delete", row.api_name)


async def add_property(
    db: AsyncSession, version_id: int, type_id: int, p: PropertyDef, *, actor_id: int
) -> PropertyType:
    version = await _draft(db, version_id)
    owner = _object_type(version, type_id)
    if any(x.api_name == p.api_name for x in owner.properties):
        raise ElementConflict(f"{owner.api_name}.{p.api_name}")
    row = PropertyType(**p.model_dump())
    owner.properties.append(row)
    await db.flush()
    _edited(db, actor_id, version_id, "property.create", f"{owner.api_name}.{p.api_name}")
    return row


async def replace_property(
    db: AsyncSession, version_id: int, type_id: int, prop_id: int, p: PropertyDef, *, actor_id: int
) -> PropertyType:
    """속성 정의를 통째로 바꾼다. 이름 변경은 받지 않는다(동기화 매핑이 그 이름을 쓴다)."""
    version = await _draft(db, version_id)
    owner = _object_type(version, type_id)
    row = next((x for x in owner.properties if x.id == prop_id), None)
    if row is None:
        raise ElementNotFound(f"property:{prop_id}")
    if row.api_name != p.api_name:
        raise ElementConflict(
            "속성 api_name 은 바꿀 수 없습니다 — 새 속성을 만들고 옛 것을 지우세요"
        )
    for field, value in p.model_dump().items():
        setattr(row, field, value)
    await db.flush()
    _edited(db, actor_id, version_id, "property.update", f"{owner.api_name}.{p.api_name}")
    return row


async def delete_property(
    db: AsyncSession, version_id: int, type_id: int, prop_id: int, *, actor_id: int
) -> None:
    version = await _draft(db, version_id)
    owner = _object_type(version, type_id)
    row = next((x for x in owner.properties if x.id == prop_id), None)
    if row is None:
        raise ElementNotFound(f"property:{prop_id}")
    owner.properties.remove(row)
    await db.flush()
    _edited(db, actor_id, version_id, "property.delete", f"{owner.api_name}.{row.api_name}")


async def add_link_type(
    db: AsyncSession, version_id: int, lt: LinkTypeDef, *, actor_id: int
) -> LinkType:
    version = await _draft(db, version_id)
    if any(x.api_name == lt.api_name for x in version.link_types):
        raise ElementConflict(lt.api_name)
    row = _link_type_row(lt)
    version.link_types.append(row)
    await db.flush()
    _edited(db, actor_id, version_id, "link_type.create", lt.api_name)
    return row


async def replace_link_type(
    db: AsyncSession, version_id: int, link_id: int, lt: LinkTypeDef, *, actor_id: int
) -> LinkType:
    version = await _draft(db, version_id)
    row = next((x for x in version.link_types if x.id == link_id), None)
    if row is None:
        raise ElementNotFound(f"link_type:{link_id}")
    if row.api_name != lt.api_name:
        raise ElementConflict("링크 타입 api_name 은 바꿀 수 없습니다")
    new = _link_type_row(lt)
    for field in ("display_name", "from_type", "to_type", "cardinality", "source", "deprecated"):
        setattr(row, field, getattr(new, field))
    await db.flush()
    _edited(db, actor_id, version_id, "link_type.update", lt.api_name)
    return row


async def delete_link_type(
    db: AsyncSession, version_id: int, link_id: int, *, actor_id: int
) -> None:
    version = await _draft(db, version_id)
    row = next((x for x in version.link_types if x.id == link_id), None)
    if row is None:
        raise ElementNotFound(f"link_type:{link_id}")
    version.link_types.remove(row)
    await db.flush()
    _edited(db, actor_id, version_id, "link_type.delete", row.api_name)


async def add_action_type(
    db: AsyncSession, version_id: int, a: ActionTypeDef, *, actor_id: int
) -> ActionType:
    version = await _draft(db, version_id)
    if any(x.api_name == a.api_name for x in version.action_types):
        raise ElementConflict(a.api_name)
    row = _action_type_row(a)
    version.action_types.append(row)
    await db.flush()
    _edited(db, actor_id, version_id, "action_type.create", a.api_name)
    return row


async def replace_action_type(
    db: AsyncSession, version_id: int, action_id: int, a: ActionTypeDef, *, actor_id: int
) -> ActionType:
    version = await _draft(db, version_id)
    row = next((x for x in version.action_types if x.id == action_id), None)
    if row is None:
        raise ElementNotFound(f"action_type:{action_id}")
    if row.api_name != a.api_name:
        raise ElementConflict("Action api_name 은 바꿀 수 없습니다")
    new = _action_type_row(a)
    row.display_name = new.display_name
    row.definition = new.definition
    await db.flush()
    _edited(db, actor_id, version_id, "action_type.update", a.api_name)
    return row


async def delete_action_type(
    db: AsyncSession, version_id: int, action_id: int, *, actor_id: int
) -> None:
    version = await _draft(db, version_id)
    row = next((x for x in version.action_types if x.id == action_id), None)
    if row is None:
        raise ElementNotFound(f"action_type:{action_id}")
    version.action_types.remove(row)
    await db.flush()
    _edited(db, actor_id, version_id, "action_type.delete", row.api_name)


async def add_function(
    db: AsyncSession, version_id: int, f: FunctionDef, *, actor_id: int
) -> FunctionType:
    version = await _draft(db, version_id)
    if any(x.api_name == f.api_name for x in version.functions):
        raise ElementConflict(f.api_name)
    row = _function_row(f)
    version.functions.append(row)
    await db.flush()
    _edited(db, actor_id, version_id, "function.create", f.api_name)
    return row


async def replace_function(
    db: AsyncSession, version_id: int, function_id: int, f: FunctionDef, *, actor_id: int
) -> FunctionType:
    version = await _draft(db, version_id)
    row = next((x for x in version.functions if x.id == function_id), None)
    if row is None:
        raise ElementNotFound(f"function:{function_id}")
    if row.api_name != f.api_name:
        raise ElementConflict("Function api_name 은 바꿀 수 없습니다")
    new = _function_row(f)
    row.display_name = new.display_name
    row.definition = new.definition
    await db.flush()
    _edited(db, actor_id, version_id, "function.update", f.api_name)
    return row


async def delete_function(
    db: AsyncSession, version_id: int, function_id: int, *, actor_id: int
) -> None:
    version = await _draft(db, version_id)
    row = next((x for x in version.functions if x.id == function_id), None)
    if row is None:
        raise ElementNotFound(f"function:{function_id}")
    version.functions.remove(row)
    await db.flush()
    _edited(db, actor_id, version_id, "function.delete", row.api_name)


# ---------------------------------------------------------------- 그래프 배치(정의 밖 — 감사 없음)

LAYOUT_LIMIT = 2000


@dataclass(frozen=True)
class NodePosition:
    kind: str
    name: str
    x: float
    y: float


async def get_layout(db: AsyncSession, version_id: int) -> list[NodePosition]:
    await get_version(db, version_id)
    rows = await db.scalars(
        select(OntologyLayout)
        .where(OntologyLayout.version_id == version_id)
        .order_by(OntologyLayout.element_kind, OntologyLayout.element_name)
    )
    return [NodePosition(r.element_kind, r.element_name, r.x, r.y) for r in rows]


async def replace_layout(
    db: AsyncSession, version_id: int, nodes: list[NodePosition], *, actor_id: int
) -> list[NodePosition]:
    """배치를 통째로 바꾼다. 게시·보관본도 고칠 수 있다 — 정의가 아니라 화면 상태다.

    요소 이름은 검사하지 않는다(지운 타입의 좌표가 남아도 화면이 무시한다). 다음 draft 로 복사된다.
    """
    await get_version(db, version_id)
    await db.execute(delete(OntologyLayout).where(OntologyLayout.version_id == version_id))
    for n in nodes:
        db.add(
            OntologyLayout(
                version_id=version_id,
                element_kind=n.kind,
                element_name=n.name,
                x=n.x,
                y=n.y,
                updated_by=actor_id,
            )
        )
    await db.flush()
    return await get_layout(db, version_id)


async def _copy_layout(db: AsyncSession, parent_id: int, version_id: int) -> None:
    src = select(
        literal(version_id, BigInteger),
        OntologyLayout.element_kind,
        OntologyLayout.element_name,
        OntologyLayout.x,
        OntologyLayout.y,
    ).where(OntologyLayout.version_id == parent_id)
    await db.execute(
        insert(OntologyLayout).from_select(
            ["version_id", "element_kind", "element_name", "x", "y"], src
        )
    )


# ---------------------------------------------------------------- 게시


async def publish(db: AsyncSession, version_id: int, *, actor_id: int | None) -> OntologyVersion:
    """교차 검사를 통과한 draft 만 게시한다. 이전 게시본은 archived 가 된다.

    게시 버전이 언제나 하나뿐이라는 것은 부분 유일 인덱스가 보장한다 — 그래서 옛 것을 먼저
    내린 뒤 새 것을 올린다(순서를 바꾸면 같은 트랜잭션 안에서도 인덱스 위반이다).
    """
    await _lock_versions(db)
    version = await _draft(db, version_id)
    doc = to_document(version)
    issues = check_document(doc)
    if issues:
        raise PublishBlocked(issues)
    previous = await published_version_id(db)
    if previous is not None:
        # 정의 문서 밖에서 깨지는 참조(행 정책이 지우는 속성을 쓴다 등, Phase 17a)도 막는다.
        base = to_document(await get_version(db, previous))
        blocked = impact.blocking_issues(await impact.change_impact(db, base, doc))
        if blocked:
            raise PublishBlocked(blocked)
    if previous is not None:
        await db.execute(
            update(OntologyVersion).where(OntologyVersion.id == previous).values(status="archived")
        )
    version.status = "published"
    version.published_at = datetime.now(UTC)
    version.content_hash = doc.content_hash()
    await db.flush()
    audit.record(
        db,
        audit.VERSION_PUBLISHED,
        actor_id=actor_id,
        target=f"ontology_version:{version_id}",
        payload={
            "version_no": version.version_no,
            "previous_id": previous,
            "content_hash": version.content_hash,
            "object_types": [t.api_name for t in doc.object_types],
        },
    )
    return version
