"""플래너용 스키마 요약 — 권한으로 거른다(TRD 9.1 권한 적용 지점 1).

읽을 수 없는 타입은 이름조차 싣지 않는다. 링크는 **양 끝 타입을 모두 읽을 수 있을 때만** 싣는다 —
한쪽이라도 읽을 수 없으면 그 링크로 탐색하는 계획은 검증기가 어차피 거부하고(5.4), 이름만으로도
"연결이 있다"는 정보가 새기 때문이다. 순수 함수 — 권한 집합은 ``security.rbac.readable_types``
결과를 호출부가 넘긴다.

Action 은 호출부가 넘긴 **요청할 수 있는** 것(``actions``) 중 대상 타입을 읽을 수 있는 것만 싣는다.
하나도 없으면 ``action_types`` 키 자체를 싣지 않는다 — 플래너 프롬프트가 Action 이 없는 배포에서
측정한 그대로 남는다(ADR-0004).
"""

from __future__ import annotations

from collections.abc import Set
from typing import Any

from ontobot.ontology.models import OntologyDocument


def schema_summary(
    doc: OntologyDocument, readable: Set[str], actions: Set[str] = frozenset()
) -> dict[str, Any]:
    visible_links = [
        link for link in doc.link_types if link.from_type in readable and link.to_type in readable
    ]
    # FK 속성에는 "어느 링크의 식별자인가"를 적는다. 없으면 모델이 이름을 코드 자리에 넣는다
    # ("강남지사 소유 시설" → owner_org_id = '강남지사', Phase 4 브라우저 확인에서 실측).
    fk_note = {
        (link.from_type, link.source.property): (
            f"{link.to_type} 의 식별자(pk). 이름으로 찾으려면 traverse 링크 '{link.api_name}'"
        )
        for link in visible_links
        if link.source.kind == "fk_property" and link.source.property
    }
    types = [
        {
            "name": t.api_name,
            "display_name": t.display_name,
            "description": t.description,
            "pk": t.pk_property,
            "title": t.title_property,
            "properties": [
                {
                    "name": p.api_name,
                    "display_name": p.display_name,
                    "type": p.data_type,
                    **({"enum": p.enum_values} if p.enum_values else {}),
                    **(
                        {"refers_to": fk_note[(t.api_name, p.api_name)]}
                        if (t.api_name, p.api_name) in fk_note
                        else {}
                    ),
                }
                for p in t.properties
            ],
        }
        for t in doc.object_types
        if t.api_name in readable
    ]
    links = [
        {
            "name": link.api_name,
            "display_name": link.display_name,
            "from": link.from_type,
            "to": link.to_type,
            "cardinality": link.cardinality,
        }
        for link in visible_links
    ]
    out: dict[str, Any] = {"object_types": types, "link_types": links}
    action_types = [
        {
            "name": a.api_name,
            "display_name": a.display_name,
            "description": a.description,
            "target": a.target_type,
            "params": [
                {
                    "name": p.api_name,
                    "display_name": p.display_name,
                    "type": p.data_type,
                    **({"enum": p.enum_values} if p.enum_values else {}),
                    "required": p.required,
                }
                for p in a.parameters
            ],
        }
        for a in doc.action_types
        if a.api_name in actions and a.target_type in readable
    ]
    if action_types:
        out["action_types"] = action_types
    return out
