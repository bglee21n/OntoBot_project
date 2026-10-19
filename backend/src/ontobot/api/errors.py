"""오류 응답 — RFC 7807 ``application/problem+json`` (TRD 14.1).

라우터는 ``ProblemError`` 를 던지고, 여기 등록된 처리기가 형식을 맞춘다. FastAPI 기본 오류
(HTTPException·검증 오류)도 같은 형식으로 바꿔 화면이 오류를 한 가지 모양으로만 다루게 한다.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        *,
        detail: str | None = None,
        code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.title = title
        self.detail = detail
        self.code = code
        self.extra = extra or {}


def problem_response(
    status: int,
    title: str,
    detail: str | None,
    code: str | None,
    instance: str,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"urn:ontobot:problem:{code}" if code else "about:blank",
        "title": title,
        "status": status,
        "instance": instance,
    }
    if detail:
        body["detail"] = detail
    if code:
        body["code"] = code
    body.update(extra or {})
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _on_problem(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(
            exc.status, exc.title, exc.detail, exc.code, request.url.path, exc.extra
        )

    @app.exception_handler(StarletteHTTPException)
    async def _on_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return problem_response(exc.status_code, str(exc.detail), None, None, request.url.path)

    @app.exception_handler(RequestValidationError)
    async def _on_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return problem_response(
            422,
            "요청 형식이 올바르지 않습니다",
            None,
            "validation_error",
            request.url.path,
            {"errors": errors},
        )
