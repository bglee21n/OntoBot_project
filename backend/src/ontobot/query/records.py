"""조회 결과 → 답변 LLM 에 줄 번호 붙은 레코드(TRD 8.1).

각 값은 **원값과 서버가 만든 표시 문자열**을 함께 싣는다(천 단위 구분, 날짜 형식). 모델이 숫자를
제멋대로 반올림하면 근거 대조에 실패하도록 하기 위함이다 — 대조는 두 표기 중 하나가 문장에
그대로 있는지만 본다.

인용 표기:
- 객체 속성: ``o:Facility/F-001#risk_grade``
- 질의 결과(집계 행): ``q:1#value``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ontobot.objects.reader import ObjectRow
from ontobot.ontology.models import ObjectTypeDef, PropertyDef
from ontobot.query.plan import AGG_VALUE
from ontobot.query.validator import ValidatedPlan

_AGG_LABEL = {"count": "건수", "sum": "합계", "avg": "평균", "min": "최솟값", "max": "최댓값"}


def display(prop: PropertyDef | None, value: Any) -> str:
    if value is None:
        return "—"
    kind = prop.data_type if prop is not None else None
    if kind == "bool" or isinstance(value, bool):
        return "예" if value in (True, "true") else "아니오"
    if kind in {"int", "decimal"} or isinstance(value, (int, float, Decimal)):
        try:
            d = Decimal(str(value))
        except ArithmeticError:
            return str(value)
        if d == d.to_integral_value():
            return f"{int(d):,}"
        return f"{d.normalize():,f}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    raw: Any
    display: str


@dataclass(frozen=True)
class Record:
    ref: str  # o:Type/pk 또는 q:n
    title: str
    type: str | None  # 객체 레코드의 타입(권한 대조용). 집계 행은 None
    fields: tuple[Field, ...]
    synced_at: datetime | None = None

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)


@dataclass
class RecordSet:
    records: list[Record] = field(default_factory=list)
    # 대조 때 '사실 문장'을 가리는 데 쓴다(TRD 8.2-2): 결과 객체 제목과 enum 값.
    titles: set[str] = field(default_factory=set)
    enum_values: set[str] = field(default_factory=set)

    def by_ref(self, ref: str) -> Record | None:
        return next((r for r in self.records if r.ref == ref), None)


def _object_record(t: ObjectTypeDef, row: ObjectRow) -> Record:
    fields = tuple(
        Field(
            p.api_name,
            p.display_name,
            row.properties.get(p.api_name),
            display(p, row.properties.get(p.api_name)),
        )
        # PII 도 싣는다 — 읽을 권한이 있는 사용자의 답이다. 로컬 모델이 아닌 외부 공급자로 갈 때는
        # 게이트웨이가 가역 토큰으로 바꾸고(Phase 5), 로그·트레이스에는 마스킹해 남긴다(TRD 9.2).
        for p in t.properties
    )
    title = str(row.properties.get(t.title_property) or row.pk)
    return Record(f"o:{t.api_name}/{row.pk}", title, t.api_name, fields, row.synced_at)


def build(vp: ValidatedPlan, rows: list[ObjectRow] | list[dict[str, Any]]) -> RecordSet:
    rs = RecordSet()
    t = vp.target
    for p in t.properties:
        rs.enum_values.update(p.enum_values or [])
    if vp.aggregate is None:
        for obj in rows:
            if not isinstance(obj, ObjectRow):
                raise TypeError("객체 조회 결과가 아닙니다")
            rec = _object_record(t, obj)
            rs.records.append(rec)
            rs.titles.add(rec.title)
        return rs
    a = vp.aggregate
    for i, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            raise TypeError("집계 결과가 아닙니다")
        row: dict[str, Any] = item
        fields = [
            Field(g.api_name, g.display_name, row[f"g{j}"], display(g, row[f"g{j}"]))
            for j, g in enumerate(a.group_by)
        ]
        subject = a.prop.display_name if a.prop is not None else "건"
        label = f"{_AGG_LABEL[a.fn]}({subject})"
        fields.append(Field(AGG_VALUE, label, row[AGG_VALUE], display(a.prop, row[AGG_VALUE])))
        rs.records.append(Record(f"q:{i}", f"결과 {i}", None, tuple(fields)))
    return rs


def render_for_llm(rs: RecordSet) -> str:
    """답변 모델에 넣는 데이터 블록. 지시문과 섞이지 않게 구분선 안에만 둔다(TRD 9.3)."""
    # 줄마다 인용 키를 **통째로** 보여 준다. 예전에는 "value (평균(위험등급)): 2.56" 처럼 적어
    # 모델이 괄호 속 라벨까지 인용에 베껴 넣었고(스파이크 실측), 깨진 인용 탓에 맞는 답이 ③이 됐다.
    lines = ["<data>"]
    for r in rs.records:
        lines.append(f"## {r.ref} — {r.title}")
        for f in r.fields:
            lines.append(f"{r.ref}#{f.name} | {f.label} | {f.display}")
    lines.append("</data>")
    return "\n".join(lines)
