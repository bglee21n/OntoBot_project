"""오프라인 번들(ADR-0013·0014) — 컨테이너 이미지·모델·스택 파일·ontobotctl 을 한 폴더(와 zip)로.

    uv run --project backend python scripts/build_bundle.py --signing-key
    D:/keys/ontobot-release.key --zip
    uv run --project backend python scripts/build_bundle.py --app-only --signing-key … --zip   #
    업그레이드용(앱만)

인터넷이 되는 빌드 장비에서 돈다(도커 필요). 결과 dist/bundle/ontobot-<버전>/:

    bundle.env        버전·커밋·이미지 태그(KEY=VALUE — ontobotctl 이 읽는다, 실행하지 않는다)
    manifest.json     같은 내용 + 파일 목록(사람·배포 콘솔용)
    SHA256SUMS(.sig)  모든 파일 해시 + Ed25519 서명(scripts/bundle_sign.py)
    images/ontobot.tar        앱·배포 콘솔 이미지(docker save)
    images/third-party.tar    PostgreSQL(pgvector)·Neo4j·Ollama — images.lock 의 다이제스트로
    받는다(전체 번들만)
    ollama-models.tar         config/llm.yaml 의 Ollama 모델(전체 번들만)
    stack/            compose·postgres-init·app.env 견본·images.lock
    bin/ontobotctl    host/install-host.ps1    INSTALL.md

커밋하지 않은 변경이 있으면 버전에 ``-dirty`` 가 붙고, 서버(ontobotctl)는 그 번들을 거부한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bundle_sign

ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "deploy" / "docker"


def step(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def run(*cmd: str, capture: bool = False) -> str:
    res = subprocess.run(cmd, cwd=ROOT, check=True, text=True, capture_output=capture)  # noqa: S603
    return res.stdout.strip() if capture else ""


def version(label: str | None) -> tuple[str, str, bool]:
    app = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    commit = run("git", "rev-parse", "--short=10", "HEAD", capture=True)
    dirty = bool(run("git", "status", "--porcelain", capture=True))
    ver = f"{app['project']['version']}-{commit}"
    if label:
        ver += f"-{label}"
    if dirty:
        ver += "-dirty"
    return ver, commit, dirty


def read_lock() -> dict[str, str]:
    out = {}
    for line in (DOCKER / "images.lock").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v
    return out


def ollama_models(models_dir: Path, out: Path) -> list[str]:
    """llm.yaml 의 Ollama 역할 모델만.

    manifests·blobs 를 볼륨 뿌리(/root/.ollama) 기준 경로로 묶는다.
    """
    cfg = yaml.safe_load((ROOT / "config" / "llm.yaml").read_text(encoding="utf-8"))
    local = {n for n, p in cfg["providers"].items() if p["adapter"] == "ollama"}
    models = sorted({r["model"] for r in cfg["roles"].values() if r["provider"] in local})
    with tarfile.open(out, "w", format=tarfile.PAX_FORMAT) as tar:
        for m in models:
            name, _, tag = m.partition(":")
            rel = Path("manifests", "registry.ollama.ai", "library", name, tag or "latest")
            manifest = models_dir / rel
            if not manifest.is_file():
                sys.exit(f"Ollama 모델이 없습니다: {m} ({manifest}) — ollama pull {m}")
            tar.add(manifest, arcname=f"models/{rel.as_posix()}")
            data = json.loads(manifest.read_text(encoding="utf-8"))
            digests = [data["config"]["digest"], *(layer["digest"] for layer in data["layers"])]
            for d in digests:
                blob = d.replace(":", "-")
                tar.add(models_dir / "blobs" / blob, arcname=f"models/blobs/{blob}")
            print(f"    {m}: 조각 {len(digests)}개")
    return models


def copy_lf(src: Path, dst: Path) -> None:
    """WSL·컨테이너에서 도는 파일은 LF 로(체크아웃이 CRLF 여도)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / "bundle")
    ap.add_argument("--app-only", action="store_true", help="앱·배포 콘솔 이미지만(업그레이드용)")
    ap.add_argument("--signing-key", type=Path, help="Ed25519 개인키(PEM). 없으면 서명하지 않는다")
    ap.add_argument("--zip", action="store_true", help="브라우저 업로드용 zip 도 만든다")
    ap.add_argument("--ollama-models", type=Path, default=Path.home() / ".ollama" / "models")
    ap.add_argument("--label", help="버전 끝에 붙일 표시(시험용 — 같은 커밋으로 두 번 만들 때)")
    a = ap.parse_args(argv)

    ver, commit, dirty = version(a.label)
    if dirty:
        print(
            "경고: 커밋하지 않은 변경이 있습니다 — 버전에 -dirty 가 붙고 서버가 거부합니다",
            file=sys.stderr,
        )
    bundle = a.out / f"ontobot-{ver}"
    if bundle.exists():
        shutil.rmtree(bundle)
    (bundle / "images").mkdir(parents=True)
    step(f"번들 {bundle} (버전 {ver})")

    app_img, dep_img = f"ontobot-app:{ver}", f"ontobot-deployer:{ver}"
    for dockerfile, tag in (("app.Dockerfile", app_img), ("deployer.Dockerfile", dep_img)):
        step(f"이미지 빌드 {tag}")
        run(
            "docker",
            "build",
            "-f",
            f"deploy/docker/{dockerfile}",
            "--build-arg",
            f"ONTOBOT_VERSION={ver}",
            "-t",
            tag,
            ".",
        )
    step("이미지 저장 images/ontobot.tar")
    run("docker", "save", "-o", str(bundle / "images" / "ontobot.tar"), app_img, dep_img)

    lock = read_lock()
    models: list[str] = []
    if not a.app_only:
        third = []
        for key in ("PG", "NEO4J", "OLLAMA"):
            tag, digest = lock[f"ONTOBOT_{key}_IMAGE"], lock[f"ONTOBOT_{key}_DIGEST"]
            repo = tag.rsplit(":", 1)[0]
            step(f"제3자 이미지 {tag} ({digest[:19]}…)")
            run("docker", "pull", "-q", f"{repo}@{digest}")
            run("docker", "tag", f"{repo}@{digest}", tag)
            third.append(tag)
        step("이미지 저장 images/third-party.tar")
        run("docker", "save", "-o", str(bundle / "images" / "third-party.tar"), *third)
        step("Ollama 모델 ollama-models.tar")
        models = ollama_models(a.ollama_models, bundle / "ollama-models.tar")

    step("스택 파일·ontobotctl·설치 안내")
    for name in (
        "compose.yaml",
        "compose.gpu.yaml",
        "compose.deployer.yaml",
        "app.env.example",
        "images.lock",
    ):
        copy_lf(DOCKER / name, bundle / "stack" / name)
    for sub in ("postgres-init", "systemd"):
        for f in sorted((DOCKER / sub).iterdir()):
            copy_lf(f, bundle / "stack" / sub / f.name)
    copy_lf(DOCKER / "ontobotctl", bundle / "bin" / "ontobotctl")
    host = ROOT / "deploy" / "windows" / "install-host.ps1"
    if host.is_file():
        (bundle / "host").mkdir(exist_ok=True)
        shutil.copy2(host, bundle / "host" / "install-host.ps1")
    shutil.copy2(ROOT / "deploy" / "INSTALL.md", bundle / "INSTALL.md")

    meta = {
        "ONTOBOT_BUNDLE_VERSION": ver,
        "ONTOBOT_BUNDLE_COMMIT": commit,
        "ONTOBOT_BUNDLE_DIRTY": str(dirty).lower(),
        "ONTOBOT_BUNDLE_APP_ONLY": str(a.app_only).lower(),
        "ONTOBOT_BUNDLE_BUILT_AT": datetime.now(UTC).isoformat(timespec="seconds"),
        "ONTOBOT_APP_IMAGE": app_img,
        "ONTOBOT_DEPLOYER_IMAGE": dep_img,
        **{k: v for k, v in lock.items() if k.endswith("_IMAGE")},
    }
    (bundle / "bundle.env").write_bytes(
        ("".join(f"{k}={v}\n" for k, v in meta.items())).encode("utf-8")
    )
    files = {
        p.relative_to(bundle).as_posix(): p.stat().st_size
        for p in sorted(bundle.rglob("*"))
        if p.is_file()
    }
    (bundle / "manifest.json").write_text(
        json.dumps({**meta, "models": models, "files": files}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    if a.signing_key:
        step("서명(SHA256SUMS.sig)")
        bundle_sign.sign(bundle, a.signing_key)
    else:
        bundle_sign.write_sums(bundle)
        print(
            "경고: 서명하지 않았습니다 — 신뢰 키가 등록된 서버는 이 번들을 거부합니다",
            file=sys.stderr,
        )

    if a.zip:
        z = a.out / f"{bundle.name}.zip"
        step(f"zip {z.name}")
        with zipfile.ZipFile(z, "w", allowZip64=True) as zf:
            for p in sorted(bundle.rglob("*")):
                if p.is_file():
                    # 이미지 tar 는 이미 크고 압축 이득이 적다 — 그대로 담아 빨리 만든다.
                    method = zipfile.ZIP_STORED if p.suffix == ".tar" else zipfile.ZIP_DEFLATED
                    zf.write(
                        p, f"{bundle.name}/{p.relative_to(bundle).as_posix()}", compress_type=method
                    )
    size = sum(p.stat().st_size for p in bundle.rglob("*") if p.is_file()) >> 20
    step(f"완료: {bundle} ({size:,} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
