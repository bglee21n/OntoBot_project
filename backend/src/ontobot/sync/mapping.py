"""매핑 정의·검증·행 변환(TRD 6.4, 11절).

매핑은 **현재 게시 버전에 실재하는** 타입·속성·링크 타입만 가리킬 수 있다. 저장할 때 검사하고,
실행할 때 한 번 더 검사한다 — 그 사이에 새 버전이 게시돼 속성이 빠졌을 수 있기 때문이다.

행 변환은 속성 자료형을 지킨다. 한 행이라도 못 바꾸면 **실행 전체를 실패**시킨다 — 그 행을 조용히
건너뛰면 원천에는 있는 객체가 챗봇에는 없는데 아무도 모른다(TRD 11절 "실패한 실행은 기존 데이터를
지우거나 반쯤 바꾸지 않는다").
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ontobot.ontology.models import ObjectTypeDef, OntologyDocument, PropertyDef
from ontobot.sync.connectors.base import IDENT, TABLE, Row

SCHEDULE = re.compile(r"^every:(\d{1,6})([smh])$")
MAX_ROW_ERRORS = 20


class MappingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_type: str
    table: str | None = None
    source_query: str | None = None
    # 원천 열 → 속성 api_name
    column_map: dict[str, str]
    pk_column: str
    mode: Literal["incremental", "full"] = "full"
    watermark_column: str | None = None
    # 이 매핑이 만드는 링크 타입(FK 속성 → Link Type). 링크의 출발 타입이 이 매핑의 타입이어야 한다.
    link_rules: list[str] = Field(default_factory=list)
    schedule: str | None = None


def schedule_seconds(schedule: str | None) -> int | None:
    """``every:5m`` → 300. 비면 None(수동 실행만)."""
    if not schedule:
        return None
    m = SCHEDULE.match(schedule)
    if not m:
        raise ValueError("schedule 은 'every:<숫자><s|m|h>' 형식이어야 합니다(예: every:15m)")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def check(
    spec: MappingSpec, doc: OntologyDocument, source_columns: list[str] | None = None
) -> list[str]:
    errors: list[str] = []
    t = doc.object_type(spec.object_type)
    if t is None:
        return [f"게시 버전에 객체 타입 '{spec.object_type}' 이 없습니다"]
    if bool(spec.table) == bool(spec.source_query):
        errors.append("표(table)와 질의(source_query) 중 하나만 지정해야 합니다")
    if spec.table and not TABLE.match(spec.table):
        errors.append(f"표 이름 형식이 아닙니다: {spec.table}")
    for col in [
        *spec.column_map,
        spec.pk_column,
        *([spec.watermark_column] if spec.watermark_column else []),
    ]:
        if not IDENT.match(col):
            errors.append(f"열 이름 형식이 아닙니다: {col}")
    props = {p.api_name for p in t.properties}
    unknown = sorted(set(spec.column_map.values()) - props)
    if unknown:
        errors.append(f"'{t.api_name}' 에 없는 속성: {', '.join(unknown)}")
    targets = list(spec.column_map.values())
    dup = sorted({p for p in targets if targets.count(p) > 1})
    if dup:
        errors.append(f"한 속성에 원천 열이 둘 이상 매핑됐습니다: {', '.join(dup)}")
    if spec.column_map.get(spec.pk_column) != t.pk_property:
        errors.append(f"pk 열 '{spec.pk_column}' 은 pk 속성 '{t.pk_property}' 에 매핑돼야 합니다")
    required = sorted(
        p.api_name for p in t.properties if not p.nullable and p.api_name not in targets
    )
    if required:
        errors.append(f"빈 값을 허용하지 않는 속성이 매핑되지 않았습니다: {', '.join(required)}")
    if spec.mode == "incremental" and not spec.watermark_column:
        errors.append("증분 모드는 watermark_column 이 필요합니다")
    for name in spec.link_rules:
        link = next((lt for lt in doc.link_types if lt.api_name == name), None)
        if link is None:
            errors.append(f"링크 타입 '{name}' 이 없습니다")
        elif link.from_type != t.api_name:
            errors.append(
                f"링크 '{name}' 의 출발 타입은 '{link.from_type}' 입니다 — 이 매핑은 '{t.api_name}'"
            )
        elif link.source.kind != "fk_property" or link.source.property not in targets:
            errors.append(
                f"링크 '{name}' 의 FK 속성 '{link.source.property}' 이 매핑되지 않았습니다"
            )
    try:
        schedule_seconds(spec.schedule)
    except ValueError as e:
        errors.append(str(e))
    if source_columns is not None:
        wanted = {*spec.column_map, spec.pk_column}
        if spec.watermark_column:
            wanted.add(spec.watermark_column)
        missing = sorted(wanted - set(source_columns))
        if missing:
            errors.append(f"원천에 없는 열: {', '.join(missing)}")
    return errors


def to_value(prop: PropertyDef, raw: Any) -> Any:
    """원천 값 → jsonb 에 넣을 값. 숫자는 정밀도를 지키려 decimal 을 문자열로 둔다."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        if not prop.nullable:
            raise ValueError("빈 값을 허용하지 않습니다")
        return None
    t = prop.data_type
    if t == "string":
        return str(raw)
    if t == "enum":
        if str(raw) not in (prop.enum_values or []):
            raise ValueError(f"enum 값이 아닙니다: {raw!r}")
        return str(raw)
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).lower() in {"true", "t", "1", "y", "yes"}:
            return True
        if str(raw).lower() in {"false", "f", "0", "n", "no"}:
            return False
        raise ValueError(f"참/거짓이 아닙니다: {raw!r}")
    if t in {"int", "decimal"}:
        try:
            d = Decimal(str(raw))
        except InvalidOperation as e:
            raise ValueError(f"숫자가 아닙니다: {raw!r}") from e
        if t == "int":
            if d != d.to_integral_value():
                raise ValueError(f"정수가 아닙니다: {raw!r}")
            return int(d)
        return str(d.normalize()) if d != d.to_integral_value() else int(d)
    if t == "date":
        if isinstance(raw, datetime):
            return raw.date().isoformat()
        if isinstance(raw, date):
            return raw.isoformat()
        return date.fromisoformat(str(raw)).isoformat()
    if t == "datetime":
        if isinstance(raw, datetime):
            return raw.isoformat()
        return datetime.fromisoformat(str(raw)).isoformat()
    raise ValueError(f"알 수 없는 자료형 {t}")  # pragma: no cover


