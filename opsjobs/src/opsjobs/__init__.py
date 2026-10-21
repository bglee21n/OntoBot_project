"""작업 실행기 — 배포 콘솔(ADR-0014)과 개발 콘솔(ADR-0015)이 함께 쓴다.

작업 하나 = 명령 몇 개를 차례로 자식 프로세스로 실행한 기록이다.
셸을 거치지 않는다(명령은 인자 목록).

- 기록: ``<root>/jobs/<id>/job.json`` + ``output.log``, 이력 ``<root>/history.jsonl``(추가만).
- 단계: 출력 줄 중 ``==> `` 로 시작하는 것(ontobotctl·build_bundle 의 진행 줄).
- 결과: 출력의 마지막 JSON 줄(``{`` 로 시작). 명령마다 ``on_line`` 으로 다른 결과를 뽑을 수도 있다.
- 콘솔이 작업 도중 다시 시작되면 그 작업은 ``unknown``(확인 필요)으로 남는다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 색·커서 제어 문자(pytest·ruff 의 색 출력). 로그와 파싱에는 글자만 남긴다.
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")

STATUSES = ("running", "succeeded", "failed", "not_ready", "unknown")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    """시각(마이크로초까지)이 앞이라 이름 순서 = 만든 순서다."""
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S%f") + secrets.token_hex(2)


@dataclass
class Job:
    id: str
    kind: str
    params: dict[str, Any]
    actor_id: int | None
    actor: str
    status: str = "running"
    started_at: str = field(default_factory=now)
    finished_at: str | None = None
    steps: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    exit_code: int | None = None
    error: str | None = None
    # 작업을 시작할 때의 소스 커밋 등 — 개발 콘솔이 "오래된 결과"를 판단하는 데 쓴다.
    meta: dict[str, Any] = field(default_factory=dict)


class Busy(Exception):
    pass


class JobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def dir(self, job_id: str) -> Path:
        if not job_id.isalnum():
            raise KeyError(job_id)
        return self.root / "jobs" / job_id

    def save(self, job: Job) -> None:
        d = self.dir(job.id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "job.json.tmp"
        tmp.write_text(json.dumps(asdict(job), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(d / "job.json")

    def get(self, job_id: str) -> Job:
        p = self.dir(job_id) / "job.json"
        if not p.is_file():
            raise KeyError(job_id)
        data = json.loads(p.read_text(encoding="utf-8"))
        known = Job.__dataclass_fields__
        return Job(**{k: v for k, v in data.items() if k in known})

    def list(self, limit: int = 50, kind: str | None = None) -> list[Job]:
        base = self.root / "jobs"
        if not base.is_dir():
            return []
        out: list[Job] = []
        for d in sorted((d for d in base.iterdir() if (d / "job.json").is_file()), reverse=True):
            job = self.get(d.name)
            if kind is None or job.kind == kind:
                out.append(job)
            if len(out) >= limit:
                break
        return out

    def last(self, kind: str) -> Job | None:
        found = self.list(limit=1, kind=kind)
        return found[0] if found else None

    def log_path(self, job_id: str) -> Path:
        return self.dir(job_id) / "output.log"

    def read_log(self, job_id: str, offset: int) -> tuple[str, int]:
        p = self.log_path(job_id)
        if not p.is_file():
            return "", 0
        with p.open("rb") as f:
            f.seek(max(0, offset))
            data = f.read(256 * 1024)
        return data.decode("utf-8", errors="replace"), max(0, offset) + len(data)

    def append_history(self, job: Job) -> None:
        line = json.dumps(
            {k: v for k, v in asdict(job).items() if k != "steps"}, ensure_ascii=False
        )
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "history.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def recover(self, message: str) -> None:
        """콘솔 재시작 — 돌던 작업은 끝을 모른다."""
        for job in self.list(limit=1000):
            if job.status == "running":
                job.status, job.finished_at, job.error = "unknown", now(), message
                self.save(job)


LineHook = Callable[[str], None]


async def run_command(
    cmd: Sequence[str],
    log: Path,
    *,
    job: Job,
    store: JobStore,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    on_line: LineHook | None = None,
) -> tuple[int, dict[str, Any] | None]:
    """명령 하나를 실행해 출력을 로그에 붙이고 (종료 코드, 마지막 JSON 줄)을 돌려준다.

    ``==> `` 줄은 바로 ``job.steps`` 에 넣고 저장한다(화면이 진행을 본다).
    """
    last_json: dict[str, Any] | None = None
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as out:
        out.write(f"$ {' '.join(cmd)}\n".encode())
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=dict(env) if env is not None else dict(os.environ),
            cwd=cwd,
        )
        if proc.stdout is None:  # PIPE 로 열었다
            raise RuntimeError("출력을 읽을 수 없습니다")
        async for raw in proc.stdout:
            raw = ANSI.sub(b"", raw)
            out.write(raw)
            out.flush()
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("==> "):
                job.steps.append(line[4:])
                store.save(job)
            elif line.startswith("{"):
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        last_json = value
                except json.JSONDecodeError:
                    pass
            if on_line is not None:
                on_line(line)
        code = await proc.wait()
    return code, last_json


def status_of(code: int) -> str:
    """종료 코드 → 상태. 2 는 '끝났지만 준비 확인 실패'(ontobotctl 규칙)."""
    return "succeeded" if code == 0 else ("not_ready" if code == 2 else "failed")
