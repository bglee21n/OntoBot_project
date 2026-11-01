"""36.15 자산화 내보내기(TRD 17.17 결정 A10, Phase 22) — 도메인 팩 모양의 zip 하나.

담는 것: 게시본 설계도(``ontology.yaml``, ``importer.dump``), 도입 골든셋의 **확정한** 문항
(``golden.yaml``, ``evaluation.golden.Pack``), 양식(``adoption/<kind>.json``), 판단 기록과 목록
(``adoption/engagement.json``·``README.md``). **객체 행은 담지 않는다** — 폐쇄망 밖으로 가져갈 수
있는 것은 설계도·합성 데이터·양식뿐이다(매뉴얼 36.15). 양식의 사람 정보 칸(``pii``)은 비운다.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.adoption import pilot, sheets, store
from ontobot.adoption.send import golden_set_name
from ontobot.db.models.evaluation import GoldenItem, GoldenSet
from ontobot.ontology import importer, versioning
from ontobot.security import audit


def strip_pii(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """선언에서 ``pii`` 인 칸의 값을 뺀다. 순수 함수."""
    sheet = sheets.BY_KIND[kind]
    out: dict[str, Any] = {}
    for key, value in payload.items():
        sec = sheet.section(key)
        hidden = {c.key for c in sec.inputs if c.pii} if sec else set()
        if isinstance(value, dict):
            out[key] = {k: v for k, v in value.items() if k not in hidden}
        elif isinstance(value, list):
            out[key] = [
                {k: v for k, v in r.items() if k not in hidden} if isinstance(r, dict) else r
                for r in value
            ]
        else:
            out[key] = value
    return out


def confirmed(item: GoldenItem) -> bool:
    """기대 값을 확정한 문항 — 기대 값·실패 사유가 있거나, 답할 수 없다고 정한 문항."""
    return bool(
        item.required_values
        or item.absent_values
        or item.expected_failure
        or not item.answerable
        or item.expected_status != "ok"
    )


def golden_pack(name: str, items: list[GoldenItem]) -> dict[str, Any]:
    """``golden.yaml`` 모양(``evaluation.golden.Pack``). 기본값인 필드는 적지 않는다."""
    out = []
    for it in items:
        row: dict[str, Any] = {
            "id": it.key or f"a{it.id}",
            "question": it.question,
            "expected_status": it.expected_status,
        }
        for field, value, default in (
            ("expected_failure", it.expected_failure, None),
            ("expected_grade", it.expected_grade, None),
            ("answerable", it.answerable, True),
            ("required_values", it.required_values, []),
            ("absent_values", it.absent_values, []),
            ("required_conditions", it.required_conditions, []),
            ("forbidden_values", it.forbidden_values, []),
            ("note", it.note, None),
        ):
            if value != default:
                row[field] = value
        out.append(row)
    return {"name": name, "items": out}


@dataclass(frozen=True)
class Exported:
    filename: str
    data: bytes
    golden_items: int
    sheets: int


async def build(db: AsyncSession, engagement_id: int, *, actor_id: int) -> Exported:
    e = await store.get(db, engagement_id)
    published = await versioning.published_version_id(db)
    files: dict[str, str] = {}
    type_count = link_count = 0
    if published is not None:
        doc = versioning.to_document(await versioning.get_version(db, published))
        files["ontology.yaml"] = importer.dump(doc, "yaml")
        type_count, link_count = len(doc.object_types), len(doc.link_types)

    gs = await db.scalar(select(GoldenSet).where(GoldenSet.name == golden_set_name(engagement_id)))
    items = []
    if gs is not None:
        rows = await db.scalars(
            select(GoldenItem).where(GoldenItem.golden_set_id == gs.id).order_by(GoldenItem.id)
        )
        items = [it for it in rows if confirmed(it)]
    if items:
        pack = golden_pack(f"adoption-{engagement_id}", items)
        files["golden.yaml"] = yaml.safe_dump(pack, allow_unicode=True, sort_keys=False)

    states = await store.sheet_states(db, engagement_id)
    for s in states:
        files[f"adoption/{s.kind}.json"] = json.dumps(
            {"kind": s.kind, "revision": s.revision, "payload": strip_pii(s.kind, s.payload)},
            ensure_ascii=False,
            indent=2,
        )
    files["adoption/engagement.json"] = json.dumps(
        {
            "name": e.name,
            "description": e.description,
            "ontology_version_id": published,
            "decisions": [
                {
                    "decision": d.decision,
                    "reason": d.reason,
                    "eval_run_id": d.eval_run_id,
                    "gate_passed": d.gate_passed,
                    "decided_at": d.created_at.isoformat(),
                }
                for d in await pilot.decisions(db, engagement_id)
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    now = datetime.now(UTC)
    files["README.md"] = "\n".join(
        [
            f"# 도입 자산 — {e.name}",
            "",
            f"- 만든 때: {now.isoformat(timespec='seconds')}",
            f"- 설계도: 게시 버전 {published} (객체 타입 {type_count}, 링크 타입 {link_count})"
            if published
            else "- 설계도: 게시본 없음",
            f"- 골든 문항(확정한 것): {len(items)}",
            f"- 양식: {len(states)}종(사람 정보 칸은 비움)",
            "- 객체 데이터는 담지 않았습니다. 합성 예시 데이터는 따로 만들어 seed/ 에 넣으세요.",
            "",
        ]
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    audit.record(
        db,
        audit.ENGAGEMENT_EXPORTED,
        actor_id=actor_id,
        target=f"engagement:{engagement_id}",
        payload={
            "ontology_version_id": published,
            "golden_items": len(items),
            "sheets": len(states),
        },
    )
    await db.flush()
    stamp = now.strftime("%Y%m%d")
    return Exported(
        f"adoption-{engagement_id}-{stamp}.zip", buf.getvalue(), len(items), len(states)
    )
