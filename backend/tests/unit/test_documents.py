"""Phase 16a — 문서 글 추출·청크(순수)."""

from __future__ import annotations

import itertools

import pytest

from ontobot.documents import chunking, extract


def _assert_substrings(text: str, chunks: list[chunking.Chunk]) -> None:
    for c in chunks:
        assert text[c.start : c.start + len(c.text)] == c.text  # 원문의 연속된 부분
    assert [c.no for c in chunks] == list(range(len(chunks)))


def test_paragraphs_are_packed_without_overlap() -> None:
    paras = [f"문단 {i} " + "가" * 300 for i in range(5)]
    text = "\n\n".join(paras)
    chunks = chunking.chunk(text, max_chars=700)
    _assert_substrings(text, chunks)
    assert [c.text.count("문단") for c in chunks] == [2, 2, 1]
    assert all(len(c.text) <= 700 for c in chunks)
    # 겹침 없음 — 청크가 앞으로만 나아간다.
    assert all(a.start + len(a.text) <= b.start for a, b in itertools.pairwise(chunks))


def test_long_paragraph_splits_at_sentence_end_then_by_length() -> None:
    sentence = "위험등급은 정기 점검 결과로 매긴다. "
    text = (sentence * 60).strip()
    chunks = chunking.chunk(text, max_chars=200)
    _assert_substrings(text, chunks)
    assert all(c.text.endswith(".") for c in chunks)
    blob = "가" * 450
    chunks = chunking.chunk(blob, max_chars=200)
    assert [len(c.text) for c in chunks] == [200, 200, 50]


def test_empty_and_whitespace() -> None:
    assert chunking.chunk("") == []
    assert chunking.chunk("\n\n   \n") == []
    text = "\n\n  첫 문단  \n\n\n둘째\n"
    chunks = chunking.chunk(text)
    _assert_substrings(text, chunks)
    assert chunks[0].text.startswith("첫 문단") and chunks[0].text.endswith("둘째")


def test_extract_text_normalizes_and_decodes_cp949() -> None:
    kind, text = extract.extract("a.txt", "가나\r\n다라   \r\n".encode("cp949"))
    assert (kind, text) == ("text/plain", "가나\n다라")
    # NFD(자모 분리) → NFC
    _, text = extract.extract("b.md", "가".encode())
    assert text == "가"
    _, text = extract.extract("c.txt", "﻿BOM".encode())
    assert text == "BOM"


@pytest.mark.parametrize(
    ("name", "data", "message"),
    [
        ("x.hwp", b"abc", "받을 수 있는 파일"),
        ("x.txt", b"   \n ", "빈 파일"),
        ("x.pdf", b"not a pdf", "PDF"),
    ],
)
def test_extract_rejects(name: str, data: bytes, message: str) -> None:
    with pytest.raises(extract.ExtractError, match=message):
        extract.extract(name, data)


def test_extract_pdf_text_layer() -> None:
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    w = pypdf.PdfWriter()
    page = w.add_blank_page(width=200, height=200)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (Risk grade 4) Tj ET")
    page[NameObject("/Contents")] = w._add_object(stream)
    import io

    buf = io.BytesIO()
    w.write(buf)
    kind, text = extract.extract("r.pdf", buf.getvalue())
    assert kind == "application/pdf" and "Risk grade 4" in text
