"""온톨로지 라우터(TRD 14.1) — 검증과 위임만 한다. 업무 규칙은 ``ontology.versioning`` 에 있다."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ontobot.api.deps import CurrentUser, Db, require_role
from ontobot.api.errors import ProblemError
from ontobot.db.models.ontology import LinkType, ObjectType, OntologyVersion, PropertyType
from ontobot.ontology import diff as diff_mod
from ontobot.ontology import importer, registry, versioning
from ontobot.ontology.models import (
    LinkSource,
    LinkTypeDef,
    ObjectTypeDef,
    PropertyDef,
    check_document,
)
from ontobot.security.rbac import ROLE_ADMIN, ROLE_MODELER

router = APIRouter(prefix="/ontology", tags=["ontology"])

Modeler = Annotated[CurrentUser, Depends(require_role(ROLE_MODELER))]
Reader = Annotated[CurrentUser, Depends(require_role(ROLE_MODELER, ROLE_ADMIN))]


# ---------------------------------------------------------------- 응답 모델


class IssueOut(BaseModel):
    path: str
    message: str


class PropertyOut(PropertyDef):
    id: int


class ObjectTypeOut(BaseModel):
    id: int
    api_name: str
    display_name: str
    description: str | None
    pk_property: str
    title_property: str
    properties: list[PropertyOut]


class LinkTypeOut(LinkTypeDef):
    id: int


class VersionSummary(BaseModel):
    id: int
    version_no: int
    status: Literal["draft", "published", "archived"]
    parent_id: int | None
    created_at: datetime
    published_at: datetime | None


class VersionDetail(VersionSummary):
    object_types: list[ObjectTypeOut]
    link_types: list[LinkTypeOut]
    # 게시 전 검사 — draft 를 조각조각 편집하는 동안 무엇이 남았는지 보여 준다.
    issues: list[IssueOut]


class ChangeOut(BaseModel):
    kind: Literal["added", "removed", "changed"]
    element: Literal["object_type", "property", "link_type"]
    path: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


def _summary(v: OntologyVersion) -> VersionSummary:
    return VersionSummary(
        id=v.id,
        version_no=v.version_no,
        status=v.status,  # type: ignore[arg-type]
        parent_id=v.parent_id,
        created_at=v.created_at,
        published_at=v.published_at,
    )


def _prop_out(p: PropertyType) -> PropertyOut:
    return PropertyOut(id=p.id, **versioning.prop_def(p).model_dump())


def _type_out(t: ObjectType) -> ObjectTypeOut:
    return ObjectTypeOut(
        id=t.id,
        api_name=t.api_name,
        display_name=t.display_name,
        description=t.description,
        pk_property=t.pk_property,
        title_property=t.title_property,
        properties=[_prop_out(p) for p in t.properties],
    )


def _link_out(lt: LinkType) -> LinkTypeOut:
    return LinkTypeOut(
        id=lt.id,
        api_name=lt.api_name,
        display_name=lt.display_name,
        from_type=lt.from_type,
        to_type=lt.to_type,
        cardinality=lt.cardinality,  # type: ignore[arg-type]
        source=LinkSource.model_validate(lt.source),
    )


def _detail(v: OntologyVersion) -> VersionDetail:
    issues = check_document(versioning.to_document(v))
    return VersionDetail(
        **_summary(v).model_dump(),
        object_types=[_type_out(t) for t in v.object_types],
        link_types=[_link_out(lt) for lt in v.link_types],
        issues=[IssueOut(path=i.path, message=i.message) for i in issues],
    )


@contextmanager
def _domain_errors() -> Iterator[None]:
    """업무 예외 → HTTP. 상태 코드 규칙을 한 곳에 둔다."""
    try:
        yield
    except versioning.VersionNotFound as e:
        raise ProblemError(404, "온톨로지 버전을 찾을 수 없습니다", code="version_not_found") from e
    except versioning.ElementNotFound as e:
        raise ProblemError(
            404, "요소를 찾을 수 없습니다", detail=str(e), code="element_not_found"
        ) from e
    except versioning.VersionNotEditable as e:
        raise ProblemError(
            409,
            "게시·보관된 버전은 수정할 수 없습니다. 새 draft 를 만드세요",
            code="version_not_editable",
        ) from e
    except versioning.ElementConflict as e:
        raise ProblemError(
            409,
            "같은 이름의 요소가 이미 있거나 바꿀 수 없는 값입니다",
            detail=str(e),
            code="element_conflict",
        ) from e
    except versioning.PublishBlocked as e:
        raise ProblemError(
            422,
            "게시 전 검사를 통과하지 못했습니다",
            code="publish_blocked",
            extra={"issues": [i.__dict__ for i in e.issues]},
        ) from e
    except importer.ImportFailed as e:
        raise ProblemError(
            422,
            "정의 문서를 읽지 못했습니다",
            code="import_failed",
            extra={"issues": [i.__dict__ for i in e.issues]},
        ) from e


# ---------------------------------------------------------------- 버전


class CreateVersion(BaseModel):
    # 비우면 빈 draft. 게시본을 고치려면 그 버전 id 를 넣는다(복사본 draft 가 생긴다).
    parent_id: int | None = None


@router.get("/versions", response_model=list[VersionSummary])
async def list_versions(db: Db, _: Reader) -> list[VersionSummary]:
    rows = await db.scalars(select(OntologyVersion).order_by(OntologyVersion.version_no.desc()))
    return [_summary(v) for v in rows]


@router.post("/versions", response_model=VersionDetail, status_code=201)
async def create_version(body: CreateVersion, db: Db, user: Modeler) -> VersionDetail:
    with _domain_errors():
        return _detail(
            await versioning.create_draft(db, actor_id=user.id, parent_id=body.parent_id)
        )


@router.get("/versions/{version_id}", response_model=VersionDetail)
async def get_version(version_id: int, db: Db, _: Reader) -> VersionDetail:
    with _domain_errors():
        return _detail(await versioning.get_version(db, version_id))


@router.get("/versions/{version_id}/diff", response_model=list[ChangeOut])
async def diff_version(
    version_id: int,
    db: Db,
    _: Reader,
    base: Annotated[int, Query(description="비교 기준 버전 id")],
) -> list[ChangeOut]:
    with _domain_errors():
        target = versioning.to_document(await versioning.get_version(db, version_id))
        base_doc = versioning.to_document(await versioning.get_version(db, base))
    return [ChangeOut(**c.__dict__) for c in diff_mod.diff(base_doc, target)]


class ImportBody(BaseModel):
    format: importer.Format = "yaml"
    content: str = Field(min_length=1, max_length=2_000_000)


@router.post("/versions/{version_id}/import", response_model=VersionDetail)
async def import_document(
    version_id: int, body: ImportBody, db: Db, user: Modeler
) -> VersionDetail:
    with _domain_errors():
        doc = importer.parse(body.content, body.format)
        return _detail(await versioning.replace_document(db, version_id, doc, actor_id=user.id))


@router.get("/versions/{version_id}/export")
async def export_document(
    version_id: int, db: Db, _: Reader, format: importer.Format = "yaml"
) -> Response:
    with _domain_errors():
        version = await versioning.get_version(db, version_id)
    text = importer.dump(versioning.to_document(version), format)
    media = "application/x-yaml" if format == "yaml" else "application/json"
    name = f"ontology-v{version.version_no}.{'yaml' if format == 'yaml' else 'json'}"
    return Response(
        text,
        media_type=f"{media}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/versions/{version_id}/publish", response_model=VersionDetail)
async def publish_version(version_id: int, db: Db, user: Modeler) -> VersionDetail:
    with _domain_errors():
        await versioning.publish(db, version_id, actor_id=user.id)
    # 레지스트리는 커밋 뒤에 다시 읽어야 한다 — 커밋 전에 읽으면 롤백된 버전이 캐시에 남는다.
    await db.commit()
    await registry.reload(db)
    return _detail(await versioning.get_version(db, version_id))


# ---------------------------------------------------------------- 객체 타입


class ObjectTypeCreate(ObjectTypeDef):
    pass


class ObjectTypePatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    pk_property: str | None = None
    title_property: str | None = None


@router.post("/versions/{version_id}/object-types", response_model=ObjectTypeOut, status_code=201)
async def create_object_type(
    version_id: int, body: ObjectTypeCreate, db: Db, user: Modeler
) -> ObjectTypeOut:
    with _domain_errors():
        row = await versioning.add_object_type(db, version_id, body, actor_id=user.id)
    return _type_out(row)


@router.patch("/versions/{version_id}/object-types/{type_id}", response_model=ObjectTypeOut)
async def patch_object_type(
    version_id: int, type_id: int, body: ObjectTypePatch, db: Db, user: Modeler
) -> ObjectTypeOut:
    with _domain_errors():
        row = await versioning.update_object_type(
            db, version_id, type_id, actor_id=user.id, **body.model_dump(exclude_unset=True)
        )
    return _type_out(row)


@router.delete("/versions/{version_id}/object-types/{type_id}", status_code=204)
async def delete_object_type(version_id: int, type_id: int, db: Db, user: Modeler) -> None:
    with _domain_errors():
        await versioning.delete_object_type(db, version_id, type_id, actor_id=user.id)


# ---------------------------------------------------------------- 속성


@router.post(
    "/versions/{version_id}/object-types/{type_id}/properties",
    response_model=PropertyOut,
    status_code=201,
)
async def create_property(
    version_id: int, type_id: int, body: PropertyDef, db: Db, user: Modeler
) -> PropertyOut:
    with _domain_errors():
        row = await versioning.add_property(db, version_id, type_id, body, actor_id=user.id)
    return _prop_out(row)


@router.patch(
    "/versions/{version_id}/object-types/{type_id}/properties/{prop_id}", response_model=PropertyOut
)
async def replace_property(
    version_id: int, type_id: int, prop_id: int, body: PropertyDef, db: Db, user: Modeler
) -> PropertyOut:
    with _domain_errors():
        row = await versioning.replace_property(
            db, version_id, type_id, prop_id, body, actor_id=user.id
        )
    return _prop_out(row)


@router.delete(
    "/versions/{version_id}/object-types/{type_id}/properties/{prop_id}", status_code=204
)
async def delete_property(
    version_id: int, type_id: int, prop_id: int, db: Db, user: Modeler
) -> None:
    with _domain_errors():
        await versioning.delete_property(db, version_id, type_id, prop_id, actor_id=user.id)


# ---------------------------------------------------------------- 링크 타입


@router.post("/versions/{version_id}/link-types", response_model=LinkTypeOut, status_code=201)
async def create_link_type(
    version_id: int, body: LinkTypeDef, db: Db, user: Modeler
) -> LinkTypeOut:
    with _domain_errors():
        row = await versioning.add_link_type(db, version_id, body, actor_id=user.id)
    return _link_out(row)


@router.patch("/versions/{version_id}/link-types/{link_id}", response_model=LinkTypeOut)
async def replace_link_type(
    version_id: int, link_id: int, body: LinkTypeDef, db: Db, user: Modeler
) -> LinkTypeOut:
    with _domain_errors():
        row = await versioning.replace_link_type(db, version_id, link_id, body, actor_id=user.id)
    return _link_out(row)


@router.delete("/versions/{version_id}/link-types/{link_id}", status_code=204)
async def delete_link_type(version_id: int, link_id: int, db: Db, user: Modeler) -> None:
    with _domain_errors():
        await versioning.delete_link_type(db, version_id, link_id, actor_id=user.id)
