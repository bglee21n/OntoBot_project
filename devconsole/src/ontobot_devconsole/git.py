"""git — 읽기는 기계용 출력(porcelain v2·NUL 구분)을 파싱하고, 쓰기는 고정 인자로만.

충돌 해결·리베이스·강제 푸시는 하지 않는다(화면은 안내만). 푸시는 터미널 비밀번호 프롬프트를 끄고
(``GIT_TERMINAL_PROMPT=0``) 저장된 자격 증명으로만 한다 — 콘솔이 입력을 기다리며 멈추지 않게.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ontobot_devconsole.catalog import CatalogError, check_branch, check_tag

DIFF_LIMIT = 200_000
SEP = "\x1f"
COAUTHOR = "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"


class GitError(Exception):
    pass


def _git(root: Path, *args: str, check: bool = True) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C.UTF-8"}
    res = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=root,
        capture_output=True,
        env=env,
        timeout=120,
    )
    out = res.stdout.decode("utf-8", errors="replace")
    if check and res.returncode != 0:
        raise GitError(
            res.stderr.decode("utf-8", errors="replace").strip() or f"git {args[0]} 실패"
        )
    return out


@dataclass
class FileChange:
    path: str
    index: str  # 스테이지 상태(. = 없음)
    worktree: str  # 작업 트리 상태
    untracked: bool = False


@dataclass
class Status:
    branch: str | None
    upstream: str | None
    ahead: int
    behind: int
    head: str | None
    files: list[FileChange] = field(default_factory=list)

    @property
    def dirty(self) -> bool:
        return bool(self.files)

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "dirty": self.dirty}


def parse_status(raw: str) -> Status:
    """``git status --porcelain=v2 --branch -z`` 출력 → Status(이름에 공백·한글이 있어도)."""
    st = Status(branch=None, upstream=None, ahead=0, behind=0, head=None)
    parts = raw.split("\0")
    i = 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if not rec:
            continue
        if rec.startswith("# branch.oid "):
            oid = rec.split(" ", 2)[2]
            st.head = None if oid == "(initial)" else oid
        elif rec.startswith("# branch.head "):
            head = rec.split(" ", 2)[2]
            st.branch = None if head == "(detached)" else head
        elif rec.startswith("# branch.upstream "):
            st.upstream = rec.split(" ", 2)[2]
        elif rec.startswith("# branch.ab "):
            a, b = rec.split(" ")[2:4]
            st.ahead, st.behind = int(a), -int(b)
        elif rec.startswith("1 "):
            f = rec.split(" ", 8)
            st.files.append(FileChange(f[8], f[1][0], f[1][1]))
        elif rec.startswith("2 "):
            # 이름 바꿈: 다음 항목이 원래 이름이다.
            f = rec.split(" ", 9)
            st.files.append(FileChange(f[9], f[1][0], f[1][1]))
            i += 1
        elif rec.startswith("u "):
            f = rec.split(" ", 10)
            st.files.append(FileChange(f[10], "U", "U"))
        elif rec.startswith("? "):
            st.files.append(FileChange(rec[2:], ".", "?", untracked=True))
    return st


def status(root: Path) -> Status:
    return parse_status(_git(root, "status", "--porcelain=v2", "--branch", "-z"))


def log(root: Path, limit: int = 50, ref: str = "HEAD") -> list[dict[str, str]]:
    fmt = SEP.join(["%H", "%h", "%an", "%aI", "%s", "%D"])
    raw = _git(root, "log", f"-n{limit}", f"--format={fmt}%x00", ref, check=False)
    out = []
    for rec in raw.split("\0"):
        rec = rec.strip("\n")
        if not rec:
            continue
        h, short, author, date, subject, refs = rec.split(SEP)
        out.append(
            {
                "hash": h,
                "short": short,
                "author": author,
                "date": date,
                "subject": subject,
                "refs": refs,
            }
        )
    return out


def diff(root: Path, path: str, staged: bool) -> str:
    st = status(root)
    known = {f.path for f in st.files}
    if path not in known:
        raise GitError("변경 목록에 없는 파일입니다")
    f = next(x for x in st.files if x.path == path)
    if f.untracked:
        p = (root / path).resolve()
        if root.resolve() not in p.parents or not p.is_file():
            raise GitError("저장소 밖의 파일입니다")
        text = p.read_text(encoding="utf-8", errors="replace")
        body = "".join(f"+{line}\n" for line in text.splitlines())
        return f"새 파일 {path}\n{body}"[:DIFF_LIMIT]
    args = ["diff", "--no-color", "--no-ext-diff"]
    if staged:
        args.append("--cached")
    out = _git(root, *args, "--", path)
    return out if len(out) <= DIFF_LIMIT else out[:DIFF_LIMIT] + "\n…(너무 길어 잘랐습니다)\n"


def branches(root: Path) -> list[dict[str, Any]]:
    fmt = SEP.join(
        ["%(refname:short)", "%(objectname:short)", "%(committerdate:iso-strict)", "%(HEAD)"]
    )
    raw = _git(root, "for-each-ref", f"--format={fmt}", "refs/heads", "--sort=-committerdate")
    out = []
    for line in raw.splitlines():
        name, short, date, head = line.split(SEP)
        out.append({"name": name, "short": short, "date": date, "current": head == "*"})
    return out


def tags(root: Path, limit: int = 20) -> list[str]:
    raw = _git(root, "tag", "--sort=-creatordate", check=False)
    return raw.splitlines()[:limit]


def head(root: Path) -> str | None:
    out = _git(root, "rev-parse", "HEAD", check=False).strip()
    return out or None


def create_branch(root: Path, name: str, switch: bool) -> None:
    check_branch(name)
    if switch:
        if status(root).dirty:
            raise GitError("커밋하지 않은 변경이 있어 전환하지 않습니다 — 먼저 커밋하세요")
        _git(root, "switch", "-c", name)
    else:
        _git(root, "branch", name)


def switch(root: Path, name: str) -> None:
    check_branch(name)
    if status(root).dirty:
        raise GitError("커밋하지 않은 변경이 있어 전환하지 않습니다 — 먼저 커밋하세요")
    _git(root, "switch", name)


def commit(root: Path, paths: list[str], message: str, coauthor: bool) -> str:
    """고른 파일만 스테이지해 커밋. 지운 파일도 경로로 고를 수 있다(git add -A -- 경로)."""
    msg = message.strip()
    if not msg:
        raise GitError("커밋 메시지를 쓰세요")
    if not paths:
        raise GitError("커밋할 파일을 고르세요")
    known = {f.path for f in status(root).files}
    unknown = [p for p in paths if p not in known]
    if unknown:
        raise GitError(f"변경 목록에 없는 파일: {unknown[:3]}")
    if coauthor and COAUTHOR not in msg:
        msg = f"{msg}\n\n{COAUTHOR}"
    _git(root, "add", "-A", "--", *paths)
    _git(root, "commit", "-m", msg)
    return _git(root, "log", "-1", "--format=%h %s").strip()


def tag(root: Path, name: str, message: str) -> None:
    check_tag(name)
    if name in tags(root, limit=10_000):
        raise GitError("이미 있는 태그입니다")
    _git(root, "tag", "-a", name, "-m", message.strip() or name)


def push_command(root: Path, with_tags: bool) -> list[str]:
    st = status(root)
    if st.branch is None:
        raise CatalogError("브랜치에 있지 않습니다(detached HEAD)")
    cmd = ["git", "push", "--set-upstream", "origin", st.branch]
    if with_tags:
        cmd.append("--follow-tags")
    return cmd


def notes_since_last_tag(root: Path) -> tuple[str | None, list[str]]:
    """릴리스 노트 초안 — 마지막 태그 이후 커밋 제목(없으면 최근 30개)."""
    last = _git(root, "describe", "--tags", "--abbrev=0", check=False).strip() or None
    rng = f"{last}..HEAD" if last else "HEAD"
    raw = _git(root, "log", "--format=%h %s", "-n200" if last else "-n30", rng, check=False)
    return last, raw.splitlines()


def changed_since(root: Path, ref: str, paths: list[str]) -> list[str]:
    """``ref`` 이후(커밋 + 작업 트리)에 바뀐 파일 중 ``paths`` 에 걸리는 것."""
    committed = _git(root, "diff", "--name-only", f"{ref}..HEAD", "--", *paths, check=False)
    working = _git(root, "diff", "--name-only", "HEAD", "--", *paths, check=False)
    return sorted({*committed.splitlines(), *working.splitlines()} - {""})


def last_commit_touching(root: Path, path: str) -> str | None:
    out = _git(root, "log", "-1", "--format=%H", "--", path, check=False).strip()
    return out or None
