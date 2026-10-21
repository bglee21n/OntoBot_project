"""개발 서버 — 작업과 달리 오래 산다(dev-api·worker·dev-web). ``task.py`` 의 같은 이름 작업을
띄운다.

각자 프로세스 그룹으로 띄워 끌 때 자식(uvicorn 리로더·vite)까지 함께 끈다. 로그는
``.devconsole/services/<이름>.log``(켤 때마다 새로). 콘솔을 끄면 모두 끈다.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from ontobot_devconsole.catalog import uv

# 이름 → (task.py 작업, 확인할 포트 — 없으면 None)
SERVICES: dict[str, tuple[str, int | None]] = {
    "dev-api": ("dev-api", 8000),
    "worker": ("worker", None),
    "dev-web": ("dev-web", 5173),
}


class ServiceError(Exception):
    pass


def _descendants(root: int) -> list[int]:
    """Windows: ``root`` 의 자손 pid(깊은 것부터). ``taskkill /T`` 만으로는 막 띄운 자식을 놓친
    적이 있다(uv → 런처 → python 사이) — 끄기 전에 목록을 떠 두고 하나씩 끈다."""
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    k32 = getattr(ctypes, "windll").kernel32  # noqa: B009 — Windows 에만 있다
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    children: dict[int, list[int]] = {}
    try:
        e = Entry()
        e.dwSize = ctypes.sizeof(Entry)
        ok = k32.Process32First(snap, ctypes.byref(e))
        while ok:
            children.setdefault(e.th32ParentProcessID, []).append(e.th32ProcessID)
            ok = k32.Process32Next(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    out: list[int] = []
    stack = [root]
    while stack:
        pid = stack.pop()
        for c in children.get(pid, []):
            if c not in out and c != root:
                out.append(c)
                stack.append(c)
    return out[::-1]


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


@dataclass
class _Proc:
    popen: subprocess.Popen[bytes]
    started: float
    log: IO[bytes]


class Services:
    def __init__(self, root: Path, data: Path) -> None:
        self.root = root
        self.dir = data / "services"
        self._procs: dict[str, _Proc] = {}

    def _check(self, name: str) -> tuple[str, int | None]:
        if name not in SERVICES:
            raise ServiceError(f"모르는 서비스: {name}")
        return SERVICES[name]

    def log_path(self, name: str) -> Path:
        self._check(name)
        return self.dir / f"{name}.log"

    def running(self, name: str) -> bool:
        p = self._procs.get(name)
        return p is not None and p.popen.poll() is None

    def start(self, name: str) -> None:
        task, port = self._check(name)
        if self.running(name):
            return
        if port is not None and port_open(port):
            raise ServiceError(f"포트 {port} 를 이미 다른 프로그램이 쓰고 있습니다")
        self.dir.mkdir(parents=True, exist_ok=True)
        log = self.log_path(name).open("wb")
        cmd = [
            uv(),
            "run",
            "--project",
            str(self.root / "backend"),
            "python",
            "scripts/task.py",
            task,
        ]
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"} | {"PYTHONUTF8": "1"}
        popen = subprocess.Popen(
            cmd, cwd=self.root, stdout=log, stderr=subprocess.STDOUT, env=env, **kwargs
        )
        self._procs[name] = _Proc(popen, time.time(), log)

    def stop(self, name: str) -> None:
        self._check(name)
        p = self._procs.pop(name, None)
        if p is None:
            return
        if p.popen.poll() is None:
            if sys.platform == "win32":
                # 프로세스 트리째(uvicorn 리로더의 자식, npm → node).
                pids = [*_descendants(p.popen.pid), p.popen.pid]
                subprocess.run(
                    ["taskkill", "/PID", str(p.popen.pid), "/T", "/F"], capture_output=True
                )
                for pid in pids:  # /T 가 놓친 것
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
            else:
                os.killpg(p.popen.pid, signal.SIGTERM)
            try:
                p.popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.popen.kill()
        p.log.close()

    def stop_all(self) -> None:
        for name in list(self._procs):
            self.stop(name)

    def status(self) -> list[dict[str, Any]]:
        out = []
        for name, (_task, port) in SERVICES.items():
            p = self._procs.get(name)
            alive = p is not None and p.popen.poll() is None
            out.append(
                {
                    "name": name,
                    "running": alive,
                    "pid": p.popen.pid if alive and p else None,
                    "uptime_s": int(time.time() - p.started) if alive and p else None,
                    "exit_code": p.popen.returncode if p and not alive else None,
                    "port": port,
                    "port_open": port_open(port) if port else None,
                }
            )
        return out

    def read_log(self, name: str, offset: int) -> tuple[str, int]:
        p = self.log_path(name)
        if not p.is_file():
            return "", 0
        with p.open("rb") as f:
            f.seek(max(0, offset))
            data = f.read(128 * 1024)
        return data.decode("utf-8", errors="replace"), max(0, offset) + len(data)
