"""개발 환경 정보 — Ollama 모델, Docker, 디스크, .env 키.

실패하면 "모름"으로 보인다(화면은 계속 뜬다).
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from ontobot_devconsole.settings import read_env_file

_MODEL = re.compile(r"\bmodel:\s*\"?([A-Za-z0-9._:/-]+)\"?")
_PROVIDER = re.compile(r"\bprovider:\s*(\w+)")


def required_models(root: Path) -> list[str]:
    """config/llm.yaml 의 roles 가 쓰는 모델(로컬 공급자). yaml 라이브러리 없이 줄 단위로 읽는다."""
    text = (root / "config" / "llm.yaml").read_text(encoding="utf-8")
    roles = text.split("roles:", 1)[1] if "roles:" in text else ""
    out = []
    for line in roles.splitlines():
        m = _MODEL.search(line)
        p = _PROVIDER.search(line)
        if m and (p is None or p.group(1) == "local"):
            out.append(m.group(1))
    return sorted(set(out))


def ollama_models(base: str = "http://127.0.0.1:11434") -> list[str] | None:
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=2) as r:  # noqa: S310 — 고정 로컬 주소
            data = json.loads(r.read())
    except (OSError, ValueError):
        return None
    return sorted(m["name"] for m in data.get("models", []))


def missing_models(root: Path) -> list[str]:
    have = ollama_models()
    need = required_models(root)
    if have is None:
        return [f"{m}(Ollama 응답 없음)" for m in need]
    return [m for m in need if m not in have]


def _run_json_lines(*cmd: str) -> list[dict[str, Any]] | None:
    try:
        res = subprocess.run(list(cmd), capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    text = res.stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return []
    if text.startswith("["):
        value = json.loads(text)
        return value if isinstance(value, list) else None
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def docker_projects() -> list[dict[str, Any]] | None:
    return _run_json_lines("docker", "compose", "ls", "-a", "--format", "json")


def docker_disk() -> list[dict[str, Any]] | None:
    return _run_json_lines("docker", "system", "df", "--format", "json")


def dir_size_mb(path: Path) -> int | None:
    if not path.exists():
        return None
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) >> 20


def env_keys(root: Path) -> dict[str, Any]:
    """.env 의 키 이름만(값은 내지 않는다)과 env.example 대비 빠진 키."""
    have = set(read_env_file(root / ".env"))
    example = set(read_env_file(root / "deploy" / "env.example"))
    return {"present": sorted(have), "missing": sorted(example - have)}


def summary(root: Path, data: Path, bundles: Path) -> dict[str, Any]:
    have = ollama_models()
    need = required_models(root)
    return {
        "ollama": {
            "reachable": have is not None,
            "models": have or [],
            "required": need,
            "missing": [m for m in need if have is None or m not in have],
        },
        "docker": {"projects": docker_projects(), "disk": docker_disk()},
        "disk": {
            "bundles_mb": dir_size_mb(bundles),
            "devconsole_mb": dir_size_mb(data),
        },
        "env": env_keys(root),
    }
