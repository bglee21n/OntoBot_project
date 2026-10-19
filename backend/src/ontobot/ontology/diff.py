"""버전 비교(TRD 6.1) — 두 정의 문서의 차이를 요소 단위로 낸다. 순수 함수."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ontobot.ontology.models import OntologyDocument

ChangeKind = Literal["added", "removed", "changed"]


@dataclass(frozen=True)
class Change:
    kind: ChangeKind
    element: Literal["object_type", "property", "link_type"]
    path: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None


def diff(base: OntologyDocument, target: OntologyDocument) -> list[Change]:
    changes: list[Change] = []
    b_types = {t.api_name: t for t in base.object_types}
    t_types = {t.api_name: t for t in target.object_types}

    for name in sorted(b_types.keys() | t_types.keys()):
        bt, tt = b_types.get(name), t_types.get(name)
        if bt is None or tt is None:
            changes.append(
                Change(
                    "added" if bt is None else "removed",
                    "object_type",
                    name,
                    bt.model_dump(mode="json") if bt else None,
                    tt.model_dump(mode="json") if tt else None,
                )
            )
            continue
        b_head = bt.model_dump(mode="json", exclude={"properties"})
        t_head = tt.model_dump(mode="json", exclude={"properties"})
        if b_head != t_head:
            changes.append(Change("changed", "object_type", name, b_head, t_head))
        b_props = {p.api_name: p.model_dump(mode="json") for p in bt.properties}
        t_props = {p.api_name: p.model_dump(mode="json") for p in tt.properties}
        for prop in sorted(b_props.keys() | t_props.keys()):
            bp, tp = b_props.get(prop), t_props.get(prop)
            if bp == tp:
                continue
            kind: ChangeKind = "added" if bp is None else "removed" if tp is None else "changed"
            changes.append(Change(kind, "property", f"{name}.{prop}", bp, tp))

    b_links = {lt.api_name: lt.model_dump(mode="json") for lt in base.link_types}
    t_links = {lt.api_name: lt.model_dump(mode="json") for lt in target.link_types}
    for name in sorted(b_links.keys() | t_links.keys()):
        bl, tl = b_links.get(name), t_links.get(name)
        if bl == tl:
            continue
        kind = "added" if bl is None else "removed" if tl is None else "changed"
        changes.append(Change(kind, "link_type", name, bl, tl))
    return changes
