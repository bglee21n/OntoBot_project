"""참조 값 실재 대조(TRD 7.1 3단계) — FK 속성 조건의 값이 대상 타입에 실제로 있는가.

스파이크에서 실측한 함정: "서울 지역 시설"을 묻자 플래너가 **코드 속성에 이름을** 넣었다
(``region_code = '서울'``). 계획은 형식상 멀쩡하고 실행도 성공해 0건이 나오는데, 사용자는
"서울에 시설이 없다"는 그럴듯한 답을 받는다. 조회 조건 문장이 해석을 보여 주긴 하지만, 그것만
믿고 둘 수 없다 — 값이 실재하지 않으면 계획 오류로 돌려 재계획시킨다.

검사 대상은 **링크의 FK 속성**(``source.kind = fk_property``)에 대한 ``eq``·``in`` 조건뿐이다.
그 속성은 정의상 다른 타입의 pk 이므로 "있어야 하는 값"이 분명하다. 다른 속성의 값은 없을 수도
있는 것이 정상이라(0건은 결과다) 검사하지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.objects.reader import existing_values, resolve
from ontobot.ontology.models import OntologyDocument
from ontobot.query.plan import MAX_TRAVERSE, Filter, QueryPlan, Traverse
from ontobot.query.validator import CheckedFilter, ValidatedPlan

# (속성 이름, 연산자, 값 목록) — 검증 전의 원 계획에서도 같은 대조를 하려고 모양을 낮춘다.
Cond = tuple[str, str, list[str]]


async def _check(
    db: AsyncSession, doc: OntologyDocument, type_name: str, conds: Sequence[Cond]
) -> list[str]:
    fk_links = {
        link.source.property: link
        for link in doc.link_types
        if link.from_type == type_name
        and link.source.kind == "fk_property"
        and link.source.property
    }
    errors: list[str] = []
    for prop, op, values in conds:
        link = fk_links.get(prop)
        if link is None or op not in {"eq", "in"} or not values:
            continue
        to_type = link.to_type
        found = await resolve(db, to_type, values, declared=())
        missing = [v for v in values if v not in found]
        if missing:
            target = doc.object_type(to_type)
            label = target.display_name if target else to_type
            name_prop = target.title_property if target else "name"
            # 고칠 계획을 그대로 보여 준다 — "traverse 를 쓰라"는 말만으로는 작은 모델이 같은 계획을
            # 다시 냈다(Phase 4 브라우저 확인에서 실측).
            example = json.dumps(
                {
                    "link": link.api_name,
                    "direction": "out",
                    "to": to_type,
                    "filters": [
                        {
                            "property": name_prop,
                            "op": op,
                            "value": values[0] if op == "eq" else values,
                        }
                    ],
                },
                ensure_ascii=False,
            )
            errors.append(
                f"'{prop}' 는 {to_type}({label})의 식별자(pk)입니다. "
                f"값 {missing} 에 해당하는 {label} 객체가 없습니다. 그 값이 이름이라면 이 조건을 "
                f"filters 에서 빼고 traverse 에 다음 항목을 넣으세요: {example}. "
                "그래도 표현할 수 없으면 조건을 빼지 말고 cannot_answer 를 내세요"
            )
    return errors


def _checked(filters: Sequence[CheckedFilter]) -> list[Cond]:
    return [
        (f.prop.api_name, f.op, [str(v) for v in (f.value if f.op == "in" else (f.value,))])
        for f in filters
    ]


async def check_references(db: AsyncSession, doc: OntologyDocument, vp: ValidatedPlan) -> list[str]:
    """대상 조건과 링크 끝 조건 모두를 본다."""
    errors = await _check(db, doc, vp.target.api_name, _checked(vp.filters))
    for hop in vp.hops:
        errors += await _check(db, doc, hop.to.api_name, _checked(hop.filters))
    return errors


async def check_raw_references(
    db: AsyncSession, doc: OntologyDocument, plan: QueryPlan
) -> list[str]:
    """검증에 실패한 계획에도 같은 대조를 한다 — 재계획은 한 번뿐이라, 오류를 한꺼번에 알려야
    고칠 기회가 있다(측정: 첫 계획의 오류 둘 중 하나만 알려 줘 두 번째 계획도 실패했다)."""

    def conds(filters: Sequence[Filter]) -> list[Cond]:
        out: list[Cond] = []
        for f in filters:
            raw = f.value if isinstance(f.value, list) else [f.value]
            out.append((f.property, f.op, [str(v) for v in raw if v is not None]))
        return out

    errors = await _check(db, doc, plan.target, conds(plan.filters))
    for tr in plan.traverse:
        errors += await _check(db, doc, tr.to, conds(tr.filters))
    return errors


async def rewrite_fk_names(
    db: AsyncSession, doc: OntologyDocument, plan: QueryPlan
) -> tuple[QueryPlan, list[str]]:
    """FK 속성에 **이름**을 넣은 조건을 같은 뜻의 ``traverse`` 로 옮긴다(결정론적, Phase 4).

    측정에서 작은 모델은 refers_to 표시와 재계획 오류 안내가 있어도 첫 계획에 거의 언제나
    ``owner_org_id = '강남지사'`` 를 냈고, 재계획에서는 traverse 를 덧붙이면서 잘못된 조건을 그대로
    남겼다. 재계획 한 번으로는 고쳐지지 않으므로 여기서 고친다. 조건은 빼지 않는다 —
    "FK 가 이름이 X 인 객체를 가리킨다" = "링크로 건너간 객체의 이름이 X 다"이고(FK 링크는 그
    속성에서 만들어진다), 바뀐 해석은 조회 조건 문장에 그대로 보인다.

    고치는 조건: 값이 하나도 그 타입의 pk 가 아니고, **모두** 그 타입 객체의 제목 값과 정확히
    같을 때만. 하나라도 어긋나면 손대지 않는다 — 참조 값 대조가 오류로 재계획시킨다.
    유사도는 쓰지 않는다.
    """
    links = {
        link.source.property: link
        for link in doc.link_types
        if link.from_type == plan.target
        and link.source.kind == "fk_property"
        and link.source.property
    }
    keep: list[Filter] = []
    traverse = list(plan.traverse)
    notes: list[str] = []
    for f in plan.filters:
        link = links.get(f.property)
        raw = f.value if isinstance(f.value, list) else [f.value]
        values = [str(v) for v in raw if v is not None]
        to = doc.object_type(link.to_type) if link else None
        if link is None or to is None or f.op not in {"eq", "in"} or not values:
            keep.append(f)
            continue
        if await resolve(db, to.api_name, values, declared=()):
            keep.append(f)  # pk 가 하나라도 있으면 코드로 쓴 것이다
            continue
        if await existing_values(db, to.api_name, to.title_property, values) != set(values):
            keep.append(f)
            continue
        hop = Traverse(
            link=link.api_name,
            direction="out",
            to=to.api_name,
            filters=[
                Filter(
                    property=to.title_property,
                    op="eq" if len(values) == 1 else "in",
                    value=values[0] if len(values) == 1 else list(values),
                )
            ],
        )
        if hop not in traverse:
            if len(traverse) >= MAX_TRAVERSE:
                keep.append(f)
                continue
            traverse.append(hop)
        notes.append(f"{f.property} {f.op} {values} → traverse {link.api_name}.{to.title_property}")
    if not notes:
        return plan, []
    return plan.model_copy(update={"filters": keep, "traverse": traverse}), notes
