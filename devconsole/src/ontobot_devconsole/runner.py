"""작업 실행 — 한 번에 하나. 기록은 ``.devconsole/jobs``(opsjobs).

작업마다 시작할 때의 커밋·작업 트리 상태를 ``meta`` 에 남긴다 — 대시보드가 "지금 소스로 돌린
결과인가"를
판단한다. 결과는 출력에서 뽑는다(``results``): pytest 요약, 평가 게이트 표, 스크립트의 마지막 JSON.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from opsjobs import Busy, Job, JobStore, new_id, now, run_command, status_of

from ontobot_devconsole import bundles, git, results
from ontobot_devconsole.catalog import (
    CHECK_ALL,
    NEEDS_MODEL,
    CatalogError,
    script_command,
    task_command,
)
from ontobot_devconsole.envinfo import missing_models
from ontobot_devconsole.settings import Settings

__all__ = ["Busy", "DevJobs"]
RECOVER_MESSAGE = "개발 콘솔이 작업 도중 다시 시작되었습니다 — 결과를 다시 확인하세요"
KINDS = (
    "task",
    "check-all",
    "release",
    "bundle-build",
    "bundle-verify",
    "bundle-export",
    "release-notes",
    "push",
)


class DevJobs:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.store = JobStore(settings.data)
        self.store.recover(RECOVER_MESSAGE)
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    # ------------------------------------------------------------ 명령 만들기(검증은 여기서)

    def _task(self, name: str) -> list[str]:
        return task_command(self.s.root, name)

    def _bundle_build(self, params: dict[str, Any]) -> list[str]:
        args = ["--out", str(self.s.bundles)]
        if params.get("app_only"):
            args.append("--app-only")
        if params.get("zip"):
            args.append("--zip")
        if params.get("sign", True):
            key = self.s.signing_key()
            if key is None or not key.is_file():
                raise CatalogError("서명 키가 설정되지 않았습니다(.env 의 ONTOBOT_SIGNING_KEY)")
            args += ["--signing-key", str(key)]
        return script_command(self.s.root, "build_bundle.py", *args)

    def _bundle_verify(self, name: str) -> list[str]:
        path = bundles.bundle_dir(self.s.bundles, name)
        pub = self.s.signing_pub()
        if pub is None or not pub.is_file():
            raise CatalogError("공개키가 없습니다(서명 키와 같은 이름의 .pub)")
        return script_command(self.s.root, "bundle_sign.py", "verify", str(path), "--pub", str(pub))

    def plan(self, kind: str, params: dict[str, Any]) -> list[list[str]]:
        """작업 → 명령 목록. 인자가 틀리면 작업을 만들기 전에 CatalogError."""
        if kind == "task":
            name = str(params.get("name", ""))
            if name in NEEDS_MODEL:
                missing = missing_models(self.s.root)
                if missing:
                    raise CatalogError(
                        f"Ollama 에 모델이 없습니다: {', '.join(missing)} (ollama pull)"
                    )
            return [self._task(name)]
        if kind == "check-all":
            return [self._task(n) for n in CHECK_ALL]
        if kind == "release":
            # 출시 준비: 전체 검사 → 평가 재생 → 앱 번들(zip·서명) — 실패하면 그 자리에서 멈춘다.
            if git.status(self.s.root).dirty:
                raise CatalogError(
                    "커밋하지 않은 변경이 있습니다 — 번들이 -dirty 가 되어 서버가 거부합니다"
                )
            return [
                *[self._task(n) for n in CHECK_ALL],
                self._task("eval-prepare"),
                self._task("eval-replay"),
                self._bundle_build({"app_only": True, "zip": True, "sign": True}),
            ]
        if kind == "bundle-build":
            return [self._bundle_build(params)]
        if kind == "bundle-verify":
            return [self._bundle_verify(str(params.get("name", "")))]
        if kind == "push":
            return [git.push_command(self.s.root, bool(params.get("tags")))]
        if kind in ("bundle-export", "release-notes"):
            bundles.bundle_dir(self.s.bundles, str(params.get("name", "")))
            if kind == "bundle-export":
                bundles.check_target(Path(str(params.get("target", ""))), self.s.root)
            return []
        raise CatalogError(f"모르는 작업 종류: {kind}")

    # ------------------------------------------------------------ 실행

    async def start(self, kind: str, params: dict[str, Any]) -> Job:
        if self._lock.locked():
            raise Busy("다른 작업이 진행 중입니다")
        cmds = self.plan(kind, params)
        st = git.status(self.s.root)
        job = Job(
            id=new_id(),
            kind=f"task:{params['name']}" if kind == "task" else kind,
            params=params,
            actor_id=None,
            actor=os.environ.get("USERNAME") or os.environ.get("USER") or "dev",
            meta={"commit": st.head, "branch": st.branch, "dirty": st.dirty},
        )
        self.store.save(job)
        await self._lock.acquire()
        task = asyncio.create_task(self._run(job, cmds))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def _env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        return env | {"PYTHONUTF8": "1", "GIT_TERMINAL_PROMPT": "0"}

    async def _run(self, job: Job, cmds: list[list[str]]) -> None:
        log = self.store.log_path(job.id)
        lines: list[str] = []

        def on_line(line: str) -> None:
            lines.append(line)
            # task.py 는 단계마다 "$ (폴더) 명령" 을 찍는다 — 그것도 단계로 보인다.
            if line.startswith("$ ("):
                job.steps.append(line[2:])
                self.store.save(job)

        try:
            code = 0
            if job.kind == "bundle-export":
                code = await asyncio.to_thread(self._export, job, log)
            elif job.kind == "release-notes":
                code = await asyncio.to_thread(self._notes, job, log)
            for cmd in cmds:
                code, last = await run_command(
                    cmd,
                    log,
                    job=job,
                    store=self.store,
                    env=self._env(),
                    cwd=self.s.root,
                    on_line=on_line,
                )
                if last is not None:
                    job.result = last
                if code != 0:
                    break
            summary = results.summarize(job.kind, lines)
            if job.kind == "release":
                summary = {
                    "tests": results.pytest_summary(lines),
                    "eval": results.eval_summary(lines),
                }
            if summary is not None:
                job.result = {**(job.result or {}), **summary}
            job.exit_code = code
            job.status = status_of(code)
        except Exception as e:  # 작업 실패는 기록이다
            job.status, job.error = "failed", f"{type(e).__name__}: {e}"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(f"오류: {job.error}\n")
        finally:
            job.finished_at = now()
            self.store.save(job)
            self.store.append_history(job)
            self._lock.release()

    def _say(self, job: Job, log: Path, text: str, step: bool = False) -> None:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(("==> " if step else "    ") + text + "\n")
        if step:
            job.steps.append(text)
            self.store.save(job)

    def _export(self, job: Job, log: Path) -> int:
        """번들(폴더와 zip)을 반출 폴더·USB 로 복사하고, 복사본의 해시를 다시 대조한다."""
        name = job.params["name"]
        src = bundles.bundle_dir(self.s.bundles, name)
        target = bundles.check_target(Path(job.params["target"]), self.s.root)
        target.mkdir(parents=True, exist_ok=True)
        zip_src = self.s.bundles / f"{name}.zip"
        if zip_src.is_file():
            self._say(job, log, f"zip 복사 → {target / zip_src.name}", step=True)
            shutil.copy2(zip_src, target / zip_src.name)
            ok = bundles.sha256(zip_src) == bundles.sha256(target / zip_src.name)
            self._say(job, log, "zip 해시 일치" if ok else "zip 해시가 다릅니다")
            if not ok:
                return 1
        dest = target / name
        self._say(job, log, f"폴더 복사 → {dest}", step=True)
        if dest.exists():
            self._say(job, log, "같은 이름의 폴더가 이미 있어 덮어쓰지 않습니다")
            return 1
        shutil.copytree(src, dest)
        self._say(job, log, "복사본 해시 대조(SHA256SUMS)", step=True)
        bad = bundles.verify_sums(dest)
        for b in bad[:20]:
            self._say(job, log, f"다름: {b}")
        job.result = {"target": str(dest), "mismatches": len(bad)}
        return 0 if not bad else 1

    def _notes(self, job: Job, log: Path) -> int:
        """릴리스 노트 초안 — 번들 **옆**(번들 밖)에 둔다. 서명에 영향이 없다."""
        name = job.params["name"]
        last, lines = git.notes_since_last_tag(self.s.root)
        out = self.s.bundles / f"{name}.RELEASE_NOTES.md"
        when = datetime.now(UTC).date().isoformat()
        head = f"# {name}\n\n- 만든 날: {when}\n- 이전 태그: {last or '없음'}\n\n## 변경\n\n"
        out.write_text(head + "".join(f"- {line}\n" for line in lines), encoding="utf-8")
        self._say(job, log, f"릴리스 노트 초안 → {out}", step=True)
        job.result = {"file": str(out), "commits": len(lines)}
        return 0
