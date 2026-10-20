"""동기화 실행(TRD 11절) — 원천 → 스테이징 → **한 트랜잭션 병합** → 그래프 아웃박스.

한 실행의 흐름:

1. ``sync_run(running)`` 을 따로 커밋한다 — 병합이 실패해도 실행 기록과 실패 사유가 남는다.
2. 원천에서 행을 묶음으로 읽어 속성 자료형으로 바꾼다. 한 행이라도 못 바꾸면 여기서 실패한다.
3. 병합 트랜잭션: 임시 스테이징 표 → 바뀐 행 판별(row_hash) → 객체 upsert → (전체 모드) 삭제 표시
   → 링크 갱신 → 같은 트랜잭션에서 ``graph_outbox`` 이벤트 → ``sync_run(succeeded)``.
   여기서 실패하면 전부 롤백되고 기존 데이터는 그대로다.

같은 매핑의 병합은 advisory lock 으로 줄을 세운다. 병합은 멱등이라(같은 행 → 같은 결과) 겹쳐 읽은
행을 두 번 병합해도 결과가 같다.

편집 레이어(TRD 6.3·11절): 값이 바뀐 객체마다 같은 트랜잭션에서 ``on_source_change`` 를 부른다.
병합은 ``object_edit`` 를 건드리지 않고(편집 레이어 우선), 편집된 속성의 새 원천 값이 편집 당시
값과 다르면 ``objects.edits`` 가 ``sync_conflict`` 를 남긴다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.sync import DataSource, Mapping, SyncRun
from ontobot.db.session import session_scope
from ontobot.graph import outbox
from ontobot.objects import edits, store
from ontobot.ontology import registry
from ontobot.ontology.models import ObjectTypeDef, OntologyDocument
from ontobot.security.secrets import SecretRefError, resolve_db
from ontobot.sync.connectors.base import ConnectorError, SourceSpec
from ontobot.sync.connectors.postgres import connector_for
from ontobot.sync.mapping import MappingSpec, RowErrors, check, convert

SourceChangeHook = Callable[[AsyncSession, str, list[str]], Awaitable[None]]


# 병합 트랜잭션 안에서 바뀐 객체의 편집을 대조한다. 테스트가 바꿔 끼울 수 있게 이름으로 둔다.
on_source_change: SourceChangeHook = edits.record_source_changes


# ---------------------------------------------------------------- 워터마크(자료형 보존)


def encode_watermark(value: Any) -> str | None:
    """원천 값의 자료형을 함께 남긴다 — 다음 실행이 같은 자료형으로 바인드해야 비교가 된다."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return f"datetime:{value.isoformat()}"
    if isinstance(value, date):
        return f"date:{value.isoformat()}"
    if isinstance(value, bool):
        raise ValueError("bool 열은 워터마크가 될 수 없습니다")
    if isinstance(value, int):
        return f"int:{value}"
    if isinstance(value, Decimal | float):
        return f"decimal:{value}"
    return f"str:{value}"


def decode_watermark(stored: str | None) -> Any:
    if not stored:
        return None
    kind, _, raw = stored.partition(":")
    parsers: dict[str, Callable[[str], Any]] = {
        "datetime": datetime.fromisoformat,
        "date": date.fromisoformat,
        "int": int,
        "decimal": Decimal,
        "str": str,
    }
    return parsers[kind](raw)


# ---------------------------------------------------------------- 실행


@dataclass
class RunOutcome:
    run_id: int
    status: str
    rows_read: int = 0
    rows_upserted: int = 0
    rows_deleted: int = 0
    error: str | None = None


class SyncFailed(Exception):
    pass


def _title(t: ObjectTypeDef, props: dict[str, Any]) -> str | None:
    """그래프 노드 제목. 제목 속성이 PII 면 싣지 않는다 — Neo4j 에는 PII 를 두지 않는다(TRD 5.1)."""
    p = t.property(t.title_property)
    if p is None or p.pii_kind is not None:
        return None
    value = props.get(t.title_property)
    return None if value is None else str(value)


