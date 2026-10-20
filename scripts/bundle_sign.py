"""오프라인 번들 서명(ADR-0014) — Ed25519. 서버는 같은 서명을 `openssl` 로 확인한다(파이썬 불필요).

    uv run --project backend python scripts/bundle_sign.py keygen --out D:/keys/ontobot-release
        → ontobot-release.key(개인키 — 배포 담당자만, 오프라인 보관) · ontobot-release.pub(서버에
        등록)
    uv run --project backend python scripts/bundle_sign.py sign <번들 폴더> --key
    D:/keys/ontobot-release.key
    uv run --project backend python scripts/bundle_sign.py verify <번들 폴더> --pub
    D:/keys/ontobot-release.pub

서명 대상은 번들의 ``SHA256SUMS``(번들의 모든 파일 해시, `sha256sum -c` 형식)다. 서명이 맞으면
목록이
진짜이고, 목록이 맞으면 파일이 진짜다. 서명은 ``SHA256SUMS.sig``(원시 64바이트를 base64 한 줄).
서버 확인(ontobotctl):

    base64 -d SHA256SUMS.sig > sig.bin
    openssl pkeyutl -verify -pubin -inkey key.pub -rawin -in SHA256SUMS -sigfile sig.bin
    sha256sum -c --quiet SHA256SUMS
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import sys
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SUMS = "SHA256SUMS"
SIG = "SHA256SUMS.sig"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_sums(bundle: Path) -> Path:
    """번들의 모든 파일(서명·목록 자신 제외) → SHA256SUMS. 경로는 / 구분, 정렬."""
    lines = []
    for p in sorted(bundle.rglob("*")):
        rel = p.relative_to(bundle).as_posix()
        if p.is_file() and rel not in (SUMS, SIG):
            lines.append(f"{sha256(p)}  {rel}\n")
    out = bundle / SUMS
    # 줄 끝은 LF — 서버의 sha256sum 이 읽는다.
    out.write_bytes("".join(lines).encode("utf-8"))
    return out


def keygen(out: Path) -> None:
    key = Ed25519PrivateKey.generate()
    priv = out.with_suffix(".key")
    pub = out.with_suffix(".pub")
    if priv.exists() or pub.exists():
        sys.exit(f"이미 있습니다: {priv} / {pub}")
    priv.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    print(
        f"개인키 {priv} (외부 유출 금지 — 오프라인 보관)\n공개키 {pub} (서버 trusted-keys 에 등록)"
    )


def load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("Ed25519 개인키가 아닙니다")
    return key


def load_public(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        sys.exit("Ed25519 공개키가 아닙니다")
    return key


def sign(bundle: Path, key_path: Path) -> None:
    sums = write_sums(bundle)
    sig = load_private(key_path).sign(sums.read_bytes())
    # 바이트로 쓴다 — Windows 텍스트 모드는 줄 끝을 CRLF 로 바꿔 서버의 base64 -d 가 거부한다.
    (bundle / SIG).write_bytes(base64.b64encode(sig) + b"\n")
    print(f"서명: {bundle / SIG}")


def verify(bundle: Path, pubs: list[Path]) -> bool:
    """서명이 등록된 공개키 중 하나와 맞고, 목록의 모든 파일 해시가 맞고,
    목록에 없는 파일이 없는가."""
    sums, sig_file = bundle / SUMS, bundle / SIG
    if not sums.is_file() or not sig_file.is_file():
        print("서명 또는 목록이 없습니다")
        return False
    data = sums.read_bytes()
    sig = base64.b64decode(sig_file.read_text(encoding="ascii").strip())
    for pub in pubs:
        try:
            load_public(pub).verify(sig, data)
            break
        except InvalidSignature:
            continue
    else:
        print("서명이 등록된 공개키와 맞지 않습니다")
        return False
    listed = {}
    for line in data.decode("utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        listed[rel] = digest
    actual = {
        p.relative_to(bundle).as_posix()
        for p in bundle.rglob("*")
        if p.is_file() and p.name not in (SUMS, SIG)
    }
    extra = sorted(actual - set(listed))
    bad = [
        rel
        for rel, d in listed.items()
        if not (bundle / rel).is_file() or sha256(bundle / rel) != d
    ]
    if extra or bad:
        print(f"목록과 다른 파일: {bad[:10]} · 목록에 없는 파일: {extra[:10]}")
        return False
    print(f"서명·파일 {len(listed)}개 일치")
    return True


def main(argv: list[str]) -> int:
    # 한국어 Windows 콘솔(cp949)에서 출력할 수 없는 글자로 죽지 않게(키는 이미 썼을 수 있다).
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", type=Path, required=True)
    s = sub.add_parser("sign")
    s.add_argument("bundle", type=Path)
    s.add_argument("--key", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("bundle", type=Path)
    v.add_argument("--pub", type=Path, action="append", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "keygen":
        keygen(a.out)
    elif a.cmd == "sign":
        sign(a.bundle, a.key)
    else:
        return 0 if verify(a.bundle, a.pub) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
