"""공개 REST API(Phase 17b, PRD 5.11 P1, TRD 17.1 결정 14) — API 키로만 부른다.

- 인증은 ``Authorization: Bearer <API 키>`` 뿐(``deps.api_caller``). 호출자는 키의 서비스
  계정이고, 무엇을 읽고 바꿀 수 있는지는 그 계정의 역할·데이터 그룹이 정한다 — 화면과 **같은
  경로**(파이프라인·근거 카드·Action 미리보기)를 그대로 부른다. 스코프는 그 위에서 좁히기만 한다.
- 질의는 SSE 가 아니라 JSON 한 번이다. 대화 맥락은 없다(질문 하나 = 요청 하나). 트레이스는 남긴다.
- Action 은 **요청(미리보기)까지만** 이다 — 승인·실행은 사람이 화면에서 한다(채팅 Action 과 같은
  규칙). 질의가 Action 요청 말이면 ``actions:request`` 스코프가 있을 때만 미리보기를 만든다
  (``origin=api``).
- 공개 OpenAPI 문서는 이 라우터만 담는다(``/public/openapi.json``). 폐쇄망이라 문서 화면
  (Swagger UI)은 두지 않는다 — 외부 스크립트를 부른다.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Request
from pydantic import BaseModel, Field

from ontobot.actions import params as action_params
from ontobot.actions import runner
from ontobot.api import actions as actions_api
from ontobot.api import objects as objects_api
from ontobot.api.deps import CurrentUser, Db, api_caller, require_scope
from ontobot.chat import pipeline
from ontobot.chat.stream import CitationData
from ontobot.config import app_version, get_settings
from ontobot.llm.config import load as load_llm_config
from ontobot.llm.gateway import LLMGateway
from ontobot.notify import config as notify_config
from ontobot.ontology import registry
from ontobot.security import rbac
from ontobot.security.rbac import SCOPE_ACTIONS_REQUEST, SCOPE_OBJECTS_READ, SCOPE_QUERY
from ontobot.tracing import recorder

TAG = "public"
router = APIRouter(prefix="/public", tags=[TAG])

QueryCaller = Annotated[CurrentUser, Depends(require_scope(SCOPE_QUERY))]
ObjectsCaller = Annotated[CurrentUser, Depends(require_scope(SCOPE_OBJECTS_READ))]
ActionsCaller = Annotated[CurrentUser, Depends(require_scope(SCOPE_ACTIONS_REQUEST))]
# 같은 요청 안에서 FastAPI 가 한 번만 푼다 — 스코프를 함께 보려고 쓴다.
Caller = Annotated[tuple[CurrentUser, frozenset[str]], Depends(api_caller)]


class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class QueryActionOut(BaseModel):
    """질문이 Action 요청 말이었을 때 — 대조 결과와 (만들었으면) 미리보기 기록 id."""

    action_type: str
    ready: bool
    issues: list[str]
    run_id: int | None = None


class QueryOut(BaseModel):
    status: Literal["ok", "empty", "failed", "not_query", "action_preview"]
    answer: str
    grade: int | None
    failure_reason: str | None
    conditions: str | None
    citations: list[CitationData]
    estimated_sentences: list[int]
    trace_id: int
    action: QueryActionOut | None = None


async def _preview_from_question(
    db: Db, user: CurrentUser, res: pipeline.PipelineResult, can_request: bool
) -> QueryActionOut | None:
    a: dict[str, Any] | None = res.action
    if a is None:
        return None
    out = QueryActionOut(action_type=a["action_type"], ready=bool(a["ready"]), issues=a["issues"])
    if not out.ready or not can_request:
        return out
    try:
        run = await runner.preview(
            db,
            runner.Actor(user.id, user.roles),
            action=a["action_type"],
            target_pk=a["target_pk"],
            params=a["params"],
            request_id=f"api-{uuid.uuid4().hex}",
            cfg=notify_config.load(get_settings().notify_config_path),
            origin="api",
        )
    except runner.ActionError as e:
        return out.model_copy(update={"ready": False, "issues": [*out.issues, str(e), *e.errors]})
    return out.model_copy(update={"run_id": run.id})


@router.post("/query", response_model=QueryOut)
async def query(
    body: QueryIn, request: Request, db: Db, user: QueryCaller, caller: Caller
) -> QueryOut:
    """질문 하나를 화면 채팅과 같은 파이프라인으로 답한다(근거 대조·등급 포함, JSON 한 번)."""
    settings = get_settings()
    access = await rbac.access(db, user.id)
    schema = registry.current()
    can_request = rbac.has_scope(caller[1], SCOPE_ACTIONS_REQUEST)
    actions = (
        action_params.requestable(schema.document, access.readable, user.roles, access.editable)
        if schema is not None
        else frozenset()
    )
    llm = LLMGateway(load_llm_config(settings.llm_config_path))
    res = await pipeline.run(
        db,
        llm,
        schema,
        access.readable,
        body.question,
        grade2_enabled=settings.grade2_enabled,
        graph_driver=getattr(request.app.state, "graph", None),
        actions=actions,
        access=access,
        documents=await rbac.document_groups(db, user.id),
    )
    action = await _preview_from_question(db, user, res, can_request)
    trace = await recorder.record(
        db,
        user_id=user.id,
        message_id=None,
        result=res,
        llm_calls=llm.calls,
        pii_values=llm.protected,
    )
    return QueryOut(
        status=res.status,
        answer=res.answer,
        grade=res.grade,
        failure_reason=res.failure_reason,
        conditions=res.conditions,
        citations=[CitationData.model_validate(c) for c in res.citations],
        estimated_sentences=res.estimated_sentences,
        trace_id=trace.id,
        action=action,
    )


@router.get("/objects/{object_type}/{pk}", response_model=objects_api.ObjectCard)
async def object_card(
    object_type: str, pk: str, db: Db, user: ObjectsCaller
) -> objects_api.ObjectCard:
    """근거 카드와 같은 읽기(권한·숨김·행 정책·편집 레이어 그대로)."""
    return await objects_api.card(object_type, pk, db, user)


@router.post("/actions/{name}/requests", response_model=actions_api.ActionRunOut, status_code=201)
async def request_action(
    name: str, body: actions_api.PreviewRequest, db: Db, user: ActionsCaller
) -> actions_api.ActionRunOut:
    """Action 요청 = 미리보기 기록. 승인·실행은 사람이 화면에서 한다.

    같은 ``request_id`` 는 한 번만 받는다.
    """
    with actions_api._errors():
        run = await runner.preview(
            db,
            runner.Actor(user.id, user.roles),
            action=name,
            target_pk=body.target_pk,
            params=body.params,
            request_id=body.request_id,
            cfg=notify_config.load(get_settings().notify_config_path),
            origin="api",
        )
    return await actions_api._one(db, user, run.id)


@router.get("/actions/requests/{run_id}", response_model=actions_api.ActionRunOut)
async def action_request(run_id: int, db: Db, user: ActionsCaller) -> actions_api.ActionRunOut:
    """요청의 지금 상태(미리보기 → 승인·실행·거부·실패)."""
    return await actions_api._one(db, user, run_id)


@router.get("/openapi.json", include_in_schema=False)
async def public_openapi(request: Request) -> dict[str, Any]:
    """공개 API 만 담은 OpenAPI 문서(인증 없이 읽는다 — 키 없이도 연동을 준비할 수 있게).

    이 라우터만 붙인 앱으로 만든다 — 앱 전체 문서에서 경로만 거르면 내부 스키마가 함께 나간다.
    """
    global _OPENAPI
    if _OPENAPI is None:
        doc_app = FastAPI(
            title="OntoBot 공개 API",
            version=app_version(),
            description=(
                "API 키(`Authorization: Bearer obk_…`)로 부른다. 스코프: "
                f"`{SCOPE_QUERY}`(질의), `{SCOPE_OBJECTS_READ}`(객체 읽기), "
                f"`{SCOPE_ACTIONS_REQUEST}`(Action 요청 — 승인은 사람이 화면에서)."
            ),
        )
        prefix = request.url.path.removesuffix("/public/openapi.json")
        doc_app.include_router(router, prefix=prefix)
        _OPENAPI = doc_app.openapi()
    return _OPENAPI


_OPENAPI: dict[str, Any] | None = None
