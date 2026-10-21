"""배포 콘솔 — 인증·번들 해제·작업 기록·API 규칙(ADR-0014). 도커·DB 없이(가짜 ontobotctl)."""

from __future__ import annotations

import ast
import asyncio
import io
import json
import stat
import sys
import time
import zipfile
from pathlib import Path

import httpx
import pytest
from argon2 import PasswordHasher

from ontobot_deployer import bundles
from ontobot_deployer.api import CSRF_HEADER, create_app
from ontobot_deployer.auth import MAX_FAILURES, Auth, Locked, UserRecord
from ontobot_deployer.jobs import RECOVER_MESSAGE, Jobs, JobStore
from ontobot_deployer.settings import Settings

SRC = Path(__file__).resolve().parents[1] / "src" / "ontobot_deployer"
HASH = PasswordHasher().hash("correct-horse-battery")


def test_never_imports_the_app() -> None:
    # 앱이 깨졌을 때도 콘솔은 돌아야 한다(롤백이 그때 필요하다).
    for f in SRC.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(n == "ontobot" or n.startswith("ontobot.") for n in names), f


# ---------------------------------------------------------------- 인증


def _users(**over: UserRecord) -> dict[str, UserRecord]:
    base = {
        "admin": UserRecord(1, "admin", "관리자", HASH, "active", frozenset({"admin"})),
        "modeler": UserRecord(2, "modeler", "모델러", HASH, "active", frozenset({"modeler"})),
        "gone": UserRecord(3, "gone", "퇴사", HASH, "disabled", frozenset({"admin"})),
        "sso": UserRecord(4, "sso", "SSO", None, "active", frozenset({"admin"})),
    }
    return {**base, **over}


def _auth(users: dict[str, UserRecord] | None = None) -> Auth:
    table = users or _users()

    async def lookup(name: str) -> UserRecord | None:
        return table.get(name)

    return Auth(lookup, session_minutes=1)


async def test_only_active_admins_with_password_log_in() -> None:
    a = _auth()
    assert (await a.login("admin", "correct-horse-battery", "c1")) is not None
    for name in ("modeler", "gone", "sso", "nobody"):
        assert await a.login(name, "correct-horse-battery", f"c-{name}") is None
    assert await a.login("admin", "wrong", "c2") is None


async def test_repeated_failures_lock_the_account() -> None:
    a = _auth()
    for _ in range(MAX_FAILURES):
        assert await a.login("admin", "wrong", "c9") is None
    with pytest.raises(Locked):
        await a.login("admin", "correct-horse-battery", "elsewhere")


async def test_sessions_expire() -> None:
    a = _auth()
    admin = await a.login("admin", "correct-horse-battery", "c")
    assert admin is not None
    token = a.open_session(admin)
    assert a.session(token) == admin
    a._sessions[token] = (admin, time.monotonic() - 1)
    assert a.session(token) is None
    assert a.session("made-up") is None


# ---------------------------------------------------------------- 번들 해제


