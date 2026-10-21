"""GitHub — ``gh`` CLI 로만(토큰은 gh 가 관리한다, 콘솔은 저장하지 않는다).

gh 가 없거나 로그인하지 않았으면 화면은 설치·``gh auth login`` 안내만 보인다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

PR_FOOTER = "🤖 Generated with [Claude Code](https://claude.com/claude-code)"


class GitHubError(Exception):
    pass


def gh_path() -> str | None:
    return shutil.which("gh")


def _gh(root: Path, *args: str) -> str:
    gh = gh_path()
    if gh is None:
        raise GitHubError("gh CLI 가 없습니다")
    res = subprocess.run([gh, *args], cwd=root, capture_output=True, timeout=60)
    if res.returncode != 0:
        raise GitHubError(res.stderr.decode("utf-8", errors="replace").strip() or "gh 실패")
    return res.stdout.decode("utf-8", errors="replace")


def availability(root: Path) -> dict[str, Any]:
    if gh_path() is None:
        return {"installed": False, "logged_in": False, "detail": None}
    try:
        _gh(root, "auth", "status")
        return {"installed": True, "logged_in": True, "detail": None}
    except GitHubError as e:
        return {"installed": True, "logged_in": False, "detail": str(e)[:300]}


def prs(root: Path) -> list[dict[str, Any]]:
    fields = "number,title,headRefName,state,isDraft,url,author,statusCheckRollup,updatedAt"
    data: list[dict[str, Any]] = json.loads(
        _gh(root, "pr", "list", "--limit", "20", "--json", fields)
    )
    for pr in data:
        checks = pr.pop("statusCheckRollup", None) or []
        states = [c.get("conclusion") or c.get("status") for c in checks]
        pr["checks"] = {
            "total": len(states),
            "failed": sum(s in ("FAILURE", "CANCELLED", "TIMED_OUT") for s in states),
            "pending": sum(s in ("IN_PROGRESS", "QUEUED", "PENDING", None) for s in states),
        }
    return data


def runs(root: Path, branch: str | None) -> list[dict[str, Any]]:
    fields = "databaseId,displayTitle,headBranch,status,conclusion,url,createdAt,workflowName"
    args = ["run", "list", "--limit", "10", "--json", fields]
    if branch:
        args += ["--branch", branch]
    data: list[dict[str, Any]] = json.loads(_gh(root, *args))
    return data


def create_pr(root: Path, title: str, body: str, draft: bool) -> str:
    title = title.strip()
    if not title:
        raise GitHubError("PR 제목을 쓰세요")
    text = body.strip()
    if PR_FOOTER not in text:
        text = f"{text}\n\n{PR_FOOTER}" if text else PR_FOOTER
    args = ["pr", "create", "--title", title, "--body", text]
    if draft:
        args.append("--draft")
    return _gh(root, *args).strip().splitlines()[-1]
