"""파일 → 글(Phase 16). 텍스트(.txt·.md)와 PDF 텍스트 층만 받는다.

스캔 PDF(글자 층 없음)·HWP 는 받지 않는다.

정규화는 NFC·줄바꿈 ``\n``·끝 공백 정리뿐이다. 근거 대조가 청크 원문을 그대로 찾으므로 그 밖의 변형
(공백 합치기·문장 부호 바꾸기)을 하지 않는다. PDF 는 ``pypdf`` 가 뽑은 글을 쪽마다 빈 줄로 잇는다.
"""

from __future__ import annotations

import io
import unicodedata
from pathlib import PurePath

MAX_BYTES = 20 * 1024 * 1024
MAX_CHARS = 2_000_000
TEXT_TYPES = {".txt": "text/plain", ".md": "text/markdown"}
PDF = "application/pdf"


class ExtractError(Exception):
    """받을 수 없는 파일 — 사용자에게 그대로 보여 줄 문장."""


def media_type(filename: str) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext in TEXT_TYPES:
        return TEXT_TYPES[ext]
    if ext == ".pdf":
        return PDF
    raise ExtractError("받을 수 있는 파일은 .txt·.md·.pdf(글자 층이 있는 것)입니다")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return "\n".join(lines).strip("\n")


def _decode(data: bytes) -> str:
    # 한국어 Windows 문서는 UTF-8 이 아닐 때가 많다(cp949). BOM 은 utf-8-sig 가 벗긴다.
    for enc in ("utf-8-sig", "cp949"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ExtractError("글자 인코딩을 알 수 없습니다(UTF-8 또는 CP949 로 저장해 주세요)")


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader  # 무거운 import 는 PDF 를 받을 때만
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ExtractError("암호가 걸린 PDF 는 받을 수 없습니다")
        pages = [p.extract_text() or "" for p in reader.pages]
    except PdfReadError as e:
        raise ExtractError("PDF 를 읽을 수 없습니다") from e
    return "\n\n".join(p.strip() for p in pages if p.strip())


def extract(filename: str, data: bytes) -> tuple[str, str]:
    """(미디어 형식, 정규화한 글)."""
    if len(data) > MAX_BYTES:
        raise ExtractError(f"파일이 너무 큽니다(최대 {MAX_BYTES // (1024 * 1024)}MB)")
    kind = media_type(filename)
    text = normalize(_pdf(data) if kind == PDF else _decode(data))
    if not text.strip():
        raise ExtractError(
            "글자를 찾지 못했습니다(스캔한 PDF 는 글자 층이 없습니다)"
            if kind == PDF
            else "빈 파일입니다"
        )
    if len(text) > MAX_CHARS:
        raise ExtractError(f"글이 너무 깁니다(최대 {MAX_CHARS:,}자)")
    return kind, text