class RowErrors(Exception):
    def __init__(self, errors: list[str], total: int) -> None:
        more = f" 외 {total - len(errors)}건" if total > len(errors) else ""
        super().__init__("; ".join(errors) + more)
        self.errors = errors
        self.total = total


def convert(
    spec: MappingSpec, t: ObjectTypeDef, rows: list[Row]
) -> list[tuple[str, dict[str, Any]]]:
    """원천 행 → (pk, 속성). 실패한 행이 하나라도 있으면 RowErrors(모아서 한 번에)."""
    out: list[tuple[str, dict[str, Any]]] = []
    errors: list[str] = []
    total = 0
    props = {p.api_name: p for p in t.properties}
    for row in rows:
        values: dict[str, Any] = {}
        bad: list[str] = []
        for col, prop in spec.column_map.items():
            try:
                values[prop] = to_value(props[prop], row.get(col))
            except ValueError as e:
                bad.append(f"{col}→{prop}: {e}")
        if bad:
            total += 1
            if len(errors) < MAX_ROW_ERRORS:
                errors.append(f"{spec.pk_column}={row.get(spec.pk_column)!r} ({'; '.join(bad)})")
            continue
        pk = values[t.pk_property]
        out.append((str(pk), values))
    seen: set[str] = set()
    for pk, _ in out:
        if pk in seen:
            total += 1
            errors.append(f"pk 가 중복됩니다: {pk!r}")
        seen.add(pk)
    if total:
        raise RowErrors(errors, total)
    return out
