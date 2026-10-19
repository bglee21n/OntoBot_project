"""YAML/JSON ↔ 정의 문서(TRD 14.1 import·export). 순수 함수 — DB를 모른다."""

from __future__ import annotations

import json
from typing import Literal

import yaml
from pydantic import ValidationError

from ontobot.ontology.models import Issue, OntologyDocument, check_document

Format = Literal["yaml", "json"]


class ImportFailed(Exception):
    def __init__(self, issues: list[Issue]) -> None:
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in issues))
        self.issues = issues


def parse(text: str, fmt: Format) -> OntologyDocument:
    """문서를 읽고 교차 참조까지 검사한다.

    어긋나면 ``ImportFailed`` — 반쯤 읽힌 문서를 쓰지 않는다.
    """
    try:
        raw = yaml.safe_load(text) if fmt == "yaml" else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as e:
        raise ImportFailed([Issue("$", f"{fmt.upper()} 형식이 아닙니다: {e}")]) from e
    if not isinstance(raw, dict):
        raise ImportFailed([Issue("$", "최상위는 object_types·link_types 를 가진 객체여야 합니다")])
    try:
        doc = OntologyDocument.model_validate(raw)
    except ValidationError as e:
        raise ImportFailed(
            [Issue(".".join(str(p) for p in err["loc"]) or "$", err["msg"]) for err in e.errors()]
        ) from e
    issues = check_document(doc)
    if issues:
        raise ImportFailed(issues)
    return doc


def dump(doc: OntologyDocument, fmt: Format) -> str:
    data = doc.model_dump(mode="json", exclude_none=True)
    if fmt == "json":
        return json.dumps(data, ensure_ascii=False, indent=2)
    text: str = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    return text
