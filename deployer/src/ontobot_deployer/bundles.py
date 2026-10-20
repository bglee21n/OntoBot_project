"""번들 — 브라우저 업로드(조각)·반입 폴더 → ``$ONTOBOT_HOME/bundles/<이름>``.

번들을 언제나 ``ONTOBOT_HOME`` 아래로 옮긴다. ontobotctl 이 모델 볼륨을 채울 때 번들 경로를 도커
데몬에
마운트하는데, 반입 폴더(콘솔 컨테이너의 /drop)는 데몬이 보는 경로가 아니기 때문이다.

압축 해제는 안전하게만: 절대 경로·``..``·심볼릭 링크·최상위 폴더가 둘 이상인 zip 은 거부한다. 진위는
여기서
판단하지 않는다 — ``ontobotctl preflight`` 가 서명·해시로 확인한다(번들 폴더 안에는 아무것도 쓰지
않는다).
"""

from __future__ import annotations

import json
import re
import shutil
import stat
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
UPLOAD_RE = re.compile(r"^[a-f0-9]{32}$")


class BundleError(Exception):
    pass


def read_env(path: Path) -> dict[str, str]:
    """KEY=VALUE 파일. 셸로 실행하지 않고 글자 그대로 읽는다."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v
    return out


@dataclass
class BundleInfo:
    name: str
    version: str | None
    commit: str | None
    dirty: bool
    app_only: bool
    built_at: str | None
    size_mb: int
    location: str  # "imported"(콘솔이 쓸 수 있음) | "drop"(반입 폴더 — 가져오기 전)
    preflight: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _size_mb(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size // (1 << 20)
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) // (1 << 20)


def preflight_path(data: Path, name: str) -> Path:
    return data / "bundles" / f"{name}.json"


def list_bundles(bundles: Path, data: Path, drop: Path | None) -> list[BundleInfo]:
    out: list[BundleInfo] = []
    names: set[str] = set()
    if bundles.is_dir():
        for d in sorted(bundles.iterdir(), reverse=True):
            env = read_env(d / "bundle.env")
            if not d.is_dir() or not env:
                continue
            names.add(d.name)
            pf = preflight_path(data, d.name)
            out.append(
                BundleInfo(
                    name=d.name,
                    version=env.get("ONTOBOT_BUNDLE_VERSION"),
                    commit=env.get("ONTOBOT_BUNDLE_COMMIT"),
                    dirty=env.get("ONTOBOT_BUNDLE_DIRTY") == "true",
                    app_only=env.get("ONTOBOT_BUNDLE_APP_ONLY") == "true",
                    built_at=env.get("ONTOBOT_BUNDLE_BUILT_AT"),
                    size_mb=_size_mb(d),
                    location="imported",
                    preflight=json.loads(pf.read_text(encoding="utf-8")) if pf.is_file() else None,
                )
            )
    if drop is not None and drop.is_dir():
        for p in sorted(drop.iterdir(), reverse=True):
            stem = p.name.removesuffix(".zip")
            if stem in names or not NAME_RE.match(p.name):
                continue
            if p.is_dir() and not (p / "bundle.env").is_file():
                continue
            if p.is_file() and p.suffix != ".zip":
                continue
            env = read_env(p / "bundle.env") if p.is_dir() else {}
            out.append(
                BundleInfo(
                    name=p.name,
                    version=env.get("ONTOBOT_BUNDLE_VERSION"),
                    commit=env.get("ONTOBOT_BUNDLE_COMMIT"),
                    dirty=env.get("ONTOBOT_BUNDLE_DIRTY") == "true",
                    app_only=env.get("ONTOBOT_BUNDLE_APP_ONLY") == "true",
                    built_at=env.get("ONTOBOT_BUNDLE_BUILT_AT"),
                    size_mb=_size_mb(p),
                    location="drop",
                    preflight=None,
                )
            )
    return out


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def safe_extract(zip_path: Path, dest_root: Path) -> Path:
    """zip → ``dest_root/<최상위 폴더>``.

    끝까지 푼 뒤에만 이름을 바꿔 반쯤 풀린 번들이 보이지 않게 한다.
    """
    with zipfile.ZipFile(zip_path) as zf:
        tops: set[str] = set()
        for info in zf.infolist():
            p = PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts or "\\" in info.filename or _is_symlink(info):
                raise BundleError(f"안전하지 않은 항목: {info.filename}")
            if p.parts:
                tops.add(p.parts[0])
        if len(tops) != 1:
            raise BundleError("번들 zip 은 최상위 폴더 하나만 담아야 합니다")
        top = tops.pop()
        if not NAME_RE.match(top):
            raise BundleError(f"번들 폴더 이름이 잘못되었습니다: {top}")
        final = dest_root / top
        if final.exists():
            raise BundleError(f"이미 가져온 번들입니다: {top}")
        tmp = dest_root / f".{top}.partial"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        try:
            for info in zf.infolist():
                rel = PurePosixPath(info.filename).relative_to(top)
                target = tmp.joinpath(*rel.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
            tmp.rename(final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    return final


def import_drop(drop: Path, name: str, dest_root: Path) -> Path:
    """반입 폴더의 zip·폴더 → bundles. 이름은 목록에 나온 것만(경로 조작 금지)."""
    if not NAME_RE.match(name):
        raise BundleError("이름이 잘못되었습니다")
    src = drop / name
    if not src.exists():
        raise BundleError(f"반입 폴더에 없습니다: {name}")
    dest_root.mkdir(parents=True, exist_ok=True)
    if src.is_file():
        return safe_extract(src, dest_root)
    final = dest_root / name
    if final.exists():
        raise BundleError(f"이미 가져온 번들입니다: {name}")
    tmp = dest_root / f".{name}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    # 심볼릭 링크는 따라가지 않고 링크로 복사한다 — 서명 확인(파일 목록)에서 걸린다.
    shutil.copytree(src, tmp, symlinks=True)
    tmp.rename(final)
    return final


def remove(dest_root: Path, data: Path, name: str) -> None:
    if not NAME_RE.match(name) or not (dest_root / name).is_dir():
        raise BundleError("없는 번들입니다")
    shutil.rmtree(dest_root / name)
    preflight_path(data, name).unlink(missing_ok=True)


# ---------------------------------------------------------------- 업로드(조각)


class Uploads:
    """``PUT /api/uploads/{id}`` 로 조각을 순서대로 붙인다.

    오프셋이 다르면 거부한다 — 이어 올리기는 크기를 물어 맞춘다.
    """

    def __init__(self, incoming: Path, max_mb: int) -> None:
        self.dir = incoming
        self.max_bytes = max_mb << 20

    def path(self, upload_id: str) -> Path:
        if not UPLOAD_RE.match(upload_id):
            raise BundleError("업로드 ID 가 잘못되었습니다")
        return self.dir / f"{upload_id}.zip.part"

    def create(self, upload_id: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.path(upload_id)
        if not p.exists():
            p.touch()

    def size(self, upload_id: str) -> int:
        p = self.path(upload_id)
        if not p.exists():
            raise BundleError("없는 업로드입니다")
        return p.stat().st_size

    def append(self, upload_id: str, offset: int, data: bytes) -> int:
        p = self.path(upload_id)
        size = self.size(upload_id)
        if offset != size:
            raise BundleError(f"오프셋이 맞지 않습니다(서버 {size})")
        if size + len(data) > self.max_bytes:
            raise BundleError("업로드 크기 상한을 넘었습니다")
        with p.open("ab") as f:
            f.write(data)
        return size + len(data)

    def finish(self, upload_id: str, dest_root: Path) -> Path:
        p = self.path(upload_id)
        if not zipfile.is_zipfile(p):
            raise BundleError("zip 파일이 아닙니다")
        try:
            return safe_extract(p, dest_root)
        finally:
            p.unlink(missing_ok=True)
