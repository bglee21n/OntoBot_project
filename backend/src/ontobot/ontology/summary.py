"""플래너용 스키마 요약 — 권한으로 거른다(TRD 9.1 권한 적용 지점 1).

읽을 수 없는 타입은 이름조차 싣지 않는다. 링크는 **양 끝 타입을 모두 읽을 수 있을 때만** 싣는다 —
한쪽이라도 읽을 수 없으면 그 링크로 탐색하는 계획은 검증기가 어차피 거부하고(5.4), 이름만으로도
"연결이 있다"는 정보가 새기 때문이다. 순수 함수 — 권한 집합은 ``security.rbac.readable_types``
결과를 호출부가 넘긴다.
"""

from __future__ import annotations

from collections.abc import Set
from typing import Any

from ontobot.ontology.models import OntologyDocument


def schema_summary(doc: OntologyDocument, readable: Set[str]) -> dict[str, Any]:
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
        for link in doc.link_types
        if link.from_type in readable and link.to_type in readable
    ]
    return {"object_types": types, "link_types": links}
