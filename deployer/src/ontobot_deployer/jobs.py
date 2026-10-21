"""작업 — ontobotctl 을 자식 프로세스로 실행하고 출력·단계·결과를 남긴다(한 번에 하나).

실행·기록은 공유 실행기 ``opsjobs`` 가 한다(개발 콘솔과 같다). 기록은
``$ONTOBOT_HOME/deployer/jobs/<id>/``. 결과는 ontobotctl 의 마지막 JSON 줄(``--json``)이다.

끝난 작업은 앱 감사 로그에도 남긴다(``ontobotctl audit`` → ``ontobot.security.cli audit-deploy``).
DB 가 안 되면 감사 기록은 실패해도 작업 결과는 그대로다(이력에는 남는다).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

from opsjobs import Busy, Job, JobStore, new_id, now, run_command, status_of

from ontobot_deployer import bundles
from ontobot_deployer.settings import Settings

__all__ = ["AUDIT_EVENT", "Busy", "Job", "JobStore", "Jobs", "Kind"]

Kind = Literal["import", "preflight", "upgrade", "rollback", "backup", "deployer_update"]
RECOVER_MESSAGE = "배포 콘솔이 작업 도중 다시 시작되었습니다 — 상태 화면에서 실제 상태를 확인하세요"
AUDIT_EVENT: dict[str, str] = {
    "import": "deploy.bundle_imported",
    "upgrade": "deploy.upgrade",
    "rollback": "deploy.rollback",
    "backup": "deploy.backup",
    "deployer_update": "deploy.deployer_update",
}


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
            id=new_id(),
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
                log.parent.mkdir(parents=True, exist_ok=True)
                with log.open("a", encoding="utf-8") as f:
                    f.write("==> 번들 가져오기\n")
                path = await asyncio.to_thread(self._import, params)
                job.params["bundle"] = path.name
                self.store.save(job)
                cmds = [self._ctl("preflight", str(path))]
            code = 0
            for cmd in cmds:
                code, last_json = await run_command(
                    cmd, log, job=job, store=self.store, env=self._env()
                )
                if last_json is not None:
                    job.result = last_json
                if code != 0:
                    break
            if job.kind in ("import", "preflight") and job.result is not None:
                pf = bundles.preflight_path(self.s.data, job.params["bundle"])
                pf.parent.mkdir(parents=True, exist_ok=True)
                pf.write_text(json.dumps(job.result, ensure_ascii=False), encoding="utf-8")
            job.exit_code = code
            job.status = status_of(code)
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

    def _env(self) -> dict[str, str]:
        return {**os.environ, "ONTOBOT_HOME": str(self.s.home)}

    async def _audit(self, job: Job, log: Path) -> None:
        event = AUDIT_EVENT.get(job.kind)
        if event is None:
            return
        payload = json.dumps(
            {"job": job.id, "status": job.status, "params": job.params, "result": job.result},
            ensure_ascii=False,
        )
        cmd = [self.s.ctl, "audit", "--event", event, "--actor-id", str(job.actor_id)]
        cmd += ["--payload", payload]
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
