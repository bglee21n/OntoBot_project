"""작업 — ontobotctl 을 자식 프로세스로 실행하고 출력·단계·결과를 남긴다(한 번에 하나).

기록: ``$ONTOBOT_HOME/deployer/jobs/<id>/job.json`` + ``output.log``, 이력
``history.jsonl``(추가만).
단계는 ontobotctl 의 ``==> `` 줄, 결과는 마지막 JSON 줄(``--json``)이다. 콘솔이 작업 도중 다시
시작되면
그 작업은 ``unknown``(확인 필요)으로 남는다 — 실제로 어디까지 됐는지는 상태 화면(ontobotctl
status)이 말한다.

끝난 작업은 앱 감사 로그에도 남긴다(``ontobotctl audit`` → ``ontobot.security.cli audit-deploy``).
DB 가
안 되면 감사 기록은 실패해도 작업 결과는 그대로다(이력에는 남는다).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from ontobot_deployer import bundles
from ontobot_deployer.settings import Settings

Kind = Literal["import", "preflight", "upgrade", "rollback", "backup", "deployer_update"]
Status = Literal["running", "succeeded", "failed", "not_ready", "unknown"]
AUDIT_EVENT: dict[str, str] = {
    "import": "deploy.bundle_imported",
    "upgrade": "deploy.upgrade",
    "rollback": "deploy.rollback",
    "backup": "deploy.backup",
    "deployer_update": "deploy.deployer_update",
}


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: Kind
    params: dict[str, Any]
    actor_id: int
    actor: str
    status: Status = "running"
    started_at: str = field(default_factory=now)
    finished_at: str | None = None
    steps: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    exit_code: int | None = None
    error: str | None = None


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
        return Job(**json.loads(p.read_text(encoding="utf-8")))

    def list(self, limit: int = 50) -> list[Job]:
        base = self.root / "jobs"
        if not base.is_dir():
            return []
        dirs = sorted((d for d in base.iterdir() if (d / "job.json").is_file()), reverse=True)
        return [self.get(d.name) for d in dirs[:limit]]

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
        with (self.root / "history.jsonl").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def recover(self) -> None:
        """콘솔 재시작 — 돌던 작업은 끝을 모른다."""
        for job in self.list(limit=1000):
            if job.status == "running":
                job.status, job.finished_at = "unknown", now()
                job.error = (
                    "배포 콘솔이 작업 도중 다시 시작되었습니다 — "
                    "상태 화면에서 실제 상태를 확인하세요"
                )
                self.save(job)


Runner = Callable[[list[str], Path], Awaitable[tuple[int, str]]]


class Jobs:
    def __init__(self, settings: Settings, store: JobStore) -> None:
        self.s = settings
        self.store = store
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def _ctl(self, *args: str) -> list[str]:
        return [self.s.ctl, "--json", "--non-interactive", *args]

    def _bundle_dir(self, name: str) -> Path:
        if not bundles.NAME_RE.match(name) or not (self.s.bundles / name).is_dir():
            raise bundles.BundleError("없는 번들입니다")
        return self.s.bundles / name

    def commands(self, kind: Kind, params: dict[str, Any]) -> list[list[str]]:
        """작업 → 실행할 ontobotctl 명령들.

        매개변수는 여기서만 명령으로 바뀐다(셸을 거치지 않는다).
        """
        if kind == "preflight":
            return [self._ctl("preflight", str(self._bundle_dir(params["bundle"])))]
        if kind == "upgrade":
            return [self._ctl("upgrade", str(self._bundle_dir(params["bundle"])))]
        if kind == "rollback":
            args = ["rollback"]
            if params.get("restore_db"):
                if params.get("confirm") != "RESTORE":
                    raise bundles.BundleError("DB 복원은 RESTORE 를 입력해야 합니다")
                args += ["--restore-db", "--confirm", "RESTORE"]
            return [self._ctl(*args)]
        if kind == "backup":
            return [self._ctl("backup", "--label", "console")]
        if kind == "deployer_update":
            return [self._ctl("deployer-update")]
        return []

    async def start(self, kind: Kind, params: dict[str, Any], actor_id: int, actor: str) -> Job:
        if self._lock.locked():
            raise Busy("다른 작업이 진행 중입니다")
        cmds = self.commands(kind, params)  # 매개변수 오류는 작업을 만들기 전에
        job = Job(
            # 시각(마이크로초까지)이 앞이라 이름 순서 = 만든 순서다.
            id=datetime.now(UTC).strftime("%Y%m%d%H%M%S%f") + secrets.token_hex(2),
            kind=kind,
            params={k: v for k, v in params.items() if k != "confirm"},
            actor_id=actor_id,
            actor=actor,
        )
        self.store.save(job)
        await self._lock.acquire()
        task = asyncio.create_task(self._run(job, cmds, params))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job: Job, cmds: list[list[str]], params: dict[str, Any]) -> None:
        log = self.store.log_path(job.id)
        try:
            if job.kind == "import":
                with log.open("a", encoding="utf-8") as f:
                    f.write("==> 번들 가져오기\n")
                path = await asyncio.to_thread(self._import, params)
                job.params["bundle"] = path.name
                self.store.save(job)
                cmds = [self._ctl("preflight", str(path))]
            code = 0
            for cmd in cmds:
                code, last_json = await self._exec(job, cmd, log)
                if last_json is not None:
                    job.result = last_json
                if code != 0:
                    break
            if job.kind in ("import", "preflight") and job.result is not None:
                pf = bundles.preflight_path(self.s.data, job.params["bundle"])
                pf.parent.mkdir(parents=True, exist_ok=True)
                pf.write_text(json.dumps(job.result, ensure_ascii=False), encoding="utf-8")
            job.exit_code = code
            job.status = "succeeded" if code == 0 else ("not_ready" if code == 2 else "failed")
            if code != 0 and job.result and job.result.get("error"):
                job.error = str(job.result["error"])
        except Exception as e:  # 작업 실패는 기록이다 — 콘솔은 계속 산다
            job.status, job.error = "failed", f"{type(e).__name__}: {e}"
            with log.open("a", encoding="utf-8") as f:
                f.write(f"오류: {job.error}\n")
        finally:
            job.finished_at = now()
            self.store.save(job)
            self.store.append_history(job)
            self._lock.release()
        await self._audit(job, log)

    def _import(self, params: dict[str, Any]) -> Path:
        if params.get("upload_id"):
            up = bundles.Uploads(self.s.incoming, self.s.max_upload_mb)
            return up.finish(params["upload_id"], self.s.bundles)
        if self.s.drop_dir is None:
            raise bundles.BundleError("반입 폴더가 설정되지 않았습니다")
        return bundles.import_drop(self.s.drop_dir, params["drop"], self.s.bundles)

    async def _exec(self, job: Job, cmd: list[str], log: Path) -> tuple[int, dict[str, Any] | None]:
        last_json: dict[str, Any] | None = None
        with log.open("ab") as out:
            out.write(f"$ {' '.join(cmd)}\n".encode())
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._env(),
            )
            if proc.stdout is None:  # PIPE 로 열었다
                raise RuntimeError("출력을 읽을 수 없습니다")
            async for raw in proc.stdout:
                out.write(raw)
                out.flush()
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("==> "):
                    job.steps.append(line[4:])
                    self.store.save(job)
                elif line.startswith("{"):
                    try:
                        last_json = json.loads(line)
                    except json.JSONDecodeError:
                        pass
            code = await proc.wait()
        return code, last_json

    def _env(self) -> dict[str, str]:
        import os

        return {**os.environ, "ONTOBOT_HOME": str(self.s.home)}

    async def _audit(self, job: Job, log: Path) -> None:
        event = AUDIT_EVENT.get(job.kind)
        if event is None:
            return
        payload = json.dumps(
            {"job": job.id, "status": job.status, "params": job.params, "result": job.result},
            ensure_ascii=False,
        )
        cmd = [
            self.s.ctl,
            "audit",
            "--event",
            event,
            "--actor-id",
            str(job.actor_id),
            "--payload",
            payload,
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._env(),
            )
            out, _ = await proc.communicate()
            if proc.returncode != 0:
                with log.open("a", encoding="utf-8") as f:
                    f.write(
                        "경고: 감사 로그 기록 실패(이력에는 남음)\n"
                        + out.decode("utf-8", "replace")
                    )
        except OSError:
            pass
