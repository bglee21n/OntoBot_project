"""매뉴얼 촬영 환경 — 화면 사진을 개발 데이터와 섞이지 않은 깨끗한 DB 에서 찍는다.

compose.test 의 PostgreSQL(55432)·Neo4j(57687) 컨테이너를 쓴다(``task.py test-up``). 그 안에
``ontobot_manual``(앱 DB)과 ``ontobot_manual_src``(데이터 소스 실습용 원천 DB)를 만든다.

    uv run --project backend python docs/manual/tools/manual_env.py prepare   # DB 를 비우고 다시 만든다
    uv run --project backend python docs/manual/tools/manual_env.py env       # 셸에 넣을 환경변수

계정 비밀번호는 환경변수 ``ONTOBOT_MANUAL_PASSWORD`` 로 받는다(저장소에 적지 않는다).
그래프 재구축이 테스트 Neo4j 를 덮으므로 통합 테스트·평가와 동시에 돌리지 않는다.
"""

from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
PG_CONTAINER = "ontobot-test-postgres-1"

ENV = {
    "ONTOBOT_DB_HOST": "127.0.0.1",
    "ONTOBOT_DB_PORT": "55432",
    "ONTOBOT_DB_USER": "ontobot",
    "ONTOBOT_DB_PASSWORD": "ontobot-test",
    "ONTOBOT_DB_NAME": "ontobot_manual",
    "ONTOBOT_NEO4J_URI": "bolt://127.0.0.1:57687",
    "ONTOBOT_NEO4J_USER": "neo4j",
    "ONTOBOT_NEO4J_PASSWORD": "ontobot-test-graph",
    # 데이터 소스 실습: 시크릿 참조 env:ONTOBOT_SRC_FAC
    "ONTOBOT_SRC_FAC_HOST": "127.0.0.1",
    "ONTOBOT_SRC_FAC_PORT": "55432",
    "ONTOBOT_SRC_FAC_DB": "ontobot_manual_src",
    "ONTOBOT_SRC_FAC_USER": "ontobot",
    "ONTOBOT_SRC_FAC_PASSWORD": "ontobot-test",
    # 메일·SMS 는 실제로 보내지 않고 logs/notify/*.jsonl 에 남기는 개발용 가짜 게이트웨이(22장 메일·SMS 알림)
    "ONTOBOT_NOTIFY_CONFIG_PATH": "config/notify.dev.yaml",
    "PYTHONUTF8": "1",
}

# 아이디, 표시 이름, 역할 — 매뉴얼 본문이 이 이름으로 설명한다.
USERS = [
    ("kim.user", "김사용", ["user"]),
    ("lee.modeler", "이모델", ["modeler", "user"]),
    ("park.admin", "박관리", ["admin", "user"]),
    ("choi.expert", "최현업", ["domain_expert"]),
    ("jung.builder", "정빌더", ["bot_builder", "user"]),
]
READ_ROLES = ["user", "modeler", "admin", "bot_builder"]
TYPES = ["Facility", "Organization", "Region"]


def child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    return env | ENV


def run(cmd: list[str], cwd: Path = ROOT, stdin: str | None = None, extra: dict[str, str] | None = None) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, env=child_env() | (extra or {}), input=stdin, text=True, check=True)


def psql(db: str, sql: str, user: str = "postgres") -> None:
    run(["docker", "exec", "-i", PG_CONTAINER, "psql", "-q", "-v", "ON_ERROR_STOP=1", "-U", user, "-d", db], stdin=sql)


def source_sql() -> str:
    """원천 DB 실습 표 — 샘플 팩 시드와 같은 값, 열 이름은 일부러 다르게(매핑 화면을 보이려고)."""
    seed = ROOT / "domain-packs" / "sample" / "seed"
    lines = [
        "CREATE TABLE fac_master (fac_id text primary key, fac_name text not null, fac_type text,"
        " risk int, cap_kw numeric, inst_date date, org_code text, region text,"
        " updated_at timestamptz not null default now());",
        "CREATE TABLE org_master (org_id text primary key, org_name text not null, manager text, phone text);",
    ]

    def q(v: str) -> str:
        return "'" + v.replace("'", "''") + "'"

    with (seed / "facility.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lines.append(
                "INSERT INTO fac_master VALUES ("
                + ", ".join(
                    [
                        q(r["facility_id"]),
                        q(r["name"]),
                        q(r["facility_type"]),
                        r["risk_grade"] or "null",
                        r["capacity_kw"] or "null",
                        q(r["installed_on"]) if r["installed_on"] else "null",
                        q(r["owner_org_id"]),
                        q(r["region_code"]),
                    ]
                )
                + ", now());"
            )
    with (seed / "organization.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lines.append(
                f"INSERT INTO org_master VALUES ({q(r['org_id'])}, {q(r['name'])}, "
                f"{q(r['manager_name'])}, {q(r['manager_phone'])});"
            )
    return "\n".join(lines)


def prepare() -> None:
    password = os.environ.get("ONTOBOT_MANUAL_PASSWORD")
    if not password or len(password) < 12:
        sys.exit("ONTOBOT_MANUAL_PASSWORD(12자 이상)를 환경변수로 주세요")
    uv = shutil.which("uv") or "uv"
    run([sys.executable, "scripts/task.py", "test-up"])
    for db in ("ontobot_manual", "ontobot_manual_src"):
        psql("postgres", f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE);')
        psql("postgres", f"CREATE DATABASE \"{db}\" OWNER ontobot ENCODING 'UTF8' TEMPLATE template0;")
        psql(db, "CREATE EXTENSION IF NOT EXISTS vector;")
    psql("ontobot_manual_src", source_sql(), user="ontobot")
    run([uv, "run", "alembic", "upgrade", "head"], cwd=BACKEND)
    run([uv, "run", "--project", "backend", "python", "scripts/seed_domain_pack.py", "--pack", "sample"])
    run([uv, "run", "--project", "backend", "python", "scripts/graph_rebuild.py"])
    cli = [uv, "run", "python", "-m", "ontobot.security.cli"]
    for username, display, roles in USERS:
        args = [*cli, "create-user", "--username", username, "--display-name", display]
        for r in roles:
            args += ["--role", r]
        run(args, cwd=BACKEND, extra={"ONTOBOT_NEW_PASSWORD": password})
    for role in READ_ROLES:
        for t in TYPES:
            run([*cli, "grant-type", "--role", role, "--type", t], cwd=BACKEND)
    print("매뉴얼 환경 준비 완료 — API·워커를 이 환경변수로 다시 띄우세요(env 명령).")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "env"
    if cmd == "prepare":
        prepare()
    elif cmd == "env":
        for k, v in ENV.items():
            print(f"export {k}={v}")
    else:
        sys.exit("사용법: manual_env.py prepare|env")


if __name__ == "__main__":
    main()