def _zip(entries: dict[str, bytes], links: tuple[str, ...] = ()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        for name in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    return buf.getvalue()


def test_safe_extract(tmp_path: Path) -> None:
    good = tmp_path / "good.zip"
    good.write_bytes(
        _zip({"ontobot-1/bundle.env": b"ONTOBOT_BUNDLE_VERSION=1\n", "ontobot-1/a/b": b"x"})
    )
    out = bundles.safe_extract(good, tmp_path / "bundles")
    assert out.name == "ontobot-1" and (out / "a" / "b").read_bytes() == b"x"
    assert not list((tmp_path / "bundles").glob(".*partial"))
    with pytest.raises(bundles.BundleError, match="이미"):
        bundles.safe_extract(good, tmp_path / "bundles")


@pytest.mark.parametrize(
    "entries,links",
    [
        ({"../evil": b"x"}, ()),
        ({"/abs/evil": b"x"}, ()),
        ({"ontobot-1/../../evil": b"x"}, ()),
        ({"a/x": b"1", "b/y": b"2"}, ()),
        ({"ontobot-1/ok": b"1"}, ("ontobot-1/link",)),
    ],
)
def test_unsafe_zip_is_rejected(
    tmp_path: Path, entries: dict[str, bytes], links: tuple[str, ...]
) -> None:
    z = tmp_path / "bad.zip"
    z.write_bytes(_zip(entries, links))
    with pytest.raises(bundles.BundleError):
        bundles.safe_extract(z, tmp_path / "bundles")
    assert not (tmp_path / "evil").exists()


def test_uploads_append_in_order(tmp_path: Path) -> None:
    up = bundles.Uploads(tmp_path / "in", max_mb=1)
    up.create("a" * 32)
    assert up.append("a" * 32, 0, b"12345") == 5
    with pytest.raises(bundles.BundleError, match="오프셋"):
        up.append("a" * 32, 0, b"again")
    with pytest.raises(bundles.BundleError, match="상한"):
        up.append("a" * 32, 5, b"x" * (1 << 20))
    with pytest.raises(bundles.BundleError):
        up.path("../../etc")


# ---------------------------------------------------------------- 작업(가짜 ontobotctl)

FAKE_CTL = """
import json, sys, time
sys.stdout.reconfigure(encoding="utf-8")  # 진짜 ontobotctl 처럼 UTF-8 로(Windows 콘솔 기본은 cp949)
args = [a for a in sys.argv[1:] if not a.startswith("--json") and a != "--non-interactive"]
cmd = args[0]
print("==> 첫 단계", flush=True)
if cmd == "upgrade":
    print("==> 둘째 단계", flush=True)
    print(json.dumps({"ok": True, "to": "2"}))
    sys.exit(0)
if cmd == "preflight":
    print(json.dumps({"ok": True, "version": "2", "problems": [], "warnings": []}))
    sys.exit(0)
if cmd == "audit":
    sys.exit(0)
print(json.dumps({"ok": False, "error": "실패 흉내"}))
sys.exit(1)
"""


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    ctl = tmp_path / "fakectl.py"
    ctl.write_text(FAKE_CTL, encoding="utf-8")
    wrapper = tmp_path / ("fakectl.cmd" if sys.platform == "win32" else "fakectl")
    if sys.platform == "win32":
        wrapper.write_text(f'@"{sys.executable}" "{ctl}" %*\n', encoding="utf-8")
    else:
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{ctl}" "$@"\n', encoding="utf-8")
        wrapper.chmod(0o755)
    home = tmp_path / "home"
    b = home / "bundles" / "ontobot-2"
    b.mkdir(parents=True)
    (b / "bundle.env").write_text("ONTOBOT_BUNDLE_VERSION=2\nONTOBOT_BUNDLE_APP_ONLY=true\n")
    return Settings(home=home, drop_dir=None, ctl=str(wrapper))


async def _wait(store: JobStore, job_id: str) -> None:
    for _ in range(200):
        if store.get(job_id).status != "running":
            return
        await asyncio.sleep(0.05)
    raise AssertionError("작업이 끝나지 않았다")


async def test_job_records_steps_result_and_history(settings: Settings) -> None:
    store = JobStore(settings.data)
    jobs = Jobs(settings, store)
    job = await jobs.start("upgrade", {"bundle": "ontobot-2"}, 1, "admin")
    await _wait(store, job.id)
    done = store.get(job.id)
    assert done.status == "succeeded" and done.steps == ["첫 단계", "둘째 단계"]
    assert done.result == {"ok": True, "to": "2"}
    assert "둘째 단계" in store.read_log(job.id, 0)[0]
    assert (settings.data / "history.jsonl").read_text(encoding="utf-8").count(job.id) == 1


async def test_failed_job_and_one_at_a_time(settings: Settings) -> None:
    store = JobStore(settings.data)
    jobs = Jobs(settings, store)
    job = await jobs.start("backup", {}, 1, "admin")
    with pytest.raises(Exception, match="진행 중"):
        await jobs.start("backup", {}, 1, "admin")
    await _wait(store, job.id)
    done = store.get(job.id)
    assert done.status == "failed" and done.error == "실패 흉내"


def test_rollback_db_needs_restore_word(settings: Settings) -> None:
    jobs = Jobs(settings, JobStore(settings.data))
    with pytest.raises(bundles.BundleError, match="RESTORE"):
        jobs.commands("rollback", {"restore_db": True, "confirm": "yes"})
    cmd = jobs.commands("rollback", {"restore_db": True, "confirm": "RESTORE"})[0]
    assert cmd[-3:] == ["--restore-db", "--confirm", "RESTORE"]
    with pytest.raises(bundles.BundleError):
        jobs.commands("upgrade", {"bundle": "../../etc"})


def test_restart_marks_running_jobs_unknown(settings: Settings) -> None:
    from ontobot_deployer.jobs import Job

    store = JobStore(settings.data)
    store.save(Job(id="x1", kind="upgrade", params={}, actor_id=1, actor="admin"))
    store.recover(RECOVER_MESSAGE)
    assert store.get("x1").status == "unknown"


# ---------------------------------------------------------------- API


async def _client(settings: Settings) -> httpx.AsyncClient:
    async def status() -> dict[str, object]:
        return {"ok": True, "current": "1"}

    app = create_app(settings, _auth(), status)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://console")


async def test_api_requires_login_and_csrf_header(settings: Settings) -> None:
    async with await _client(settings) as c:
        assert (await c.get("/api/status")).status_code == 401
        body = {"username": "admin", "password": "correct-horse-battery"}
        assert (await c.post("/api/login", json=body)).status_code == 403  # 헤더 없음
        h = {CSRF_HEADER: "1"}
        assert (await c.post("/api/login", json=body, headers=h)).status_code == 200
        st = (await c.get("/api/status")).json()
        assert st["current"] == "1" and st["busy"] is False
        assert (await c.post("/api/jobs", json={"kind": "backup"})).status_code == 403


async def test_upgrade_needs_passed_preflight_and_typed_version(settings: Settings) -> None:
    async with await _client(settings) as c:
        h = {CSRF_HEADER: "1"}
        await c.post(
            "/api/login", json={"username": "admin", "password": "correct-horse-battery"}, headers=h
        )
        up = {"kind": "upgrade", "bundle": "ontobot-2", "confirm_version": "2"}
        r = await c.post("/api/jobs", json=up, headers=h)
        assert r.status_code == 409 and "사전 점검" in r.json()["detail"]
        pf = await c.post("/api/jobs", json={"kind": "preflight", "bundle": "ontobot-2"}, headers=h)
        await _wait(JobStore(settings.data), pf.json()["id"])
        listed = (await c.get("/api/bundles")).json()
        assert listed[0]["preflight"]["ok"] is True
        wrong = {**up, "confirm_version": "3"}
        assert (await c.post("/api/jobs", json=wrong, headers=h)).status_code == 422
        r = await c.post("/api/jobs", json=up, headers=h)
        assert r.status_code == 201
        await _wait(JobStore(settings.data), r.json()["id"])
        jobs = (await c.get("/api/jobs")).json()
        assert [j["kind"] for j in jobs] == ["upgrade", "preflight"]
        log = (await c.get(f"/api/jobs/{r.json()['id']}/log")).json()
        assert "둘째 단계" in log["text"]


async def test_cidr_allowlist(settings: Settings) -> None:
    from dataclasses import replace

    from ontobot_deployer.settings import _cidrs

    s = replace(settings, allow_cidrs=_cidrs("10.0.0.0/8"))
    async with await _client(s) as c:
        # ASGITransport 의 클라이언트 주소는 127.0.0.1 — 대역 밖.
        assert (await c.get("/healthz")).status_code == 403


def test_bundle_env_is_read_not_executed(tmp_path: Path) -> None:
    p = tmp_path / "bundle.env"
    p.write_text("A=$(rm -rf /)\n# 주석\nB=x=y\n", encoding="utf-8")
    assert bundles.read_env(p) == {"A": "$(rm -rf /)", "B": "x=y"}
    assert json.dumps(bundles.read_env(tmp_path / "none.env")) == "{}"
