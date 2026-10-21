"""만든 번들 — ``dist/bundle`` 의 목록·해시 대조·반출 대상 검사."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^ontobot-[A-Za-z0-9._-]{1,120}$")


class BundleError(Exception):
    pass


def bundle_dir(root: Path, name: str) -> Path:
    if not NAME_RE.match(name) or not (root / name / "bundle.env").is_file():
        raise BundleError(f"없는 번들입니다: {name}")
    return root / name


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                out[k] = v
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sums(bundle: Path) -> list[str]:
    """SHA256SUMS 와 다른 파일 목록(서명은 보지 않는다 — 복사가 온전한지만)."""
    sums = bundle / "SHA256SUMS"
    if not sums.is_file():
        return ["SHA256SUMS 없음"]
    bad = []
    for line in sums.read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        p = bundle / rel
        if not p.is_file() or sha256(p) != digest:
            bad.append(rel)
    return bad


def check_target(target: Path, repo: Path) -> Path:
    """반출 대상 — 절대 경로, 저장소 밖(번들을 저장소에 복사하면 커밋 위험)."""
    if not str(target) or not target.is_absolute():
        raise BundleError("반출할 폴더를 절대 경로로 주세요(예: E:\\ 또는 D:\\OntoBot-release)")
    t = target.resolve()
    if t == repo.resolve() or repo.resolve() in t.parents:
        raise BundleError("저장소 안으로는 반출하지 않습니다")
    return t


def _size_mb(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) >> 20


def list_bundles(root: Path, verified: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """``verified`` 는 번들 이름 → 마지막 서명 확인 작업(상태·시각)."""
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        env = read_env(d / "bundle.env")
        if not d.is_dir() or not env or not NAME_RE.match(d.name):
            continue
        out.append(
            {
                "name": d.name,
                "version": env.get("ONTOBOT_BUNDLE_VERSION"),
                "commit": env.get("ONTOBOT_BUNDLE_COMMIT"),
                "dirty": env.get("ONTOBOT_BUNDLE_DIRTY") == "true",
                "app_only": env.get("ONTOBOT_BUNDLE_APP_ONLY") == "true",
                "built_at": env.get("ONTOBOT_BUNDLE_BUILT_AT"),
                "size_mb": _size_mb(d),
                "signed": (d / "SHA256SUMS.sig").is_file(),
                "zip": (root / f"{d.name}.zip").is_file(),
                "notes": (root / f"{d.name}.RELEASE_NOTES.md").is_file(),
                "verify": verified.get(d.name),
            }
        )
    return out


def remove(root: Path, name: str) -> None:
    d = bundle_dir(root, name)
    shutil.rmtree(d)
    for extra in (root / f"{name}.zip", root / f"{name}.RELEASE_NOTES.md"):
        extra.unlink(missing_ok=True)
