"""개발 콘솔 — git 출력 파싱·결과 파싱·카탈로그·API 보호·반출 대상(ADR-0015)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ontobot_devconsole import bundles, git, results
from ontobot_devconsole.api import create_app
from ontobot_devconsole.catalog import (
    CatalogError,
    available_tasks,
    check_branch,
    check_tag,
    task_command,
)
from ontobot_devconsole.runner import DevJobs
from ontobot_devconsole.services import ServiceError, Services
from ontobot_devconsole.settings import REPO_ROOT, Settings


def test_parse_status_handles_spaces_korean_renames_and_untracked() -> None:
    raw = "\0".join(
        [
            "# branch.oid 0123456789abcdef",
            "# branch.head feature/phase-12",
            "# branch.upstream origin/feature/phase-12",
            "# branch.ab +2 -1",
            "1 .M N... 100644 100644 100644 aaa bbb docs/manual/운영자 매뉴얼.md",
            "2 R. N... 100644 100644 100644 aaa bbb R100 new name.py",
            "old name.py",
            "? 새 파일.txt",
            "",
        ]
    )
    st = git.parse_status(raw)
    assert (st.branch, st.upstream, st.ahead, st.behind) == (
        "feature/phase-12",
        "origin/feature/phase-12",
        2,
        1,
    )
    assert [f.path for f in st.files] == [
        "docs/manual/운영자 매뉴얼.md",
        "new name.py",
        "새 파일.txt",
    ]
    assert st.files[0].worktree == "M" and st.files[1].index == "R" and st.files[2].untracked
    assert st.dirty


def test_parse_status_detached_and_clean() -> None:
    st = git.parse_status("# branch.oid abc\0# branch.head (detached)\0")
    assert st.branch is None and not st.dirty


def test_pytest_summary_adds_backend_and_console_runs() -> None:
    lines = [
        "FAILED tests/unit/test_x.py::test_a - AssertionError",
        "========= 1 failed, 235 passed in 2.71s =========",
        "19 passed in 1.60s",
    ]
    s = results.pytest_summary(lines)
    assert s is not None
    assert (s["passed"], s["failed"]) == (254, 1)
    assert s["failed_tests"] == ["tests/unit/test_x.py::test_a"]
    assert results.pytest_summary(["nothing here"]) is None


def test_eval_summary() -> None:
    lines = [
        "  [O] q01  ok/1",
        "  [X] u01  ok/3",
        ' "cassette_misses": 0',
        "  통과 [invariant] citation_rate min 1.0 — 값 1.0",
        "  실패 [threshold] accuracy min 0.9 — 값 0.85",
        "게이트: 실패 (eval_run 3)",
    ]
    s = results.eval_summary(lines)
    assert s is not None
    assert s["gate_passed"] is False and s["failed_items"] == ["u01"] and s["cassette_misses"] == 0
    assert [c["ok"] for c in s["checks"]] == [True, False]


def test_catalog_follows_task_py() -> None:
    names = {t.name for t in available_tasks(REPO_ROOT)}
    assert {"lint", "test-unit", "eval-record", "image"} <= names
    assert "dev-api" not in names  # 서비스는 따로
    cmd = task_command(REPO_ROOT, "lint")
    assert cmd[-2:] == ["scripts/task.py", "lint"]
    with pytest.raises(CatalogError):
        task_command(REPO_ROOT, "rm -rf /")


@pytest.mark.parametrize("bad", ["", "-x", "a..b", "a b", "x.lock", "한글", "a;b"])
def test_branch_names(bad: str) -> None:
    with pytest.raises(CatalogError):
        check_branch(bad)
    assert check_branch("feature/phase-12") == "feature/phase-12"


def test_tag_names() -> None:
    assert check_tag("v0.2.0") == "v0.2.0"
    assert check_tag("v1.0.0-rc.1") == "v1.0.0-rc.1"
    for bad in ("0.2.0", "v1", "v1.0.0;x"):
        with pytest.raises(CatalogError):
            check_tag(bad)


def test_export_target(tmp_path: Path) -> None:
    with pytest.raises(bundles.BundleError):
        bundles.check_target(Path("relative/dir"), REPO_ROOT)
    with pytest.raises(bundles.BundleError):
        bundles.check_target(REPO_ROOT / "dist" / "x", REPO_ROOT)
    assert bundles.check_target(tmp_path, REPO_ROOT) == tmp_path.resolve()


def test_unknown_service(tmp_path: Path) -> None:
    with pytest.raises(ServiceError):
        Services(REPO_ROOT, tmp_path).start("rm")


def test_plan_rejects_unknown_kinds_and_tasks(tmp_path: Path) -> None:
    jobs = DevJobs(_settings(tmp_path))
    with pytest.raises(CatalogError):
        jobs.plan("shell", {"cmd": "whoami"})
    with pytest.raises(CatalogError):
        jobs.plan("task", {"name": "nope"})
    with pytest.raises(bundles.BundleError):
        jobs.plan("bundle-verify", {"name": "../../etc"})


# ---------------------------------------------------------------- API 보호


def _settings(tmp_path: Path) -> Settings:
    """기록은 임시 폴더로(저장소의 .devconsole 를 건드리지 않게) — root 는 실제 저장소(git 읽기)."""

    class S(Settings):
        @property
        def data(self) -> Path:
            return tmp_path / ".devconsole"

    return S(root=REPO_ROOT)


@pytest.fixture
def client(tmp_path: Path) -> httpx.AsyncClient:
    app = create_app(_settings(tmp_path))
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8200"
    )


async def test_host_header_must_be_localhost(client: httpx.AsyncClient) -> None:
    async with client as c:
        assert (await c.get("/healthz")).status_code == 200
        r = await c.get("/healthz", headers={"Host": "evil.example:8200"})
        assert r.status_code == 403


async def test_state_changes_need_header_and_local_origin(client: httpx.AsyncClient) -> None:
    async with client as c:
        body = {"kind": "task", "name": "nope"}
        assert (await c.post("/api/jobs", json=body)).status_code == 403
        h = {"X-Ontobot-Dev": "1", "Origin": "https://evil.example"}
        assert (await c.post("/api/jobs", json=body, headers=h)).status_code == 403
        h = {"X-Ontobot-Dev": "1", "Origin": "http://localhost:5175"}
        r = await c.post("/api/jobs", json=body, headers=h)
        assert r.status_code == 422 and "모르는 작업" in r.json()["detail"]
        r = await c.post("/api/jobs", json={"kind": "shell"}, headers=h)
        assert r.status_code == 422


async def test_read_endpoints(client: httpx.AsyncClient) -> None:
    async with client as c:
        ov = (await c.get("/api/overview")).json()
        assert "git" in ov and set(ov["checks"]) >= {"lint", "test-unit", "eval-replay"}
        assert ov["git"]["head"]
        tasks = (await c.get("/api/tasks")).json()
        assert any(t["name"] == "lint" for t in tasks)
        log = (await c.get("/api/git/log?limit=3")).json()
        assert len(log) == 3 and log[0]["hash"]
        assert (await c.get("/api/git/diff", params={"path": "no/such/file"})).status_code == 404
