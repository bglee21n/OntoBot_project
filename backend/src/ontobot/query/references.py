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

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.objects.reader import resolve
from ontobot.ontology.models import OntologyDocument
from ontobot.query.validator import ValidatedPlan


async def check_references(db: AsyncSession, doc: OntologyDocument, vp: ValidatedPlan) -> list[str]:
    fk_targets = {
        link.source.property: link.to_type
        for link in doc.link_types
        if link.from_type == vp.target.api_name
        and link.source.kind == "fk_property"
        and link.source.property
    }
    errors: list[str] = []
    for f in vp.filters:
        to_type = fk_targets.get(f.prop.api_name)
        if to_type is None or f.op not in {"eq", "in"}:
            continue
        values = [str(v) for v in (f.value if f.op == "in" else (f.value,))]
        found = await resolve(db, to_type, values, declared=())
        missing = [v for v in values if v not in found]
        if missing:
            target = doc.object_type(to_type)
            label = target.display_name if target else to_type
            errors.append(
                f"'{f.prop.api_name}' 는 {to_type}({label})의 식별자(pk)입니다. "
                f"값 {missing} 에 해당하는 {label} 객체가 없습니다. "
                "이름이 아니라 식별자를 써야 합니다. 식별자를 알 수 없으면 조건을 빼지 말고 "
                "cannot_answer 를 내세요"
            )
    return errors
