"""개발 콘솔 HTTP API 와 화면(ADR-0015) — 개발 PC 의 127.0.0.1 전용.

로그인이 없는 대신 다른 사이트가 이 콘솔을 부를 수 없게 막는다:
- ``Host`` 가 localhost·127.0.0.1(이 포트)가 아니면 거부(DNS 리바인딩).
- 상태를 바꾸는 요청은 ``X-Ontobot-Dev: 1`` 헤더 + (있으면) ``Origin`` 이 localhost 여야 한다(CSRF).
- 명령은 고정 목록(catalog)뿐 — 임의 셸 명령을 받지 않는다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from opsjobs import Busy, Job
from pydantic import BaseModel, Field

from ontobot_devconsole import bundles, envinfo, git, github, overview
from ontobot_devconsole.catalog import CatalogError, available_tasks
from ontobot_devconsole.runner import KINDS, DevJobs
from ontobot_devconsole.services import ServiceError, Services
from ontobot_devconsole.settings import LOOPBACK, Settings

CSRF_HEADER = "x-ontobot-dev"


class JobIn(BaseModel):
    kind: str = Field(max_length=32)
    name: str | None = Field(default=None, max_length=128)
    app_only: bool = False
    zip: bool = False
    sign: bool = True
    target: str | None = Field(default=None, max_length=400)
    tags: bool = False


class CommitIn(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)
    message: str = Field(min_length=1, max_length=5000)
    coauthor: bool = True


class BranchIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    switch: bool = True


class TagIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    message: str = Field(default="", max_length=2000)


class PrIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=20000)
    draft: bool = False


def _host_ok(host: str | None, port: int) -> bool:
    if not host:
        return False
    name = urlsplit(f"//{host}").hostname or ""
    return name in LOOPBACK


def _job(job: Job) -> dict[str, Any]:
    return asdict(job)


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    jobs = DevJobs(s)
    services = Services(s.root, s.data)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        services.stop_all()  # 콘솔을 끄면 개발 서버도 끈다

    app = FastAPI(
        title="OntoBot 개발 콘솔",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def guard(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not _host_ok(request.headers.get("host"), s.port):
            return JSONResponse({"detail": "localhost 로만 접속합니다"}, status_code=403)
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD"):
            origin = request.headers.get("origin")
            if request.headers.get(CSRF_HEADER) != "1" or (
                origin and (urlsplit(origin).hostname or "") not in LOOPBACK
            ):
                return JSONResponse(
                    {"detail": "요청 헤더가 없거나 다른 사이트입니다"}, status_code=403
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def fail(e: Exception, status: int = 409) -> HTTPException:
        return HTTPException(status, str(e))

    # ------------------------------------------------------------ 대시보드·작업

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/overview")
    async def get_overview() -> dict[str, Any]:
        try:
            data = overview.build(s, jobs.store)
        except git.GitError as e:
            raise fail(e, 500) from e
        data["busy"] = jobs.busy
        data["services"] = services.status()
        return data

    @app.get("/api/tasks")
    async def tasks() -> list[dict[str, Any]]:
        return [asdict(t) for t in available_tasks(s.root)]

    @app.post("/api/jobs", status_code=201)
    async def start_job(body: JobIn) -> dict[str, Any]:
        if body.kind not in KINDS:
            raise HTTPException(422, f"모르는 작업 종류: {body.kind}")
        options: dict[str, dict[str, Any]] = {
            "task": {"name": body.name},
            "check-all": {},
            "release": {},
            "bundle-build": {"app_only": body.app_only, "zip": body.zip, "sign": body.sign},
            "bundle-verify": {"name": body.name},
            "bundle-export": {"name": body.name, "target": body.target},
            "release-notes": {"name": body.name},
            "push": {"tags": body.tags},
        }
        params = options[body.kind]
        try:
            return _job(await jobs.start(body.kind, params))
        except Busy as e:
            raise fail(e) from e
        except (CatalogError, bundles.BundleError, git.GitError) as e:
            raise fail(e, 422) from e

    @app.get("/api/jobs")
    async def list_jobs(kind: str | None = None) -> list[dict[str, Any]]:
        return [_job(j) for j in jobs.store.list(limit=100, kind=kind)]

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str) -> dict[str, Any]:
        try:
            return _job(jobs.store.get(job_id))
        except KeyError as e:
            raise HTTPException(404, "없는 작업입니다") from e

    @app.get("/api/jobs/{job_id}/log")
    async def job_log(job_id: str, offset: int = 0) -> dict[str, Any]:
        try:
            jobs.store.get(job_id)
        except KeyError as e:
            raise HTTPException(404, "없는 작업입니다") from e
        text, nxt = jobs.store.read_log(job_id, offset)
        return {"text": text, "next": nxt}

    @app.get("/api/jobs/{job_id}/log.txt")
    async def job_log_file(job_id: str) -> Response:
        try:
            p = jobs.store.log_path(job_id)
        except KeyError as e:
            raise HTTPException(404, "없는 작업입니다") from e
        if not p.is_file():
            return PlainTextResponse("")
        return FileResponse(p, media_type="text/plain; charset=utf-8", filename=f"dev-{job_id}.log")

    # ------------------------------------------------------------ 소스(git)

    @app.get("/api/git/status")
    async def git_status() -> dict[str, Any]:
        return git.status(s.root).as_dict()

    @app.get("/api/git/log")
    async def git_log(limit: int = 50) -> list[dict[str, str]]:
        return git.log(s.root, min(max(limit, 1), 200))

    @app.get("/api/git/diff")
    async def git_diff(path: str, staged: bool = False) -> dict[str, str]:
        try:
            return {"path": path, "diff": git.diff(s.root, path, staged)}
        except git.GitError as e:
            raise fail(e, 404) from e

    @app.get("/api/git/branches")
    async def git_branches() -> dict[str, Any]:
        return {"branches": git.branches(s.root), "tags": git.tags(s.root)}

    @app.post("/api/git/branch")
    async def git_branch(body: BranchIn) -> dict[str, str]:
        if jobs.busy:
            raise HTTPException(409, "작업이 진행 중입니다")
        try:
            git.create_branch(s.root, body.name, body.switch)
        except (git.GitError, CatalogError) as e:
            raise fail(e, 422) from e
        return {"branch": body.name}

    @app.post("/api/git/switch")
    async def git_switch(body: BranchIn) -> dict[str, str]:
        if jobs.busy:
            raise HTTPException(409, "작업이 진행 중입니다")
        try:
            git.switch(s.root, body.name)
        except (git.GitError, CatalogError) as e:
            raise fail(e, 422) from e
        return {"branch": body.name}

    @app.post("/api/git/commit")
    async def git_commit(body: CommitIn) -> dict[str, str]:
        if jobs.busy:
            raise HTTPException(409, "작업이 진행 중입니다")
        try:
            return {"commit": git.commit(s.root, body.paths, body.message, body.coauthor)}
        except git.GitError as e:
            raise fail(e, 422) from e

    @app.post("/api/git/tag")
    async def git_tag(body: TagIn) -> dict[str, str]:
        try:
            git.tag(s.root, body.name, body.message)
        except (git.GitError, CatalogError) as e:
            raise fail(e, 422) from e
        return {"tag": body.name}

    # ------------------------------------------------------------ GitHub

    @app.get("/api/github")
    async def gh_overview() -> dict[str, Any]:
        avail = github.availability(s.root)
        out: dict[str, Any] = {**avail, "prs": [], "runs": [], "error": None}
        if avail["logged_in"]:
            try:
                out["prs"] = github.prs(s.root)
                out["runs"] = github.runs(s.root, git.status(s.root).branch)
            except github.GitHubError as e:
                out["error"] = str(e)[:500]
        return out

    @app.post("/api/github/pr")
    async def gh_pr(body: PrIn) -> dict[str, str]:
        try:
            return {"url": github.create_pr(s.root, body.title, body.body, body.draft)}
        except github.GitHubError as e:
            raise fail(e, 422) from e

    # ------------------------------------------------------------ 번들

    @app.get("/api/bundles")
    async def list_bundles() -> dict[str, Any]:
        verified: dict[str, dict[str, Any]] = {}
        for j in reversed(jobs.store.list(limit=200, kind="bundle-verify")):
            name = str(j.params.get("name"))
            verified[name] = {"status": j.status, "finished_at": j.finished_at}
        key = s.signing_key()
        return {
            "dir": str(s.bundles),
            "bundles": bundles.list_bundles(s.bundles, verified),
            "signing_key": str(key) if key else None,
            "signing_key_exists": bool(key and key.is_file()),
        }

    @app.delete("/api/bundles/{name}", status_code=204)
    async def delete_bundle(name: str) -> None:
        if jobs.busy:
            raise HTTPException(409, "작업이 진행 중입니다")
        try:
            bundles.remove(s.bundles, name)
        except bundles.BundleError as e:
            raise fail(e, 404) from e

    # ------------------------------------------------------------ 개발 환경

    @app.get("/api/env")
    async def env() -> dict[str, Any]:
        return {**envinfo.summary(s.root, s.data, s.bundles), "services": services.status()}

    @app.post("/api/services/{name}/{op}")
    async def service_op(
        name: str, op: Literal["start", "stop", "restart"]
    ) -> list[dict[str, Any]]:
        try:
            if op in ("stop", "restart"):
                services.stop(name)
            if op in ("start", "restart"):
                services.start(name)
        except ServiceError as e:
            raise fail(e, 422) from e
        return services.status()

    @app.get("/api/services/{name}/log")
    async def service_log(name: str, offset: int = 0) -> dict[str, Any]:
        try:
            text, nxt = services.read_log(name, offset)
        except ServiceError as e:
            raise fail(e, 404) from e
        return {"text": text, "next": nxt}

    # ------------------------------------------------------------ 화면

    if s.ui_dir is not None:
        ui: Path = s.ui_dir
        if (ui / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=ui / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        async def index() -> FileResponse:
            return FileResponse(ui / "devconsole.html")

    return app