async def _start(mapping_id: int) -> tuple[int, Mapping, DataSource, str | None]:
    async with session_scope() as db:
        mapping = await db.get(Mapping, mapping_id)
        if mapping is None:
            raise SyncFailed(f"매핑 {mapping_id} 이 없습니다")
        source = await db.get(DataSource, mapping.source_id)
        if source is None:
            raise SyncFailed(f"데이터 소스 {mapping.source_id} 가 없습니다")
        last = await db.scalar(
            select(SyncRun.watermark)
            .where(SyncRun.mapping_id == mapping_id, SyncRun.status == "succeeded")
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
        run = SyncRun(mapping_id=mapping_id, status="running", started_at=datetime.now(UTC))
        db.add(run)
        await db.commit()
        return run.id, mapping, source, last
    raise AssertionError("unreachable")


async def _finish_failed(run_id: int, error: str, rows_read: int) -> None:
    async with session_scope() as db:
        run = await db.get(SyncRun, run_id)
        if run is not None:
            run.status = "failed"
            run.error = error[:4000]
            run.rows_read = rows_read
            run.finished_at = datetime.now(UTC)
            await db.commit()


def _spec(m: Mapping) -> MappingSpec:
    return MappingSpec(
        object_type=m.object_type,
        table=m.table,
        source_query=m.source_query,
        column_map=m.column_map,
        pk_column=m.pk_column,
        mode=m.mode,  # type: ignore[arg-type]
        watermark_column=m.watermark_column,
        link_rules=[r["link_type"] for r in m.link_rules],
        schedule=m.schedule,
    )


async def run_mapping(mapping_id: int) -> RunOutcome:
    run_id, mapping, source, last_watermark = await _start(mapping_id)
    rows_read = 0
    try:
        schema = registry.current()
        if schema is None:
            async with session_scope() as db:
                schema = await registry.reload(db)
        if schema is None:
            raise SyncFailed("게시된 온톨로지가 없습니다")
        spec = _spec(mapping)
        errors = check(spec, schema.document)
        if errors:
            # 매핑 저장 뒤 새 버전이 게시돼 속성이 빠졌을 수 있다 — 실행 시점에 다시 본다.
            raise SyncFailed("매핑이 현재 게시 버전과 맞지 않습니다: " + "; ".join(errors))
        t = schema.document.object_type(spec.object_type)
        if t is None:  # check() 가 이미 막는다 — 타입 좁히기
            raise SyncFailed(f"타입 '{spec.object_type}' 이 없습니다")
        connector = connector_for(source.kind, resolve_db(source.secret_ref))
        since = decode_watermark(last_watermark) if spec.mode == "incremental" else None

        rows: list[dict[str, Any]] = []
        watermark = last_watermark
        async for batch in connector.rows(
            SourceSpec(
                table=spec.table, query=spec.source_query, watermark_column=spec.watermark_column
            ),
            since=since,
        ):
            rows.extend(batch)
            rows_read += len(batch)
            if spec.watermark_column:
                watermark = encode_watermark(batch[-1].get(spec.watermark_column)) or watermark
        converted = convert(spec, t, rows)

        async with session_scope() as db:
            outcome = await _merge(db, schema.document, t, spec, source.id, converted, run_id)
            run = await db.get(SyncRun, run_id)
            if run is None:
                raise SyncFailed(f"실행 기록 {run_id} 이 사라졌습니다")
            run.status = "succeeded"
            run.finished_at = datetime.now(UTC)
            run.rows_read = rows_read
            run.rows_upserted = outcome.rows_upserted
            run.rows_deleted = outcome.rows_deleted
            run.watermark = watermark
            await db.commit()
            return outcome
        raise AssertionError("unreachable")
    except (SyncFailed, RowErrors, ConnectorError, SecretRefError, ValueError) as e:
        await _finish_failed(run_id, f"{type(e).__name__}: {e}", rows_read)
        return RunOutcome(run_id, "failed", rows_read=rows_read, error=str(e))
    except Exception as e:  # 병합 중 DB 오류 등 — 롤백은 세션이 했다. 실패를 기록하고 다시 올린다.
        await _finish_failed(run_id, f"{type(e).__name__}: {e}", rows_read)
        raise


async def _merge(
    db: AsyncSession,
    doc: OntologyDocument,
    t: ObjectTypeDef,
    spec: MappingSpec,
    source_id: int,
    rows: list[tuple[str, dict[str, Any]]],
    run_id: int,
) -> RunOutcome:
    now = datetime.now(UTC)
    # 같은 타입의 병합은 줄을 세운다(매핑이 둘이어도). 트랜잭션이 끝나면 저절로 풀린다.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(CAST(:k AS text)))"),
        {"k": f"sync:{t.api_name}"},
    )
    await store.stage(db, [(pk, props, _title(t, props)) for pk, props in rows])
    changed = await store.changed_pks(db, t.api_name)
    await store.upsert_staged(db, t.api_name, source_id, now)
    if changed:
        titles = await store.staged_titles(db, changed)
        await outbox.enqueue(
            db, "node_upsert", ({"type": t.api_name, "pk": r.pk, "title": r.title} for r in titles)
        )
        await on_source_change(db, t.api_name, changed)

    deleted: list[str] = []
    if spec.mode == "full":
        deleted = await store.mark_missing_deleted(db, t.api_name, source_id, now)
        if deleted:
            gone = await store.delete_links_touching(
                db,
                [lt.api_name for lt in doc.link_types if lt.from_type == t.api_name],
                [lt.api_name for lt in doc.link_types if lt.to_type == t.api_name],
                deleted,
                now,
            )
            await outbox.enqueue(db, "link_delete", _link_payloads(doc, gone))
            await outbox.enqueue(
                db, "node_delete", ({"type": t.api_name, "pk": pk} for pk in deleted)
            )

    for name in spec.link_rules:
        link = next(lt for lt in doc.link_types if lt.api_name == name)
        if link.source.property is None:  # check() 가 fk_property 만 받는다
            raise SyncFailed(f"링크 '{name}' 에 FK 속성이 없습니다")
        stale, added = await store.sync_fk_links(db, name, link.source.property, source_id, now)
        await outbox.enqueue(db, "link_delete", _link_payloads(doc, stale))
        await outbox.enqueue(db, "link_upsert", _link_payloads(doc, added))

    return RunOutcome(
        run_id,
        "succeeded",
        rows_read=len(rows),
        rows_upserted=len(changed),
        rows_deleted=len(deleted),
    )


def _link_payloads(doc: OntologyDocument, rows: Any) -> list[dict[str, str]]:
    by_name = {lt.api_name: lt for lt in doc.link_types}
    return [
        {
            "link_type": r.link_type,
            "from_type": by_name[r.link_type].from_type,
            "from_pk": r.from_pk,
            "to_type": by_name[r.link_type].to_type,
            "to_pk": r.to_pk,
        }
        for r in sorted(rows, key=lambda r: (r.link_type, r.from_pk, r.to_pk))
        if r.link_type in by_name
    ]
