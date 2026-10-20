"""배포 콘솔 HTTP API 와 화면(ADR-0014).

- 모든 ``/api`` 는 로그인한 관리자만(로그인·``/healthz`` 제외). 세션 쿠키는
  HttpOnly·SameSite=Strict.
- 상태를 바꾸는 요청은 ``X-Ontobot-Deploy: 1`` 헤더가 있어야 한다(다른 사이트의 폼·링크로는 붙일 수
  없다 — CSRF).
- ``ONTOBOT_DEPLOYER_ALLOW_CIDRS`` 를 주면 그 대역 밖의 요청은 403.
- 절차는 ontobotctl 이 한다. 여기는 검증과 위임, 기록만.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ontobot_deployer import bundles
from ontobot_deployer.auth import Admin, Auth, Locked, pg_lookup
from ontobot_deployer.jobs import Busy, Job, Jobs, JobStore
from ontobot_deployer.settings import Settings

COOKIE = "ontobot_deploy"
CSRF_HEADER = "x-ontobot-deploy"
STATUS_TTL = 3.0


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class JobIn(BaseModel):
    kind: Literal["import", "preflight", "upgrade", "rollback", "backup", "deployer_update"]
    bundle: str | None = Field(default=None, max_length=128)
    drop: str | None = Field(default=None, max_length=128)
    upload_id: str | None = Field(default=None, max_length=32)
    restore_db: bool = False
    confirm: str | None = Field(default=None, max_length=16)
    # 업그레이드는 대상 버전을 사람이 다시 입력해야 실행한다(엉뚱한 번들 클릭 방지).
    confirm_version: str | None = Field(default=None, max_length=128)


StatusFn = Callable[[], Awaitable[dict[str, Any]]]


def current(request: Request) -> Admin:
    auth: Auth = request.app.state.auth
    admin = auth.session(request.cookies.get(COOKIE))
    if admin is None:
        raise HTTPException(401, "로그인하세요")
    return admin


User = Annotated[Admin, Depends(current)]


def create_app(
    settings: Settings | None = None,
    auth: Auth | None = None,
    status_fn: StatusFn | None = None,
) -> FastAPI:
    s = settings or Settings.from_env()
    a = auth or Auth(
        pg_lookup(s.db_host, s.db_port, s.db_name, s.db_user, s.db_password), s.session_minutes
    )
    store = JobStore(s.data)
    s.data.mkdir(parents=True, exist_ok=True)
    store.recover()
    jobs = Jobs(s, store)
    uploads = bundles.Uploads(s.incoming, s.max_upload_mb)
    app = FastAPI(title="OntoBot 배포 콘솔", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth = a
    cache: dict[str, Any] = {"at": 0.0, "value": None}

    async def ctl_status() -> dict[str, Any]:
        proc = await asyncio.create_subprocess_exec(
            s.ctl,
            "--json",
            "status",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        for line in reversed(out.decode("utf-8", "replace").splitlines()):
            if line.startswith("{"):
                value: dict[str, Any] = json.loads(line)
                return value
        return {"ok": False, "error": err.decode("utf-8", "replace")[-500:]}

    get_status = status_fn or ctl_status

    # ------------------------------------------------------------ 공통 검사

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if s.allow_cidrs and request.client is not None:
            try:
                ip = ipaddress.ip_address(request.client.host)
                allowed = any(ip in net for net in s.allow_cidrs)
            except ValueError:
                allowed = False
            if not allowed:
                return JSONResponse({"detail": "허용되지 않은 주소입니다"}, status_code=403)
        if (
            request.url.path.startswith("/api/")
            and request.method not in ("GET", "HEAD")
            and request.headers.get(CSRF_HEADER) != "1"
        ):
            return JSONResponse({"detail": "요청 헤더가 없습니다"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    # ------------------------------------------------------------ 로그인

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/login")
    async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
        client = request.client.host if request.client else "?"
        try:
            admin = await a.login(body.username, body.password, client)
        except Locked as e:
            raise HTTPException(429, str(e)) from e
        except OSError as e:
            raise HTTPException(503, "앱 DB 에 닿지 못해 로그인할 수 없습니다") from e
        if admin is None:
            raise HTTPException(401, "아이디·비밀번호가 맞지 않거나 관리자가 아닙니다")
        response.set_cookie(
            COOKIE,
            a.open_session(admin),
            httponly=True,
            samesite="strict",
            secure=s.cookie_secure,
            path="/",
        )
        return {"id": admin.id, "username": admin.username, "display_name": admin.display_name}

    @app.post("/api/logout", status_code=204)
    async def logout(request: Request, response: Response) -> None:
        a.close(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")

    @app.get("/api/me")
    async def me(user: User) -> dict[str, Any]:
        return {"id": user.id, "username": user.username, "display_name": user.display_name}

    # ------------------------------------------------------------ 상태·번들·백업

    @app.get("/api/status")
    async def status(user: User) -> dict[str, Any]:
        if cache["value"] is None or time.monotonic() - cache["at"] > STATUS_TTL:
            cache["value"], cache["at"] = await get_status(), time.monotonic()
        return {**cache["value"], "busy": jobs.busy}

    @app.get("/api/bundles")
    async def list_bundles(user: User) -> list[dict[str, Any]]:
        found = bundles.list_bundles(s.bundles, s.data, s.drop_dir)
        return [b.as_dict() for b in found]

    @app.delete("/api/bundles/{name}", status_code=204)
    async def delete_bundle(name: str, user: User) -> None:
        if jobs.busy:
            raise HTTPException(409, "작업이 진행 중입니다")
        try:
            bundles.remove(s.bundles, s.data, name)
        except bundles.BundleError as e:
            raise HTTPException(404, str(e)) from e

    @app.get("/api/backups")
    async def list_backups(user: User) -> list[dict[str, Any]]:
        d = s.home / "backups"
        if not d.is_dir():
            return []
        files = sorted(d.glob("*.dump"), reverse=True)
        return [{"name": f.name, "size_mb": f.stat().st_size // (1 << 20)} for f in files]

    # ------------------------------------------------------------ 업로드(조각)

    @app.post("/api/uploads")
    async def new_upload(user: User) -> dict[str, Any]:
        upload_id = secrets.token_hex(16)
        uploads.create(upload_id)
        return {"id": upload_id, "chunk_mb": 8, "max_mb": s.max_upload_mb}

    @app.get("/api/uploads/{upload_id}")
    async def upload_size(upload_id: str, user: User) -> dict[str, int]:
        try:
            return {"size": uploads.size(upload_id)}
        except bundles.BundleError as e:
            raise HTTPException(404, str(e)) from e

    @app.put("/api/uploads/{upload_id}")
    async def upload_chunk(upload_id: str, request: Request, user: User) -> dict[str, int]:
        try:
            offset = int(request.headers.get("x-offset", "-1"))
            data = await request.body()
            if len(data) > 16 << 20:
                raise bundles.BundleError("조각이 너무 큽니다(16MB 이하)")
            size = await asyncio.to_thread(uploads.append, upload_id, offset, data)
        except (bundles.BundleError, ValueError) as e:
            raise HTTPException(409, str(e)) from e
        return {"size": size}

    # ------------------------------------------------------------ 작업

    @app.post("/api/jobs", status_code=201)
    async def start_job(body: JobIn, user: User) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if body.kind == "import":
            if bool(body.drop) == bool(body.upload_id):
                raise HTTPException(422, "반입 폴더 항목이나 업로드 중 하나를 고르세요")
            params = {"drop": body.drop} if body.drop else {"upload_id": body.upload_id}
        elif body.kind in ("preflight", "upgrade"):
            if not body.bundle:
                raise HTTPException(422, "번들을 고르세요")
            params = {"bundle": body.bundle}
            if body.kind == "upgrade":
                info = next(
                    (
                        b
                        for b in bundles.list_bundles(s.bundles, s.data, None)
                        if b.name == body.bundle
                    ),
                    None,
                )
                if info is None:
                    raise HTTPException(404, "없는 번들입니다")
                if not info.preflight or not info.preflight.get("ok"):
                    raise HTTPException(409, "사전 점검을 통과한 번들만 업그레이드할 수 있습니다")
                if body.confirm_version != info.version:
                    raise HTTPException(422, "확인용으로 대상 버전을 정확히 입력하세요")
        elif body.kind == "rollback":
            params = {"restore_db": body.restore_db, "confirm": body.confirm}
        try:
            job = await jobs.start(body.kind, params, user.id, user.username)
        except Busy as e:
            raise HTTPException(409, str(e)) from e
        except bundles.BundleError as e:
            raise HTTPException(422, str(e)) from e
        cache["value"] = None
        return _job(job)

    @app.get("/api/jobs")
    async def list_jobs(user: User) -> list[dict[str, Any]]:
        return [_job(j) for j in store.list()]

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, user: User) -> dict[str, Any]:
        try:
            return _job(store.get(job_id))
        except KeyError as e:
            raise HTTPException(404, "없는 작업입니다") from e

    @app.get("/api/jobs/{job_id}/log")
    async def job_log(job_id: str, user: User, offset: int = 0) -> dict[str, Any]:
        try:
            store.get(job_id)
        except KeyError as e:
            raise HTTPException(404, "없는 작업입니다") from e
        text, nxt = store.read_log(job_id, offset)
        return {"text": text, "next": nxt}

    @app.get("/api/jobs/{job_id}/log.txt")
    async def job_log_file(job_id: str, user: User) -> Response:
        try:
            p = store.log_path(job_id)
        except KeyError as e:
            raise HTTPException(404, "없는 작업입니다") from e
        if not p.is_file():
            return PlainTextResponse("")
        return FileResponse(
            p, media_type="text/plain; charset=utf-8", filename=f"deploy-{job_id}.log"
        )

    # ------------------------------------------------------------ 화면

    if s.ui_dir is not None:
        ui: Path = s.ui_dir
        if (ui / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=ui / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(ui / "index.html")

    return app


def _job(job: Job) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(job)
