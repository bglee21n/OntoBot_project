"""샘플 데이터 질의 시연(Phase 18c, PRD 5.8 P1) — 게시 전 draft 를 샘플 행으로 물어 본다.

- 행은 draft 의 격리 스키마(``objects.demo_store``)에만 들어간다. 운영 ``object`` 표·그래프·동기화와
  섞이지 않는다. 질문은 **운영 파이프라인 그대로**(계획 → 검증 → 실행 → 답변 → 근거 대조)를 그
  스키마에서 돈다 — 저장소 주입은 ``demo_store.use`` 하나뿐이다.
- draft 가 게시 전 검사를 통과해야 시연할 수 있다(키 속성 없는 타입으로는 근거를 만들 수 없다).
- CSV 첫 줄은 속성 이름(``api_name``)이다. 정의에 없는 열·pk 없는 행·자료형이 안 맞는 값은 그 표
  전체를 받지 않는다(동기화와 같다 — 행 하나라도 실패면 전체 실패). 최대 ``MAX_ROWS`` 행.
- 읽기 권한은 draft 의 모든 타입(시연하는 모델러가 만든 샘플이다). 그래프가 없어 탐색 조건은
  ``graph_unavailable``.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.chat import pipeline
from ontobot.llm.gateway import LLMGateway
from ontobot.objects import demo_store
from ontobot.objects.store import seed_fk_links, upsert_objects
from ontobot.ontology import versioning
from ontobot.ontology.models import Issue, OntologyDocument, PropertyDef, check_document
from ontobot.ontology.registry import PublishedSchema

MAX_ROWS = 1000
MAX_CSV_CHARS = 1_000_000


class DemoNotReady(Exception):
    def __init__(self, issues: list[Issue]) -> None:
        super().__init__(f"{len(issues)} issue(s)")
        self.issues = issues


class RowsInvalid(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors[:3]))
        self.errors = errors


async def schema_of(db: AsyncSession, version_id: int) -> PublishedSchema:
    """시연에 쓸 스키마 — 게시 전 검사를 통과한 버전만.

    폐기 요소는 뺀다(플래너가 보는 것과 같게).
    """
    version = await versioning.get_version(db, version_id)
    doc = versioning.to_document(version)
    issues = check_document(doc)
    if issues:
        raise DemoNotReady(issues)
    return PublishedSchema(version.id, version.version_no, doc.content_hash(), doc)


def _convert(prop: PropertyDef, raw: str) -> Any:
    v = raw.strip()
    if v == "":
        return None
    if prop.data_type == "int":
        return int(v)
    if prop.data_type == "decimal":
        d = Decimal(v.replace(",", ""))
        return int(d) if d == d.to_integral_value() else float(d)
    if prop.data_type == "bool":
        if v.lower() not in {"true", "false", "1", "0", "y", "n", "예", "아니오"}:
            raise ValueError(v)
        return v.lower() in {"true", "1", "y", "예"}
    if prop.data_type == "date":
        return date.fromisoformat(v).isoformat()
    if prop.data_type == "datetime":
        return datetime.fromisoformat(v).isoformat()
    if prop.data_type == "enum" and v not in (prop.enum_values or []):
        raise ValueError(f"{v} 는 {prop.enum_values} 가 아니다")
    return v


def parse_rows(
    doc: OntologyDocument, object_type: str, csv_text: str
) -> list[tuple[str, dict[str, Any]]]:
    """CSV → ``(pk, properties)``. 오류는 모두 모아 ``RowsInvalid`` 로(한 번에 고치게)."""
    t = doc.object_type(object_type)
    if t is None:
        raise RowsInvalid([f"draft 에 객체 타입 '{object_type}' 이 없습니다"])
    if len(csv_text) > MAX_CSV_CHARS:
        raise RowsInvalid([f"CSV 가 너무 깁니다(최대 {MAX_CSV_CHARS:,}자)"])
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    header = [h.strip() for h in (reader.fieldnames or [])]
    known = {p.api_name: p for p in t.properties}
    errors = [f"정의에 없는 열: {h}" for h in header if h not in known]
    if t.pk_property not in header:
        errors.append(f"pk 열 '{t.pk_property}' 이 없습니다")
    if errors:
        raise RowsInvalid(errors)
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for n, row in enumerate(reader, start=2):
        if n - 1 > MAX_ROWS:
            raise RowsInvalid([f"행이 너무 많습니다(최대 {MAX_ROWS}행)"])
        props: dict[str, Any] = {}
        for h in header:
            try:
                props[h] = _convert(known[h], (row.get(h) or ""))
            except (ValueError, InvalidOperation) as e:
                errors.append(f"{n}행 {h}: {known[h].data_type} 값이 아닙니다({e})")
        pk = str(props.get(t.pk_property) or "").strip()
        if not pk:
            errors.append(f"{n}행: pk('{t.pk_property}') 가 비었습니다")
        elif pk in seen:
            errors.append(f"{n}행: pk '{pk}' 가 겹칩니다")
        seen.add(pk)
        out.append((pk, props))
    if errors:
        raise RowsInvalid(errors)
    return out


async def load_rows(
    db: AsyncSession, schema: PublishedSchema, object_type: str, csv_text: str
) -> tuple[int, int]:
    """샘플 행 넣기 → (바뀐 행, 새 링크). FK 링크 규칙은 draft 의 것을 모두 다시 돈다."""
    rows = parse_rows(schema.document, object_type, csv_text)
    await demo_store.ensure(db, schema.version_id)
    await demo_store.use(db, schema.version_id)
    changed = await upsert_objects(db, object_type, rows)
    links = 0
    for lt in schema.document.link_types:
        if lt.source.kind == "fk_property" and lt.source.property:
            links += await seed_fk_links(db, lt.api_name, lt.from_type, lt.source.property)
    return changed, links


async def ask(
    db: AsyncSession, llm: LLMGateway, schema: PublishedSchema, question: str
) -> pipeline.PipelineResult:
    """운영 파이프라인 그대로 — 시연 스키마에서, 그래프 없이, draft 의 모든 타입을 읽는다."""
    await demo_store.use(db, schema.version_id)
    doc = schema.document.active()
    return await pipeline.run(
        db, llm, schema, frozenset(t.api_name for t in doc.object_types), question
    )
