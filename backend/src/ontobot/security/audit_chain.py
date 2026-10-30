"""감사 로그 해시 체인 검증(PRD 5.5, TRD 17.1-10) — 끊긴 곳을 **보고만** 한다(추가 전용이라 고치지
않는다).

해시는 DB 트리거(``audit_log_chain``, 마이그레이션 0014)가 매긴다. 검증은 같은 해시
함수(``audit_log_hash``)로
행마다 다시 계산하고, 순번 순서로 직전 해시와 이어지는지, 순번에 빈칸이 없는지, 체인 머리(마지막
순번·해시)가
마지막 행과 같은지 본다 — 내용 변경·중간 삭제·꼬리 삭제·트리거를 끄고 넣은 행을 잡는다. DB
소유자가 함수까지
바꿔 다시 쓰는 공격은 막지 못한다(외부 보관 사본과 대조는 백업이 맡는다).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_CHECK = text(
    """
    WITH r AS (
        SELECT seq, id, hash, prev_hash,
               audit_log_hash(prev_hash, seq, ts, actor_id, event, target, payload, note) AS expect,
               lag(hash) OVER (ORDER BY seq) AS prev_actual,
               lag(seq) OVER (ORDER BY seq) AS prev_seq,
               row_number() OVER (ORDER BY seq) AS n
        FROM audit_log WHERE seq IS NOT NULL
    )
    SELECT seq, id,
           CASE WHEN hash IS DISTINCT FROM expect THEN 'hash_mismatch'
                WHEN n = 1 AND (seq <> 1 OR prev_hash IS NOT NULL) THEN 'head_missing'
                WHEN n > 1 AND prev_hash IS DISTINCT FROM prev_actual THEN 'link_broken'
                WHEN n > 1 AND seq <> prev_seq + 1 THEN 'gap' END AS reason
    FROM r
    WHERE hash IS DISTINCT FROM expect
       OR (n = 1 AND (seq <> 1 OR prev_hash IS NOT NULL))
       OR (n > 1 AND (prev_hash IS DISTINCT FROM prev_actual OR seq <> prev_seq + 1))
    ORDER BY seq LIMIT 1
    """
)


@dataclass(frozen=True)
class ChainReport:
    ok: bool
    rows: int
    last_seq: int
    # 처음 끊긴 곳(없으면 None). reason: hash_mismatch / link_broken / gap / head_missing /
    # unchained(트리거 없이 들어간 행) / tail_missing(머리와 마지막 행이 다름)
    broken_seq: int | None = None
    broken_id: int | None = None
    reason: str | None = None


async def verify(db: AsyncSession) -> ChainReport:
    rows, last_seq, last_hash = (
        await db.execute(
            text(
                "SELECT count(*), coalesce(max(seq), 0), "
                "(SELECT hash FROM audit_log WHERE seq = (SELECT max(seq) FROM audit_log)) "
                "FROM audit_log"
            )
        )
    ).one()
    unchained = await db.scalar(
        text("SELECT min(id) FROM audit_log WHERE seq IS NULL OR hash IS NULL")
    )
    if unchained is not None:
        return ChainReport(False, rows, last_seq, None, unchained, "unchained")
    bad = (await db.execute(_CHECK)).first()
    if bad is not None:
        return ChainReport(False, rows, last_seq, bad.seq, bad.id, bad.reason)
    head_seq, head_hash = (
        await db.execute(text("SELECT seq, hash FROM audit_chain_head WHERE id = 1"))
    ).one()
    if head_seq != last_seq or head_hash != last_hash:
        return ChainReport(False, rows, last_seq, last_seq + 1, None, "tail_missing")
    return ChainReport(True, rows, last_seq)
