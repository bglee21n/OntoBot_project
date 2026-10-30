"""audit log hash chain

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-31 12:00:00.000000

감사 로그 변조 방지(PRD 5.5, TRD 17.1-10). 넣을 때마다 DB 트리거가 순번(``seq``)과 해시를 매긴다.

- 해시 = SHA-256(직전 해시 | 순번 | 시각(UTC) | 행위자 | 이벤트 | 대상 | payload(jsonb) | 메모).
- 순서는 advisory lock + 체인 머리 표(``audit_chain_head``, 한 줄)로 정한다. id 는 트리거
  전에 정해져 트랜잭션 순서와 어긋날 수 있으므로 체인 순서는 ``seq`` 다.
- 앱 코드(``audit.record``)는 바뀌지 않는다. 트리거를 끄고 넣거나 고치면 검증이 잡는다.
- 기존 행은 id 순서로 체인을 채운다(추가 전용 트리거를 잠시 끄고).
asyncpg 는 한 문장에 명령 하나만 받는다 — 나눠서 실행한다.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

LOCK_KEY = 0x0B70_0A0D  # 감사 체인 줄 세우기(versioning 의 0x0B70_0001 과 다른 값)


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("seq", sa.BigInteger(), nullable=True))
    op.add_column("audit_log", sa.Column("hash", sa.String(length=64), nullable=True))
    op.create_index("uq_audit_log_seq", "audit_log", ["seq"], unique=True)
    op.create_table(
        "audit_chain_head",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=True),
        sa.CheckConstraint("id = 1", name=op.f("ck_audit_chain_head_single")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_chain_head")),
    )
    op.execute("INSERT INTO audit_chain_head (id, seq, hash) VALUES (1, 0, NULL)")
    op.execute(
        """
        CREATE FUNCTION audit_log_hash(
            prev text, seq bigint, ts timestamptz, actor bigint, event text, target text,
            payload jsonb, note text
        ) RETURNS text LANGUAGE sql IMMUTABLE AS $$
            SELECT encode(sha256(convert_to(concat_ws('|',
                coalesce(prev, ''),
                seq::text,
                to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
                coalesce(actor::text, ''),
                event,
                coalesce(target, ''),
                payload::text,
                coalesce(note, '')
            ), 'UTF8')), 'hex')
        $$
        """
    )
    op.execute(
        """
        CREATE FUNCTION audit_log_chain() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            head_seq bigint;
            head_hash text;
        BEGIN
            PERFORM pg_advisory_xact_lock(LOCK_KEY_VALUE);
            SELECT seq, hash INTO head_seq, head_hash FROM audit_chain_head WHERE id = 1 FOR UPDATE;
            NEW.ts := coalesce(NEW.ts, now());
            NEW.payload := coalesce(NEW.payload, '{}'::jsonb);
            NEW.seq := head_seq + 1;
            NEW.prev_hash := head_hash;
            NEW.hash := audit_log_hash(NEW.prev_hash, NEW.seq, NEW.ts, NEW.actor_id, NEW.event,
                                       NEW.target, NEW.payload, NEW.note);
            UPDATE audit_chain_head SET seq = NEW.seq, hash = NEW.hash WHERE id = 1;
            RETURN NEW;
        END;
        $$
        """.replace("LOCK_KEY_VALUE", str(LOCK_KEY))
    )
    op.execute(
        "CREATE TRIGGER audit_log_chain BEFORE INSERT ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_chain()"
    )
    # 기존 행 — id 순서로 체인을 채운다. 추가 전용 트리거를 잠시 끈다(이 트랜잭션 안에서만).
    op.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete")
    op.execute(
        """
        DO $$
        DECLARE
            r record;
            s bigint := 0;
            h text := NULL;
        BEGIN
            FOR r IN SELECT * FROM audit_log ORDER BY id LOOP
                s := s + 1;
                UPDATE audit_log SET seq = s, prev_hash = h,
                    hash = audit_log_hash(h, s, r.ts, r.actor_id, r.event, r.target,
                                          r.payload, r.note)
                WHERE id = r.id
                RETURNING hash INTO h;
            END LOOP;
            UPDATE audit_chain_head SET seq = s, hash = h WHERE id = 1;
        END;
        $$
        """
    )
    op.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_chain ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_chain()")
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "audit_log_hash(text, bigint, timestamptz, bigint, text, text, jsonb, text)"
    )
    op.drop_table("audit_chain_head")
    op.drop_index("uq_audit_log_seq", table_name="audit_log")
    op.drop_column("audit_log", "hash")
    op.drop_column("audit_log", "seq")
