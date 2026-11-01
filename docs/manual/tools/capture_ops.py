"""운영자 매뉴얼 촬영 — 설치본의 웹 화면(:8000)·배포 콘솔(:8100)과 서버 명령 출력을 실제로 찍는다.

촬영 대상은 이 PC 의 시험 설치본(Docker Desktop VM 의 ontobot-trial, ``ontobotctl install`` 로 설치)이다.
장마다 앞 장이 남긴 상태(만든 계정·데이터 소스·업그레이드)에 기대므로 처음부터 순서대로 돈다. 촬영 전용
관리자 ``manual.capture`` 의 비밀번호는 루트 .env 의 ONTOBOT_MANUAL_PASSWORD 이고, 촬영이 만드는 계정도 같은
비밀번호를 쓴다(저장소에 적지 않는다).

    uv run --project backend --with playwright python docs/manual/tools/capture_ops.py [장 ...]

사진은 docs/manual/ops/images/<장>/<이름>.png — 본문(docs/manual/ops/*.md)이 이 경로를 쓴다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.environ.setdefault("ONTOBOT_MANUAL_URL", "http://localhost:8000")


def env_value(key: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"{key} 가 .env 에 없습니다")


os.environ.setdefault("ONTOBOT_MANUAL_PASSWORD", env_value("ONTOBOT_MANUAL_PASSWORD"))

from playwright.sync_api import Browser, Locator, Page, sync_playwright  # noqa: E402
from shots import BASE, Shooter, ask, go, last_answer, password, shot_turn  # noqa: E402
from term import Term, ctl, sh  # noqa: E402

IMAGES = ROOT / "docs" / "manual" / "ops" / "images"
DEPLOY = os.environ.get("ONTOBOT_DEPLOY_URL", "http://localhost:8100")
ADMIN = "manual.capture"
BUNDLE_M1 = "ontobot-0.1.0-2098e81ffd-m1"
BUNDLE_M2 = "ontobot-0.1.0-2098e81ffd-m2"
SHOWN = 'wsl -d ontobot -u root -- bash -c "cd /opt/ontobot && {}"'


def wsl(cmd: str) -> str:
    """본문에 적는 운영 서버 명령(관리자 PowerShell)."""
    return SHOWN.format(cmd)


class Ctx:
    def __init__(self, browser: Browser, term_page: Page, chapter: str) -> None:
        self.browser = browser
        self.s = Shooter(browser, chapter, root=IMAGES)
        self.t = Term(term_page, IMAGES, chapter)

    @contextmanager
    def app(self, username: str | None = ADMIN, **kw: object) -> Iterator[Page]:
        with self.s.as_user(username, **kw) as pg:  # type: ignore[arg-type]
            yield pg

    @contextmanager
    def console(self, username: str | None = ADMIN) -> Iterator[Page]:
        with self.s.as_user(None) as pg:
            pg.goto(DEPLOY, wait_until="networkidle")
            if username:
                console_login(pg, username)
            yield pg


def console_login(pg: Page, username: str, pw: str | None = None) -> None:
    pg.get_by_label("아이디").fill(username)
    pg.get_by_label("비밀번호").fill(pw or password())
    pg.get_by_role("button", name="로그인").click()
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(1500)


def console_tab(pg: Page, label: str) -> None:
    pg.locator("header nav").get_by_role("button", name=label, exact=True).click()
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(1200)


def nav(pg: Page, label: str) -> None:
    pg.locator("header.topbar").get_by_role("link", name=label, exact=True).click()
    pg.wait_for_load_state("networkidle")
    pg.wait_for_timeout(1200)


# ---------------------------------------------------------------- 2장 구성 — 서버 안 들여다보기


def ch_structure(c: Ctx) -> None:
    t = c.t
    t.shot("ctl-status", "wsl -d ontobot -u root -- ontobotctl status", ctl("status"))
    t.shot("compose-ps", wsl("docker compose ps"), sh("docker compose ps"))
    t.shot(
        "compose-ps-short",
        wsl("docker compose ps --format 'table {{.Service}}\\t{{.Status}}'"),
        sh("docker compose ps -a --format 'table {{.Service}}\t{{.Status}}'"),
    )
    t.shot("home-ls", "wsl -d ontobot -u root -- ls -la /opt/ontobot", sh("ls -la ."))
    t.shot("state-env", "wsl -d ontobot -u root -- cat /opt/ontobot/state.env", sh("cat state.env"))
    t.shot("config-ls", "wsl -d ontobot -u root -- ls -l /opt/ontobot/config", sh("ls -l config"))
    t.shot("releases-ls", "wsl -d ontobot -u root -- ls /opt/ontobot/releases /opt/ontobot/bundles", sh("ls releases bundles"))
    t.shot(
        "images",
        wsl("docker images"),
        sh("docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' | grep -E 'REPOSITORY|ontobot|neo4j|ollama|pgvector'"),
    )
    t.shot("volumes", wsl("docker volume ls"), sh("docker volume ls | grep -E 'DRIVER|ontobot'"))
    t.shot("df", "wsl -d ontobot -u root -- df -h /opt/ontobot", sh("df -h ."))


# ---------------------------------------------------------------- 4장 번들


def ch_bundle(c: Ctx) -> None:
    t = c.t
    b = f"bundles/{BUNDLE_M1}"
    t.shot("bundle-ls", f"wsl -d ontobot -u root -- ls -l /opt/ontobot/{b}", sh(f"ls -l {b}"))
    t.shot("bundle-images", f"wsl -d ontobot -u root -- ls -lh /opt/ontobot/{b}/images", sh(f"ls -lh {b}/images"))
    t.shot("bundle-env", f"wsl -d ontobot -u root -- cat /opt/ontobot/{b}/bundle.env", sh(f"cat {b}/bundle.env"))
    t.shot("bundle-manifest", f"wsl -d ontobot -u root -- cat /opt/ontobot/{b}/manifest.json", sh(f"cat {b}/manifest.json"), keep=(40, 0))
    t.shot("bundle-sums", f"wsl -d ontobot -u root -- head /opt/ontobot/{b}/SHA256SUMS", sh(f"head {b}/SHA256SUMS"))
    t.shot("trusted-keys", "wsl -d ontobot -u root -- ls -l /opt/ontobot/trusted-keys", sh("ls -l trusted-keys"))
    t.shot("verify", f"wsl -d ontobot -u root -- ontobotctl verify /opt/ontobot/{b}", sh(f"ontobotctl verify {b}"))
    t.shot(
        "preflight-same",
        f"wsl -d ontobot -u root -- ontobotctl preflight /opt/ontobot/{b}",
        sh(f"ontobotctl preflight {b}"),
        expect_fail=True,
    )


# ---------------------------------------------------------------- 5장 설치 확인


def ch_verify(c: Ctx) -> None:
    s = c.s
    with c.app(None, viewport={"width": 1000, "height": 260}) as pg:
        for name, url in [("healthz", BASE + "/healthz"), ("readyz", BASE + "/readyz"), ("deployer-healthz", DEPLOY + "/healthz")]:
            pg.goto(url)
            pg.wait_for_timeout(600)
            pretty = pg.get_by_text("예쁜 인쇄")
            if pretty.count():
                pretty.click()
            s.shot(pg, name)
    with c.app(None) as pg:
        go(pg, "/login")
        s.shot(pg, "web-login", marks=[("label:has-text('아이디') input", 1), ("input[type=password]", 2), ("button[type=submit]", 3)])
    with c.app() as pg:
        s.shot(pg, "after-login", marks=[("header .muted", 1, "below")])
        ask(pg, "위험등급이 3 이상인 시설은 몇 개야?")
        shot_turn(s, pg, "sample-answer")
        ask(pg, "강남지사가 소유한 시설 보여줘")
        shot_turn(s, pg, "sample-traverse")
    with c.console() as pg:
        s.shot(pg, "console-status")


# ---------------------------------------------------------------- 6장 웹 화면 둘러보기(관리자)

# v1 촬영 관리자(admin·user) 기준 위쪽 메뉴 — 인터뷰(domain_expert·modeler)·평가(bot_builder·modeler)는 없다.
NAV = ["채팅", "객체 탐색", "문서", "온톨로지", "데이터 소스", "Action", "트레이스", "관리"]


def ch_web(c: Ctx) -> None:
    s = c.s
    with c.app(None) as pg:
        go(pg, "/login")
        s.shot(pg, "login-empty")
        pg.get_by_label("아이디").fill(ADMIN)
        pg.get_by_label("비밀번호").fill("틀린비밀번호입니다")
        s.shot(pg, "login-filled", marks=[("button[type=submit]", None)])
        pg.get_by_role("button", name="로그인", exact=True).click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "login-wrong", marks=[(".error, [role=alert]", None)])
    with c.app() as pg:
        bar = pg.locator("header.topbar")
        marks = [(bar.get_by_role("link", name=n, exact=True), i + 1, "below") for i, n in enumerate(NAV)]
        n = len(NAV)
        marks += [(bar.locator(".muted"), n + 1, "below"), (bar.get_by_role("button", name="로그아웃"), n + 2, "below")]
        s.shot(pg, "topbar", marks=marks)
        s.shot(pg, "chat-page", marks=[(".chat-list", 1), (".chat-input", 2)])
        for n, name in [("온톨로지", "ontology-page"), ("데이터 소스", "sources-page"), ("Action", "actions-page")]:
            nav(pg, n)
            s.shot(pg, name, full=True)
        nav(pg, "관리")
        tabs = pg.locator("nav[role=tablist] button")
        # v1 관리 화면은 카드가 늘어 한 화면에 들지 않는다 — 페이지 전체를 찍는다.
        card = lambda text: pg.locator("main .card", has_text=text).first  # noqa: E731
        s.shot(
            pg,
            "admin-page",
            full=True,
            marks=[
                (pg.locator("main .card").first.locator("code"), 1, "below"),
                (pg.get_by_role("link", name="배포 콘솔 열기(업그레이드·롤백·백업)"), 2, "below"),
                (card("운영 지표"), 3),
                (card("성공 지표 목표"), 4),
                (card("객체 탐색 확장 한도"), 5),
                (pg.locator("nav[role=tablist]"), 6, "below"),
                (pg.locator("main table", has=pg.locator("th", has_text="데이터 그룹")).first, 7),
                (pg.locator("details.card summary", has_text="로컬 계정 추가"), 8),
            ],
        )
        bar.get_by_role("button", name="로그아웃").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "after-logout")


# ---------------------------------------------------------------- 7장 사용자 계정

USERS = [
    ("kim.user", "김사용", ["user"]),
    ("lee.modeler", "이모델", ["modeler", "user"]),
    ("park.admin", "박관리", ["admin", "user"]),
    ("choi.expert", "최현업", ["domain_expert"]),
    ("jung.builder", "정빌더", ["bot_builder"]),
]


def _add_user(pg: Page, username: str, display: str, roles: list[str], pw: str) -> Locator:
    box = pg.locator("details.card", has_text="로컬 계정 추가").first
    if box.get_attribute("open") is None:
        box.locator("summary").click()
        pg.wait_for_timeout(300)
    f = box.locator("form")
    f.get_by_label("아이디").fill(username)
    f.get_by_label("표시 이름").fill(display)
    f.get_by_label("초기 비밀번호(12자 이상)").fill(pw)
    for cb in f.locator("label.check").all():
        name = cb.inner_text().strip()
        inp = cb.locator("input")
        if (name in roles) != inp.is_checked():
            inp.click()
    return box


def _users_table(pg: Page) -> Locator:
    # v1 관리 화면은 사용자 표 앞에 성공 지표 목표 표가 있다 — "데이터 그룹" 칸이 있는 표가 사용자 표다.
    return pg.locator("main table", has=pg.locator("th", has_text="데이터 그룹")).first


def _user_row(pg: Page, username: str) -> Locator:
    return pg.locator("tbody tr", has=pg.locator(f"code:text-is('{username}')")).first


def ch_users(c: Ctx) -> None:
    s = c.s
    with c.app() as pg:
        nav(pg, "관리")
        table = _users_table(pg)
        heads = table.locator("thead th")
        s.shot(pg, "users-columns", clip=table, marks=[(heads.nth(i), i + 1, "below") for i in range(7)])
        box = pg.locator("details.card", has_text="로컬 계정 추가").first
        s.shot(pg, "add-closed", clip=box, marks=[(box.locator("summary"), 1)])
        box.locator("summary").click()
        pg.wait_for_timeout(400)
        f = box.locator("form")
        s.shot(
            pg,
            "add-open",
            clip=box,
            marks=[
                (f.get_by_label("아이디"), 1),
                (f.get_by_label("표시 이름"), 2),
                (f.get_by_label("초기 비밀번호(12자 이상)"), 3),
                (f.locator("div.row.span-all").nth(0), 4),
                (f.locator("div.row.span-all").nth(1), 5),  # v1 데이터 그룹
                (f.get_by_role("button", name="추가"), 6, "right"),
            ],
        )
        # 오류 두 가지 — 짧은 비밀번호, 이미 있는 아이디(사진만, 만들지 않는다)
        _add_user(pg, "kim.user", "김사용", ["user"], "short123")
        s.shot(pg, "add-short-filled", clip=box)
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "add-short-error", clip=box)
        # 첫 계정
        _add_user(pg, "kim.user", "김사용", ["user"], password())
        s.shot(pg, "add-kim-filled", clip=box, marks=[(f.get_by_role("button", name="추가"), 1, "right")])
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "add-kim-done", marks=[(_user_row(pg, "kim.user"), None)])
        _add_user(pg, "kim.user", "김사용", ["user"], password())
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "add-duplicate-error", clip=box)
        for username, display, roles in USERS[1:]:
            _add_user(pg, username, display, roles, password())
            f.get_by_role("button", name="추가").click()
            pg.wait_for_timeout(1500)
        go(pg, "/admin")
        s.shot(pg, "users-all", full=True)
        me = _user_row(pg, ADMIN)
        s.shot(pg, "self-row", clip=_users_table(pg), marks=[(me.locator("td").nth(5), 1)])
    # 첫 로그인 — 새 계정
    with c.app("kim.user") as pg:
        s.shot(pg, "kim-first-login", marks=[("header .muted", 1, "below")])
        go(pg, "/admin")
        s.shot(pg, "kim-admin-denied")
    # 역할 바꾸기 — 로그인해 있는 사람의 세션이 끊긴다
    with c.app("lee.modeler") as lee, c.app() as pg:
        nav(pg, "관리")
        row = _user_row(pg, "lee.modeler")
        row.locator("label.check", has_text="domain_expert").locator("input").click()
        pg.wait_for_timeout(300)
        s.shot(pg, "role-changed", clip=row, marks=[(row.get_by_role("button", name="역할 저장"), 1, "right")])
        row.get_by_role("button", name="역할 저장").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "role-saved", clip=_user_row(pg, "lee.modeler"))
        go(lee, "/ontology")
        s.shot(lee, "role-kicked")
    # 정지·다시 사용
    with c.app() as pg:
        nav(pg, "관리")
        row = _user_row(pg, "kim.user")
        s.shot(pg, "disable-button", clip=row, marks=[(row.get_by_role("button", name="정지"), 1, "right")])
        row.get_by_role("button", name="정지").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "disabled", clip=_user_row(pg, "kim.user"), marks=[(_user_row(pg, "kim.user").locator(".badge"), 1)])
        with c.app(None) as kim:
            go(kim, "/login")
            kim.get_by_label("아이디").fill("kim.user")
            kim.get_by_label("비밀번호").fill(password())
            kim.get_by_role("button", name="로그인", exact=True).click()
            kim.wait_for_timeout(1500)
            s.shot(kim, "disabled-login", marks=[(".error, [role=alert]", None)])
        row = _user_row(pg, "kim.user")
        row.get_by_role("button", name="다시 사용").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "enabled", clip=_user_row(pg, "kim.user"))
        # 알림 연락처
        row = _user_row(pg, "park.admin")
        row.get_by_role("button", name="고치기").click()
        pg.wait_for_timeout(400)
        s.shot(pg, "contacts-open", clip=row)
        row.locator("input[placeholder='메일 주소']").fill("park.admin@corp.local")
        row.locator("input[placeholder^='휴대전화']").fill("010-1234-5678")
        s.shot(pg, "contacts-filled", clip=row, marks=[(row.get_by_role("button", name="저장"), 1, "right")])
        row.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "contacts-saved", clip=_user_row(pg, "park.admin"))
        row = _user_row(pg, ADMIN)
        row.get_by_role("button", name="고치기").click()
        row.locator("input[placeholder='메일 주소']").fill("ops@corp.local")
        row.locator("input[placeholder^='휴대전화']").fill("010-2222-3333")
        row.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(1200)
        go(pg, "/admin")
        s.shot(pg, "users-final", full=True)


# ---------------------------------------------------------------- 8장 역할별 읽기 권한


def _role_card(pg: Page, role: str) -> Locator:
    return pg.locator("main .card", has=pg.locator(f"strong:text-is('{role}')")).first


def ch_perms(c: Ctx) -> None:
    s = c.s
    with c.app() as pg:
        nav(pg, "관리")
        pg.get_by_role("tab", name="역할별 읽기 권한").click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "perms-page", full=True)
        card = _role_card(pg, "bot_builder")
        s.shot(
            pg,
            "perms-card",
            clip=card,
            marks=[(card.locator("strong"), 1), (card.locator("label.check").first, 2), (card.get_by_role("button", name="권한 저장"), 3, "right")],
        )
    with c.app("jung.builder") as pg:
        ask(pg, "위험등급이 3 이상인 시설은 몇 개야?")
        shot_turn(s, pg, "denied-answer")
    with c.app() as pg:
        nav(pg, "관리")
        pg.get_by_role("tab", name="역할별 읽기 권한").click()
        pg.wait_for_timeout(1200)
        card = _role_card(pg, "bot_builder")
        card.locator("label.check", has_text="Facility").locator("input").click()
        pg.wait_for_timeout(300)
        s.shot(pg, "perms-checked", clip=card, marks=[(card.get_by_role("button", name="권한 저장"), 1, "right")])
        card.get_by_role("button", name="권한 저장").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "perms-saved", clip=_role_card(pg, "bot_builder"), marks=[(_role_card(pg, "bot_builder").locator(".ok-text"), 1)])
    with c.app("jung.builder") as pg:
        ask(pg, "위험등급이 3 이상인 시설은 몇 개야?")
        shot_turn(s, pg, "allowed-answer")
        go(pg, "/chat")
        ask(pg, "강남지사가 소유한 시설 보여줘")
        shot_turn(s, pg, "link-denied-answer")
    with c.app() as pg:
        nav(pg, "관리")
        pg.get_by_role("tab", name="역할별 읽기 권한").click()
        pg.wait_for_timeout(1200)
        for role in ("bot_builder", "modeler"):
            card = _role_card(pg, role)
            for ty in ("Facility", "Organization", "Region"):
                inp = card.locator("label.check", has_text=ty).locator("input")
                if not inp.is_checked():
                    inp.click()
            card.get_by_role("button", name="권한 저장").click()
            pg.wait_for_timeout(1200)
        go(pg, "/admin")
        pg.get_by_role("tab", name="역할별 읽기 권한").click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "perms-final", full=True)
    with c.app("jung.builder") as pg:
        ask(pg, "강남지사가 소유한 시설 보여줘")
        shot_turn(s, pg, "link-allowed-answer")


# ---------------------------------------------------------------- 10장 트레이스

TRACE_SQL = (
    "SELECT t.id, to_char(t.created_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS') AS 시각, u.username,"
    " t.status, t.failure_reason, t.grade FROM trace t LEFT JOIN app_user u ON u.id = t.user_id"
    " ORDER BY t.id DESC LIMIT 12;"
)


def psql_app(sql: str, db: str = "ontobot_db") -> list[str]:
    return sh(f'docker compose exec -T postgres psql -U postgres -d {db} -c "{sql}"')


def trace_ids() -> list[tuple[int, str, str]]:
    out = subprocess.run(
        ["docker", "exec", "ontobot-postgres-1", "psql", "-U", "postgres", "-d", "ontobot_db", "-At", "-F", "|", "-c",
         "SELECT id, coalesce(status,''), coalesce(failure_reason,'') FROM trace ORDER BY id DESC LIMIT 500"],
        capture_output=True,
        encoding="utf-8",
    ).stdout
    rows = []
    for line in out.splitlines():
        i, st, fr = line.split("|")
        rows.append((int(i), st, fr))
    return rows


def open_trace(pg: Page, trace_id: int) -> Locator:
    pg.get_by_role("tab", name="트레이스").click()
    pg.wait_for_timeout(500)
    pg.get_by_label("트레이스 id").fill(str(trace_id))
    pg.get_by_role("button", name="열기").click()
    pg.wait_for_timeout(1500)
    return pg.locator("main .card", has=pg.locator(f"strong:text-is('#{trace_id}')")).first


def ch_trace(c: Ctx) -> None:
    s, t = c.s, c.t
    with c.app("kim.user") as pg:
        ask(pg, "변전소는 몇 개야?")
        ask(pg, "부산 철탑 02호 위험등급 알려줘")
        go(pg, "/chat")
        ask(pg, "시설 점검 담당자 이름 알려줘")
        shot_turn(s, pg, "user-failed-answer")
    t.shot("trace-list", wsl('docker compose exec -T postgres psql -U postgres -d ontobot_db -c "SELECT … FROM trace …"'),
           psql_app(TRACE_SQL))
    rows = trace_ids()
    ok = next(i for i, st, _ in rows if st == "ok")
    failed = next((i for i, st, _ in rows if st == "failed"), None)
    with c.app() as pg:
        nav(pg, "관리")
        pg.get_by_role("tab", name="트레이스").click()
        pg.wait_for_timeout(600)
        s.shot(pg, "trace-tab", marks=[(pg.get_by_label("트레이스 id"), 1), (pg.get_by_role("button", name="열기"), 2, "right")])
        card = open_trace(pg, ok)
        s.shot(pg, "trace-ok", full=True, marks=[(card.locator(".badge"), 1), (card.locator("details").first.locator("summary"), 2)])
        card.locator("details", has_text="LLM 호출").locator("summary").click()
        pg.wait_for_timeout(500)
        s.shot(pg, "trace-ok-llm", full=True)
        if failed:
            card = open_trace(pg, failed)
            s.shot(pg, "trace-failed", full=True, marks=[(card.locator(".badge"), 1)])
        pg.get_by_label("트레이스 id").fill("999999")
        pg.get_by_role("button", name="열기").click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "trace-missing")


# ---------------------------------------------------------------- 11~12장 데이터 소스·매핑·동기화

SRC_DB = "facility_src"
FAC_MAP = {
    "facility_id": "fac_id",
    "name": "fac_name",
    "facility_type": "fac_type",
    "risk_grade": "risk",
    "capacity_kw": "cap_kw",
    "installed_on": "inst_date",
    "owner_org_id": "org_code",
    "region_code": "region",
}
ORG_MAP = {"org_id": "org_id", "name": "org_name", "manager_name": "manager", "manager_phone": "phone"}


def pg_super(db: str, sql: str) -> list[str]:
    return ["docker", "exec", "-i", "ontobot-postgres-1", "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", db, "-c", sql]


def src_sql(sql: str) -> None:
    subprocess.run(
        ["docker", "exec", "-i", "ontobot-postgres-1", "psql", "-q", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", SRC_DB],
        input=sql, text=True, encoding="utf-8", check=True, capture_output=True,
    )


def _mapping(pg: Page, table: str = "fac_master") -> Locator:
    return pg.locator("article.card", has_text=table).first


def sync_and_wait(pg: Page, table: str = "fac_master") -> None:
    rows = _mapping(pg, table).locator("tbody tr")
    before = rows.first.inner_text() if rows.count() else ""
    _mapping(pg, table).get_by_role("button", name="지금 동기화").click()
    for _ in range(160):
        pg.wait_for_timeout(1500)
        go(pg, "/sources", wait=500)
        rows = _mapping(pg, table).locator("tbody tr")
        top = rows.first.inner_text() if rows.count() else ""
        if top and top != before and "실행 중" not in top:
            return
    raise RuntimeError("동기화가 끝나지 않았습니다(워커 확인)")


def ch_sources(c: Ctx) -> None:
    s, t = c.s, c.t
    import manual_env  # type: ignore[import-not-found]

    # 원천 DB 준비(사내 DB 담당자가 할 일을 시험 설치본의 PostgreSQL 안에서 흉내 낸다)
    subprocess.run(pg_super("postgres", f'DROP DATABASE IF EXISTS "{SRC_DB}" WITH (FORCE)'), capture_output=True)
    subprocess.run(pg_super("postgres", "DROP ROLE IF EXISTS ontobot_reader"), capture_output=True)
    subprocess.run(pg_super("postgres", f'CREATE DATABASE "{SRC_DB}"'), check=True, capture_output=True)
    src_sql(manual_env.source_sql())
    reader_pw = os.urandom(12).hex()
    t.shot(
        "reader-sql",
        [
            "psql -h 10.0.0.15 -U postgres -d facility_src     # (원천 DB 담당자) 읽기 전용 계정 만들기",
            "CREATE ROLE ontobot_reader LOGIN PASSWORD '****';",
            "GRANT CONNECT ON DATABASE facility_src TO ontobot_reader;",
            "GRANT USAGE ON SCHEMA public TO ontobot_reader;",
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO ontobot_reader;",
        ],
        ["docker", "exec", "-i", "ontobot-postgres-1", "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", SRC_DB],
        stdin=(
            f"CREATE ROLE ontobot_reader LOGIN PASSWORD '{reader_pw}';\n"
            f"GRANT CONNECT ON DATABASE {SRC_DB} TO ontobot_reader;\n"
            "GRANT USAGE ON SCHEMA public TO ontobot_reader;\n"
            "GRANT SELECT ON ALL TABLES IN SCHEMA public TO ontobot_reader;\n"
        ),
        prompt="",
        title="원천 DB 담당자 PC — psql",
    )
    t.shot(
        "source-tables",
        "psql -h 10.0.0.15 -U ontobot_reader -d facility_src -c \"\\d fac_master\"",
        ["docker", "exec", "ontobot-postgres-1", "psql", "-U", "postgres", "-d", SRC_DB, "-c", "\\d fac_master"],
        title="원천 DB 담당자 PC — psql",
        prompt="> ",
    )
    t.shot(
        "source-rows",
        "psql … -c \"SELECT fac_id, fac_name, fac_type, risk, org_code FROM fac_master LIMIT 8\"",
        ["docker", "exec", "ontobot-postgres-1", "psql", "-U", "postgres", "-d", SRC_DB, "-c",
         "SELECT fac_id, fac_name, fac_type, risk, org_code FROM fac_master ORDER BY fac_id LIMIT 8"],
        title="원천 DB 담당자 PC — psql",
        prompt="> ",
    )
    # app.env 에 접속 정보 다섯 줄(본문은 nano 로 고치는 법을 적는다)
    t.shot("appenv-before", "wsl -d ontobot -u root -- grep -n SRC /opt/ontobot/app.env", sh("grep -n SRC app.env"))
    block = (
        "\n# 시설 원장 DB(읽기 전용 계정) — 화면의 시크릿 참조 env:ONTOBOT_SRC_FAC\n"
        "ONTOBOT_SRC_FAC_HOST=postgres\nONTOBOT_SRC_FAC_PORT=5432\n"
        f"ONTOBOT_SRC_FAC_DB={SRC_DB}\nONTOBOT_SRC_FAC_USER=ontobot_reader\nONTOBOT_SRC_FAC_PASSWORD={reader_pw}\n"
    )
    subprocess.run(sh("grep -q ^ONTOBOT_SRC_FAC_HOST= app.env || cat >> app.env"), input=block, text=True, check=True)
    subprocess.run(sh(f"sed -i 's/^ONTOBOT_SRC_FAC_PASSWORD=.*/ONTOBOT_SRC_FAC_PASSWORD={reader_pw}/' app.env"), check=True)
    t.shot("appenv-after", "wsl -d ontobot -u root -- grep -n SRC /opt/ontobot/app.env", sh("grep -n SRC app.env"))
    t.shot("compose-up", wsl("docker compose up -d"), sh("docker compose up -d 2>&1"), timeout=300)
    time.sleep(20)
    t.shot("compose-ps-after", wsl("docker compose ps --format 'table {{.Service}}\\t{{.Status}}'"),
           sh("docker compose ps --format 'table {{.Service}}\t{{.Status}}'"))
    with c.app("lee.modeler") as pg:
        go(pg, "/sources")
        s.shot(pg, "page-empty", full=True)
        add = pg.locator("details.card", has_text="데이터 소스 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        s.shot(pg, "add-source-open", clip=add, marks=[
            (f.locator("label", has_text="종류").locator("select"), 1),
            (f.locator("label", has_text="이름").locator("input"), 2),
            (f.locator("label", has_text="시크릿 참조").locator("input"), 3),
            (f.get_by_role("button", name="추가"), 4, "right"),
        ])
        f.locator("label", has_text="이름").locator("input").fill("시설 원장 DB")
        f.locator("label", has_text="시크릿 참조").locator("input").fill("env:ONTOBOT_SRC_FAC")
        s.shot(pg, "add-source-filled", clip=add)
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1500)
        add = pg.locator("details.card", has_text="데이터 소스 추가").first
        if add.get_attribute("open") is None:
            add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="이름").locator("input").fill("연습용(접속 정보 없음)")
        f.locator("label", has_text="시크릿 참조").locator("input").fill("env:ONTOBOT_SRC_NONE")
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1500)
        go(pg, "/sources")
        table = pg.locator("table:has(th:has-text('시크릿 참조'))").first
        s.shot(pg, "sources-table", clip=table)
        ok = table.locator("tbody tr", has_text="시설 원장 DB").first
        ok.get_by_role("button", name="접속 시험").click()
        pg.wait_for_timeout(3000)
        s.shot(pg, "test-ok", clip=table, marks=[(ok.locator(".ok-text"), 1)])
        bad = table.locator("tbody tr", has_text="연습용").first
        bad.get_by_role("button", name="접속 시험").click()
        pg.wait_for_timeout(3000)
        s.shot(pg, "test-fail", clip=table, marks=[(bad.locator(".error-text"), 1)])
    # 비밀번호가 틀린 경우 — app.env 를 잠시 틀리게
    subprocess.run(sh("sed -i 's/^ONTOBOT_SRC_FAC_PASSWORD=.*/ONTOBOT_SRC_FAC_PASSWORD=wrong-password/' app.env && docker compose up -d api worker"), capture_output=True)
    time.sleep(25)
    with c.app("lee.modeler") as pg:
        go(pg, "/sources")
        table = pg.locator("table:has(th:has-text('시크릿 참조'))").first
        ok = table.locator("tbody tr", has_text="시설 원장 DB").first
        ok.get_by_role("button", name="접속 시험").click()
        pg.wait_for_timeout(3000)
        s.shot(pg, "test-wrong-password", clip=table, marks=[(ok.locator(".error-text"), 1)])
    subprocess.run(sh(f"sed -i 's/^ONTOBOT_SRC_FAC_PASSWORD=.*/ONTOBOT_SRC_FAC_PASSWORD={reader_pw}/' app.env && docker compose up -d api worker"), capture_output=True)
    time.sleep(25)


def ch_mapping(c: Ctx) -> None:
    s, t = c.s, c.t
    with c.app("lee.modeler") as pg:
        go(pg, "/sources")
        add = pg.locator("details.card", has_text="매핑 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="데이터 소스").locator("select").select_option(label="시설 원장 DB")
        f.locator("label", has_text="객체 타입").locator("select").select_option("Facility")
        f.locator("label", has_text="원천 표").locator("input").fill("fac_master")
        s.shot(pg, "map-start", clip=add, marks=[
            (f.locator("label", has_text="데이터 소스").locator("select"), 1),
            (f.locator("label", has_text="객체 타입").locator("select"), 2),
            (f.locator("label", has_text="원천 표").locator("input"), 3),
            (f.get_by_role("button", name="열 불러오기"), 4, "right"),
        ])
        f.get_by_role("button", name="열 불러오기").click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "map-columns", clip=add)
        for prop, col in FAC_MAP.items():
            f.locator("tbody tr", has=pg.locator(f"code:text-is('{prop}')")).locator("select").select_option(col)
        s.shot(pg, "map-columns-filled", clip=f.locator("table"))
        mode = f.locator("label", has_text="모드").locator("select")
        mode.select_option("incremental")
        pg.wait_for_timeout(300)
        f.locator("label", has_text="워터마크 열").locator("select").select_option("updated_at")
        s.shot(pg, "map-incremental", clip=f.locator(".grid-form").last, marks=[(mode, 1), (f.locator("label", has_text="워터마크 열").locator("select"), 2)])
        mode.select_option("full")
        f.locator("label", has_text="일정").locator("input").fill("every:1h")
        for box in f.locator("label.check input").all():
            box.check()
        s.shot(pg, "map-options", clip=f.locator(".grid-form").last, marks=[
            (mode, 1), (f.locator("label", has_text="일정").locator("input"), 2), (f.locator("label.check").first, 3),
        ])
        s.shot(pg, "map-filled", clip=add)
        f.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(2000)
        go(pg, "/sources")
        card = _mapping(pg)
        s.shot(pg, "map-created", clip=card, marks=[
            (card.locator("h3"), 1), (card.locator(".muted").first, 2), (card.get_by_role("button", name="지금 동기화"), 3, "right"),
        ])
        card.get_by_role("button", name="지금 동기화").click()
        pg.wait_for_timeout(700)
        s.shot(pg, "sync-queued", clip=_mapping(pg))
        for _ in range(160):
            pg.wait_for_timeout(1500)
            go(pg, "/sources", wait=500)
            top = _mapping(pg).locator("tbody tr").first
            if top.count() and "실행 중" not in top.inner_text():
                break
        card = _mapping(pg)
        s.shot(pg, "sync-ok", clip=card, marks=[(card.locator("tbody tr").first.locator(".badge"), 1), (card.locator("tbody tr").first.locator("td").nth(2), 2)])
        # 조직 매핑
        add = pg.locator("details.card", has_text="매핑 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="데이터 소스").locator("select").select_option(label="시설 원장 DB")
        f.locator("label", has_text="객체 타입").locator("select").select_option("Organization")
        f.locator("label", has_text="원천 표").locator("input").fill("org_master")
        f.get_by_role("button", name="열 불러오기").click()
        pg.wait_for_timeout(2000)
        for prop, col in ORG_MAP.items():
            f.locator("tbody tr", has=pg.locator(f"code:text-is('{prop}')")).locator("select").select_option(col)
        f.locator("label", has_text="일정").locator("input").fill("every:1h")
        s.shot(pg, "map-org", clip=add)
        f.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(2000)
        go(pg, "/sources")
        sync_and_wait(pg, "org_master")
        s.shot(pg, "mappings-two", full=True)
        # 실패 → 고침: 원천 표 열 이름이 바뀌었다
        src_sql("ALTER TABLE fac_master RENAME COLUMN risk TO risk_level;")
        sync_and_wait(pg)
        card = _mapping(pg)
        s.shot(pg, "sync-fail", clip=card, marks=[(card.locator("tbody tr").first.locator(".error-text"), 1)])
        src_sql("ALTER TABLE fac_master RENAME COLUMN risk_level TO risk;")
        # 값 변환 실패 — 날짜가 아닌 값
        src_sql("ALTER TABLE fac_master ALTER COLUMN inst_date TYPE text; UPDATE fac_master SET inst_date = '2015-13-45' WHERE fac_id = 'F-0002';")
        sync_and_wait(pg)
        card = _mapping(pg)
        s.shot(pg, "sync-fail-value", clip=card, marks=[(card.locator("tbody tr").first.locator(".error-text"), 1)])
        src_sql("UPDATE fac_master SET inst_date = (SELECT inst_date FROM fac_master WHERE fac_id='F-0001') WHERE fac_id='F-0002';")
        subprocess.run(pg_super(SRC_DB, "SELECT 1"), capture_output=True)
        import manual_env  # type: ignore[import-not-found]
        import csv

        with (ROOT / "domain-packs/sample/seed/facility.csv").open(encoding="utf-8") as fh:
            orig = next(r for r in csv.DictReader(fh) if r["facility_id"] == "F-0002")["installed_on"]
        src_sql(f"UPDATE fac_master SET inst_date = '{orig}' WHERE fac_id='F-0002'; ALTER TABLE fac_master ALTER COLUMN inst_date TYPE date USING inst_date::date;")
        sync_and_wait(pg)
        s.shot(pg, "sync-fixed", clip=_mapping(pg))
        # 원천에서 행이 생기고 사라지면
        src_sql("INSERT INTO fac_master VALUES ('F-0031','대구 전주 31호','전주',2,5,'2024-03-02','ORG-03','27', now());")
        sync_and_wait(pg)
        s.shot(pg, "sync-added", clip=_mapping(pg))
        src_sql("DELETE FROM fac_master WHERE fac_id = 'F-0031';")
        sync_and_wait(pg)
        s.shot(pg, "sync-deleted", clip=_mapping(pg))
        _ = manual_env
    t.shot("sync-runs-sql", wsl('docker compose exec -T postgres psql -U postgres -d ontobot_db -c "SELECT … FROM sync_run …"'),
           psql_app("SELECT id, mapping_id, status, rows_read, rows_upserted, rows_deleted,"
                    " to_char(started_at AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI:SS') AS started FROM sync_run ORDER BY id DESC LIMIT 10;"))
    with c.app("kim.user") as pg:
        ask(pg, "강남 변전소 01호 정보 알려줘")
        last_answer(pg).locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        card = pg.locator(".object-card")
        s.shot(pg, "card-lineage", clip=card, marks=[(card.locator("text=값 출처").first, 1)])


# ---------------------------------------------------------------- 13장 그래프 투영


def _graph(pg: Page) -> Locator:
    return pg.locator(".card", has_text="그래프 투영").first


def wait_healthy(service: str, seconds: int = 180) -> None:
    for _ in range(seconds // 3):
        out = subprocess.run(sh(f"docker compose ps --format '{{{{.Health}}}}' {service}"), capture_output=True, text=True).stdout.strip()
        if out in ("healthy", ""):
            if out == "healthy" or service == "worker":
                return
        time.sleep(3)


def ch_graph(c: Ctx) -> None:
    s, t = c.s, c.t
    with c.app() as pg:
        go(pg, "/sources")
        g = _graph(pg)
        s.shot(pg, "panel", clip=g, marks=[
            (g.locator("p").first, 1), (g.get_by_role("button", name="지금 대사"), 2, "below"), (g.get_by_role("button", name="재구축"), 3, "below"),
        ])
        g.get_by_role("button", name="지금 대사").click()
        pg.wait_for_timeout(6000)
        go(pg, "/sources")
        g = _graph(pg)
        s.shot(pg, "reconciled", clip=g, marks=[(g.locator("p.muted"), 1)])
    t.shot("neo4j-stop", wsl("docker compose stop neo4j"), sh("docker compose stop neo4j 2>&1"))
    time.sleep(8)
    with c.app() as pg:
        go(pg, "/sources")
        pg.wait_for_timeout(5000)
        s.shot(pg, "panel-down", clip=_graph(pg), marks=[(_graph(pg).locator(".error-text"), 1)])
    with c.app("kim.user") as pg:
        ask(pg, "강남지사가 소유한 시설 보여줘")
        shot_turn(s, pg, "traverse-down")
        go(pg, "/chat")
        ask(pg, "위험등급이 3 이상인 시설은 몇 개야?")
        shot_turn(s, pg, "count-while-down")
    with c.app(None, viewport={"width": 1000, "height": 260}) as pg:
        pg.goto(BASE + "/readyz")
        pg.wait_for_timeout(600)
        s.shot(pg, "readyz-degraded")
    with c.console() as pg:
        s.shot(pg, "console-degraded", marks=[(pg.locator("dd.error-text"), 1), (pg.locator("tbody tr", has_text="neo4j"), 2)])
    t.shot("neo4j-start", wsl("docker compose start neo4j"), sh("docker compose start neo4j 2>&1"))
    wait_healthy("neo4j")
    time.sleep(10)
    with c.app() as pg:
        go(pg, "/sources")
        _graph(pg).get_by_role("button", name="지금 대사").click()
        pg.wait_for_timeout(6000)
        go(pg, "/sources")
        s.shot(pg, "panel-back", clip=_graph(pg))
        _graph(pg).get_by_role("button", name="재구축").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "rebuild-started", clip=_graph(pg))
        for _ in range(40):
            pg.wait_for_timeout(3000)
            go(pg, "/sources", wait=500)
            if "미반영 이벤트: 0" in _graph(pg).inner_text():
                break
        _graph(pg).get_by_role("button", name="지금 대사").click()
        pg.wait_for_timeout(6000)
        go(pg, "/sources")
        s.shot(pg, "rebuild-done", clip=_graph(pg))
    t.shot("graph-check", wsl("docker compose run --rm -w /app migrate python scripts/graph_rebuild.py --check"),
           sh("docker compose run --rm -T -w /app migrate python scripts/graph_rebuild.py --check 2>&1"), timeout=300)
    print("    확인 창:", c.s.dialogs, flush=True)


# ---------------------------------------------------------------- 14장 Action 운영


def run_card(pg: Page, text: str, kind: str | None = None) -> Locator:
    loc = pg.locator("details.card", has_text=text)
    if kind:
        loc = loc.filter(has_text=kind)
    return loc.first


def _open(card: Locator) -> None:
    if card.get_attribute("open") is None:
        card.locator("summary").click()
        card.page.wait_for_timeout(500)


def _tab(pg: Page, i: int) -> None:
    pg.locator("nav[role=tablist] button").nth(i).click()
    pg.wait_for_timeout(1500)


def request_on_screen(pg: Page, action: str, pk: str, fields: dict[str, str]) -> Locator:
    go(pg, "/actions")
    panel = pg.locator(".card", has_text="Action 요청").first
    panel.locator("select").first.select_option(action)
    pg.wait_for_timeout(400)
    panel.get_by_label("대상 객체 pk").fill(pk)
    for label, value in fields.items():
        el = panel.get_by_label(label)
        if el.evaluate("e => e.tagName") == "SELECT":
            el.select_option(value)
        else:
            el.fill(value)
    return panel


def ch_actions(c: Ctx) -> None:
    s = c.s
    with c.app("kim.user") as pg:
        panel = request_on_screen(pg, "adjust_risk_grade", "F-0004", {"새 위험등급": "3", "사유": "현장 점검에서 기초 균열 발견"})
        s.shot(pg, "request-filled", clip=panel, marks=[
            (panel.locator("select").first, 1), (panel.get_by_label("대상 객체 pk"), 2),
            (panel.get_by_label("새 위험등급"), 3), (panel.get_by_label("사유"), 4),
            (panel.get_by_role("button", name="미리보기"), 5, "right"),
        ])
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "request-previewed", clip=panel)
        request_on_screen(pg, "adjust_risk_grade", "F-0005", {"새 위험등급": "5", "사유": "태풍 피해 우려"})
        pg.locator(".card", has_text="Action 요청").first.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
        go(pg, "/chat")
        ask(pg, "F-0012 시설 위험등급을 2로 바꿔줘. 사유는 정밀 점검 결과 이상 없음")
        shot_turn(s, pg, "chat-request")
        go(pg, "/actions")
        _tab(pg, 1)
        s.shot(pg, "mine-tab", full=True)
    with c.app("park.admin") as pg:
        s.shot(pg, "approver-badge", clip=pg.locator("header.topbar"), pad=0, marks=[(pg.locator("header .badge"), 1, "below")])
        go(pg, "/actions")
        s.shot(pg, "to-decide", full=True)
        card = run_card(pg, "F-0004")
        _open(card)
        approve = card.get_by_role("button", name="승인하고 실행")
        s.shot(pg, "approve-open", clip=card, marks=[(approve, 1), (card.locator("input"), 2), (card.get_by_role("button", name="거부"), 3, "right")])
        approve.click()
        pg.wait_for_timeout(2500)
        c5 = run_card(pg, "F-0005")
        _open(c5)
        c5.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(2500)
        c12 = run_card(pg, "F-0012")
        _open(c12)
        c12.locator("input").fill("정밀 점검 보고서를 첨부해 다시 요청하세요")
        s.shot(pg, "reject-reason", clip=c12, marks=[(c12.locator("input"), 1), (c12.get_by_role("button", name="거부"), 2, "right")])
        c12.get_by_role("button", name="거부").click()
        pg.wait_for_timeout(2500)
        _tab(pg, 2)
        s.shot(pg, "all-records", full=True)
        for pk, name in [("F-0004", "executed-card"), ("F-0012", "rejected-card")]:
            cd = run_card(pg, pk)
            _open(cd)
            s.shot(pg, name, clip=cd)
        cd = run_card(pg, "F-0005")
        _open(cd)
        rb = cd.get_by_role("button", name="롤백")
        s.shot(pg, "rollback-button", clip=cd, marks=[(rb, 1, "right")])
        rb.click()
        pg.wait_for_timeout(2500)
        cd = run_card(pg, "F-0005")
        _open(cd)
        s.shot(pg, "rolled-back", clip=cd)
        go(pg, "/actions")
        inbox = pg.locator(".card", has_text="알림함")
        s.shot(pg, "inbox", clip=inbox)
    with c.app("kim.user") as pg:
        ask(pg, "F-0004 시설 위험등급 알려줘")
        shot_turn(s, pg, "edited-answer")
    print("    확인 창:", s.dialogs, flush=True)


# ---------------------------------------------------------------- 15장 메일·SMS 알림

MAILSINK = "ontobot-mailsink"
MAILSINK_PY = r'''
import asyncio, datetime, email, email.policy
async def handle(r, w):
    def send(s):
        w.write((s + "\r\n").encode())
    send("220 mailsink ESMTP"); await w.drain()
    data, buf = False, []
    while True:
        line = await r.readline()
        if not line:
            break
        if data:
            if line.rstrip(b"\r\n") == b".":
                data = False
                m = email.message_from_bytes(b"".join(buf), policy=email.policy.default)
                print("=== 메일 받음", datetime.datetime.now().strftime("%H:%M:%S"))
                print("From:", m["From"]); print("To:", m["To"]); print("Subject:", m["Subject"])
                print(m.get_body().get_content().strip(), flush=True)
                buf = []
                send("250 OK")
            else:
                buf.append(line[1:] if line.startswith(b"..") else line)
                continue
        else:
            u = line.decode("ascii", "replace").upper()
            if u.startswith(("HELO", "EHLO")): send("250 mailsink")
            elif u.startswith("DATA"): data = True; send("354 end with .")
            elif u.startswith("QUIT"): send("221 bye"); await w.drain(); break
            else: send("250 OK")
        await w.drain()
    w.close()
async def main():
    srv = await asyncio.start_server(handle, "0.0.0.0", 2525)
    print("mailsink listening 2525", flush=True)
    async with srv:
        await srv.serve_forever()
asyncio.run(main())
'''

NOTIFY_YAML = """\
# Action 알림의 메일·SMS 게이트웨이(TRD 17.1-4). 운영자가 사내 게이트웨이를 설정한다.
max_recipients: 50   # 실행 한 번에 채널별 수신자 상한
max_attempts: 3      # 발송 재시도 상한(넘으면 failed — 실행 기록에 보인다)
retry_seconds: 10    # 재시도 간격 = 이 값 × 시도 횟수

channels:
  email:
    adapter: smtp
    host: ontobot-mailsink        # 사내 메일 서버 주소(촬영 PC 에서는 시험용 메일 수신기)
    port: 2525
    starttls: false
    sender: ontobot@corp.local
  sms:
    adapter: sms_table            # 사내 UMS 발송 테이블에 한 행 INSERT
    secret_ref: env:ONTOBOT_UMS   # ONTOBOT_UMS_HOST·_PORT·_DB·_USER·_PASSWORD (app.env)
    table: ums_send
    to_column: dest_phone
    text_column: msg_text
    sender_column: callback
    sender: "0212345678"
"""


def app_image() -> str:
    return subprocess.run(["docker", "inspect", "-f", "{{.Config.Image}}", "ontobot-api-1"], capture_output=True, text=True).stdout.strip()


def ch_notify(c: Ctx) -> None:
    s, t = c.s, c.t
    # 모델러가 메일·SMS 로 알리는 Action 을 게시한다(사용자 매뉴얼 17장과 같은 화면)
    with c.app("lee.modeler") as pg:
        go(pg, "/ontology")
        pg.locator("tbody tr", has_text="게시됨").get_by_role("button", name="이 버전으로 새 draft").click()
        pg.wait_for_timeout(2000)
        go(pg, "/ontology")
        pg.locator("tbody tr", has_text="draft").first.get_by_role("link", name="열기").click()
        pg.wait_for_timeout(2000)
        sec = pg.locator("h2:has-text('Action')").locator("xpath=..")
        sec.get_by_role("button", name="Action 추가").click()
        pg.wait_for_timeout(600)
        form = sec.locator("form").last
        form.locator("label", has_text="api_name").locator("input").fill("urgent_notice")
        form.locator("label", has_text="표시 이름").locator("input").fill("긴급 정비 통보")
        form.locator("label", has_text="대상 타입").locator("select").select_option("Facility")
        form.locator("label", has_text="설명").locator("input").fill("현장 위험을 담당 관리자에게 메일과 문자로 바로 알린다")
        form.locator("label", has_text="승인 역할").locator("input").fill("admin")
        params = form.locator("fieldset", has=pg.locator("legend", has_text="파라미터"))
        params.get_by_role("button", name="+ 추가").click()
        r = params.locator(".row").nth(0)
        r.locator("input").nth(0).fill("message")
        r.locator("input").nth(1).fill("통보 내용")
        nt = form.locator("fieldset", has=pg.locator("legend", has_text="알림"))
        for ch in ("email", "sms"):
            nt.get_by_role("button", name="+ 추가").click()
            blk = nt.locator(":scope > .stack").last
            blk.locator(".row select").first.select_option(ch)
            blk.locator(".row input").first.fill("admin")
            blk.locator(":scope > input").first.fill("[긴급] '{target}'({target_pk}) {message}")
        form.locator("label", has_text="정정 문구").locator("input").fill("[정정] '{target}'({target_pk}) 긴급 정비 통보를 취소합니다.")
        s.shot(pg, "modeler-action-form", clip=form, marks=[(nt, None)])
        form.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(2000)
        pg.locator("main .row").first.get_by_role("button", name="게시").click()
        pg.wait_for_timeout(3000)
        go(pg, "/ontology")
        s.shot(pg, "versions-after", full=True)
    # 채널을 설정하기 전 — 미리보기가 막힌다
    with c.app("kim.user") as pg:
        panel = request_on_screen(pg, "urgent_notice", "F-0020", {"통보 내용": "철탑 기울어짐 확인"})
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "blocked-preview", clip=panel)
    t.shot("notify-before", "wsl -d ontobot -u root -- cat /opt/ontobot/config/notify.yaml", sh("cat config/notify.yaml"), keep=(14, 4))
    # 사내 UMS 발송 테이블(촬영 PC 에서는 원천 DB 안에 만든다)과 메일 수신기
    src_sql(
        "DROP TABLE IF EXISTS ums_send; CREATE TABLE ums_send (id bigserial primary key, dest_phone text not null,"
        " msg_text text not null, callback text, created_at timestamptz not null default now());"
        " GRANT INSERT, SELECT ON ums_send TO ontobot_reader; GRANT USAGE ON SEQUENCE ums_send_id_seq TO ontobot_reader;"
    )
    subprocess.run(["docker", "rm", "-f", MAILSINK], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--name", MAILSINK, "--network", "ontobot_default", "--entrypoint", "python", app_image(), "-u", "-c", MAILSINK_PY],
        check=True, capture_output=True,
    )
    subprocess.run(sh("cat > config/notify.yaml"), input=NOTIFY_YAML, text=True, encoding="utf-8", check=True)
    pw = subprocess.run(sh("grep ^ONTOBOT_SRC_FAC_PASSWORD= app.env | cut -d= -f2"), capture_output=True, text=True).stdout.strip()
    block = (
        "\n# 사내 UMS(SMS 발송 테이블) — notify.yaml 의 secret_ref env:ONTOBOT_UMS\n"
        "ONTOBOT_UMS_HOST=postgres\nONTOBOT_UMS_PORT=5432\n"
        f"ONTOBOT_UMS_DB={SRC_DB}\nONTOBOT_UMS_USER=ontobot_reader\nONTOBOT_UMS_PASSWORD={pw}\n"
    )
    subprocess.run(sh("grep -q ^ONTOBOT_UMS_HOST= app.env || cat >> app.env"), input=block, text=True, check=True)
    t.shot("notify-after", "wsl -d ontobot -u root -- cat /opt/ontobot/config/notify.yaml", sh("cat config/notify.yaml"))
    t.shot("appenv-ums", "wsl -d ontobot -u root -- grep -n UMS /opt/ontobot/app.env", sh("grep -n UMS app.env"))
    t.shot("notify-apply", wsl("docker compose up -d && docker compose restart api worker"),
           sh("docker compose up -d 2>&1 | grep -E 'Recreate|Started|Running' ; docker compose restart api worker 2>&1"), timeout=300)
    time.sleep(25)
    with c.app("kim.user") as pg:
        panel = request_on_screen(pg, "urgent_notice", "F-0020", {"통보 내용": "철탑 기울어짐 확인"})
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "preview-ok", clip=panel)
    with c.app("park.admin") as pg:
        go(pg, "/actions")
        cd = run_card(pg, "F-0020", "긴급 정비 통보")
        _open(cd)
        s.shot(pg, "approve-notify", clip=cd)
        cd.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(2500)
        _tab(pg, 2)
        cd = run_card(pg, "F-0020", "긴급 정비 통보")
        _open(cd)
        s.shot(pg, "deliveries-pending", clip=cd)
        for _ in range(40):
            pg.wait_for_timeout(3000)
            go(pg, "/actions")
            _tab(pg, 2)
            cd = run_card(pg, "F-0020", "긴급 정비 통보")
            _open(cd)
            if "대기" not in cd.inner_text():
                break
        s.shot(pg, "deliveries-sent", clip=cd)
    t.shot("mailsink-log", f"(사내 메일 서버의 받은 편지 — 촬영 PC 의 시험용 수신기 로그) docker logs {MAILSINK}", ["docker", "logs", MAILSINK],
           title="메일 서버 관리자 화면", prompt="$ ")
    t.shot("ums-rows", "(UMS 담당자) SELECT * FROM ums_send;",
           ["docker", "exec", "ontobot-postgres-1", "psql", "-U", "postgres", "-d", SRC_DB, "-c",
            "SELECT id, dest_phone, msg_text, callback, to_char(created_at AT TIME ZONE 'Asia/Seoul','HH24:MI:SS') AS 시각 FROM ums_send ORDER BY id"],
           title="UMS 담당자 PC — psql", prompt="> ")
    t.shot("worker-notify-log", wsl("docker compose logs --tail 400 worker | grep -i notif"),
           sh("docker compose logs --no-log-prefix --tail 400 worker 2>&1 | grep -i notif | tail -8"))
    # 메일 서버가 멈추면 — 재시도 뒤 실패
    subprocess.run(["docker", "stop", MAILSINK], capture_output=True)
    with c.app("kim.user") as pg:
        panel = request_on_screen(pg, "urgent_notice", "F-0024", {"통보 내용": "변압기 누유 확인"})
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
    with c.app("park.admin") as pg:
        go(pg, "/actions")
        cd = run_card(pg, "F-0024", "긴급 정비 통보")
        _open(cd)
        cd.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(5000)
        _tab(pg, 2)
        cd = run_card(pg, "F-0024", "긴급 정비 통보")
        _open(cd)
        s.shot(pg, "retrying", clip=cd)
        for _ in range(60):
            pg.wait_for_timeout(5000)
            go(pg, "/actions")
            _tab(pg, 2)
            cd = run_card(pg, "F-0024", "긴급 정비 통보")
            _open(cd)
            if "대기" not in cd.inner_text():
                break
        s.shot(pg, "delivery-failed", clip=cd, marks=[(cd.locator(".error-text").first, 1)])
    t.shot("worker-notify-fail-log", wsl("docker compose logs --tail 400 worker | grep -i notif"),
           sh("docker compose logs --no-log-prefix --tail 400 worker 2>&1 | grep -i notif | tail -8"))
    subprocess.run(["docker", "start", MAILSINK], capture_output=True)
    time.sleep(3)
    # 보낸 알림이 있는 Action 을 롤백 — 정정 문구
    with c.app("park.admin") as pg:
        go(pg, "/actions")
        _tab(pg, 2)
        cd = run_card(pg, "F-0020", "긴급 정비 통보")
        _open(cd)
        cd.get_by_role("button", name="롤백").click()
        pg.wait_for_timeout(3000)
        for _ in range(40):
            pg.wait_for_timeout(3000)
            go(pg, "/actions")
            _tab(pg, 2)
            cd = run_card(pg, "F-0020", "긴급 정비 통보")
            _open(cd)
            if "대기" not in cd.inner_text():
                break
        s.shot(pg, "compensation-sent", clip=cd)
    t.shot("mailsink-log-2", f"docker logs {MAILSINK} --since 5m", ["docker", "logs", MAILSINK, "--since", "5m"], title="메일 서버 관리자 화면", prompt="$ ")
    print("    확인 창:", s.dialogs, flush=True)


# ---------------------------------------------------------------- 16장 편집 충돌


def ch_conflicts(c: Ctx) -> None:
    s, t = c.s, c.t
    t.shot("source-update", "(원천 DB 에서 담당 부서가 값을 고침) UPDATE fac_master SET risk = 2 WHERE fac_id = 'F-0004';",
           pg_super(SRC_DB, "UPDATE fac_master SET risk = 2, updated_at = now() WHERE fac_id = 'F-0004'"),
           title="원천 DB 담당자 PC — psql", prompt="> ")
    with c.app("lee.modeler") as pg:
        go(pg, "/sources")
        sync_and_wait(pg)
        panel = pg.locator(".card", has_text="편집 충돌").first
        s.shot(pg, "conflict-open", clip=panel)
    with c.app("kim.user") as pg:
        ask(pg, "F-0004 시설 위험등급 알려줘")
        shot_turn(s, pg, "conflict-answer")
        last_answer(pg).locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        card = pg.locator(".object-card")
        s.shot(pg, "conflict-card", clip=card, marks=[(card.locator(".edited"), 1)])
    with c.app() as pg:
        go(pg, "/sources")
        panel = pg.locator(".card", has_text="편집 충돌").first
        ack = panel.get_by_role("button", name="확인", exact=True)
        s.shot(pg, "conflict-buttons", clip=panel, marks=[(ack, 1), (panel.get_by_role("button", name="편집 해제"), 2, "right")])
        ack.click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "conflict-acked-live", clip=panel)
        panel.get_by_role("button", name="확인함").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "conflict-acked", clip=panel)
        clear = panel.get_by_role("button", name="편집 해제")
        if clear.count():
            clear.first.click()
            pg.wait_for_timeout(2000)
            panel.get_by_role("button", name="닫힘").click()
            pg.wait_for_timeout(1500)
            s.shot(pg, "conflict-cleared", clip=panel)
    with c.app("kim.user") as pg:
        ask(pg, "F-0004 시설 위험등급 알려줘")
        shot_turn(s, pg, "after-clear-answer")
    print("    확인 창:", s.dialogs, flush=True)


# ---------------------------------------------------------------- 17~24장 배포 콘솔

BUNDLE_DIR = ROOT / "dist" / "bundle"
DIRTY_ZIP = "ontobot-0.1.0-032e67b767-dirty.zip"


def status_ready(pg: Page) -> None:
    pg.wait_for_selector("main dl.kv, main .error", timeout=120_000)
    pg.wait_for_timeout(800)


def wait_job(pg: Page, seconds: int = 900, shots: Callable[[int], None] | None = None) -> str:
    """작업 화면에서 끝날 때까지 기다린다. 진행 중에 ``shots(경과 초)`` 를 부른다."""
    t0 = time.time()
    while time.time() - t0 < seconds:
        badge = pg.locator("main .card .badge").first
        text = badge.inner_text() if badge.count() else ""
        if text and text != "진행 중":
            pg.wait_for_timeout(1500)
            return text
        if shots:
            shots(int(time.time() - t0))
        pg.wait_for_timeout(1000)
    raise RuntimeError("작업이 끝나지 않았습니다")


def drop_copy(src: Path, name: str | None = None) -> None:
    r"""반입 폴더에 넣는다. 촬영 PC(Docker Desktop)에서는 C:\OntoBot-bundles 가 아니라 VM 의 /mnt/c/OntoBot-bundles 가
    콘솔의 /drop(읽기 전용)이므로, 같은 경로를 쓰기로 붙인 일회성 컨테이너가 복사한다."""
    drop_sh(f"cp -a /s/{src.name} /d/{name or src.name}", src.parent)


def drop_sh(script: str, src_dir: Path | None = None) -> None:
    vols = ["-v", "/mnt/c/OntoBot-bundles:/d"] + (["-v", f"{src_dir}:/s:ro"] if src_dir else [])
    subprocess.run(["docker", "run", "--rm", "--user", "0", *vols, "--entrypoint", "sh", app_image(), "-c", script],
                   check=True, capture_output=True)


DEPLOYER_C = "ontobot-deployer-deployer-1"


def bundle_card(pg: Page, version: str) -> Locator:
    return pg.locator("main .card", has=pg.locator(f"strong:text-is('{version}')")).first


def ch_console(c: Ctx) -> None:
    s = c.s
    with c.console(None) as pg:
        s.shot(pg, "login-empty", marks=[(pg.get_by_label("아이디"), 1), (pg.get_by_label("비밀번호"), 2), (pg.get_by_role("button", name="로그인"), 3)])
        console_login(pg, ADMIN, "틀린비밀번호입니다")
        s.shot(pg, "login-wrong", marks=[(".error", None)])
        pg.get_by_label("아이디").fill("")
        console_login(pg, "kim.user")
        s.shot(pg, "login-not-admin", marks=[(".error", None)])
        pg.get_by_label("아이디").fill("")
        pg.get_by_label("아이디").fill(ADMIN)
        pg.get_by_label("비밀번호").fill(password())
        s.shot(pg, "login-filled")
        pg.get_by_role("button", name="로그인").click()
        status_ready(pg)
        bar = pg.locator("header.topbar")
        tabs = bar.locator("nav button")
        s.shot(pg, "topbar", marks=[
            (tabs.nth(0), 1, "below"), (tabs.nth(1), 2, "below"), (tabs.nth(2), 3, "below"), (tabs.nth(3), 4, "below"),
            (bar.locator(".muted"), 5, "below"), (bar.get_by_role("button", name="로그아웃"), 6, "below"),
        ])
        bar.get_by_role("button", name="로그아웃").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "after-logout")
    with c.app() as pg:
        nav(pg, "관리")
        with pg.expect_popup() as pop:
            pg.get_by_role("link", name="배포 콘솔 열기(업그레이드·롤백·백업)").click()
        popup = pop.value
        popup.set_viewport_size({"width": 1280, "height": 800})
        popup.wait_for_load_state("networkidle")
        s.shot(popup, "opened-from-admin")


def ch_status(c: Ctx) -> None:
    s, t = c.s, c.t
    with c.console() as pg:
        status_ready(pg)
        kv = pg.locator("main dl.kv")
        dts = kv.locator("dt")
        s.shot(pg, "status-marked", marks=[
            *[(dts.nth(i), i + 1) for i in range(6)],
            (pg.locator("main table"), 7),
            (pg.get_by_role("button", name="지금 백업"), 8, "right"),
            (pg.locator("main h2", has_text="백업 파일"), 9),
        ])
        s.shot(pg, "status-table", clip=pg.locator("main table"))
    t.shot("ctl-status-json", "wsl -d ontobot -u root -- ontobotctl status --json", ctl("status", "--json"))


def ch_backup(c: Ctx) -> None:
    s, t = c.s, c.t
    t.shot("backups-before", "wsl -d ontobot -u root -- ls -l /opt/ontobot/backups", sh("ls -l backups"))
    with c.console() as pg:
        status_ready(pg)
        pg.get_by_role("button", name="지금 백업").click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "backup-running")
        wait_job(pg, 300)
        s.shot(pg, "backup-done", full=True)
        console_tab(pg, "현황")
        status_ready(pg)
        s.shot(pg, "status-after-backup", marks=[(pg.locator("main dl.kv dd").nth(3), 1), (pg.locator("main ul li").first, 2)])
    t.shot("ctl-backup", "wsl -d ontobot -u root -- ontobotctl backup --label before-change", ctl("backup", "--label", "before-change"), timeout=300)
    t.shot("backups-after", "wsl -d ontobot -u root -- ls -lh /opt/ontobot/backups", sh("ls -lh backups"))
    t.shot("backup-list", "wsl -d ontobot -u root -- bash -c \"cd /opt/ontobot && docker compose exec -T postgres pg_restore -l /backups/<파일> | head -20\"",
           sh("f=$(ls -t backups | head -1); docker compose exec -T postgres pg_restore -l /backups/$f | head -20"))
    t.shot("backup-env", "wsl -d ontobot -u root -- grep BACKUP /opt/ontobot/.env", sh("grep BACKUP .env"))
    with c.console() as pg:
        status_ready(pg)
        s.shot(pg, "status-two-backups", clip=pg.locator("main"), pad=0)


def ch_bundles(c: Ctx) -> None:
    s, t = c.s, c.t
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "번들")
        s.shot(pg, "bundles-start", full=True)
        card = bundle_card(pg, "0.1.0-2098e81ffd-m1")
        s.shot(pg, "card-marked", clip=card, marks=[
            (card.locator("strong").first, 1), (card.locator(".badge").first, 2), (card.locator(".badge.succeeded"), 3, "below"),
            (card.locator(".muted").first, 4, "below"), (card.get_by_role("button", name="점검"), 5, "below"),
            (card.get_by_role("button", name="업그레이드"), 6, "below"), (card.get_by_role("button", name="삭제"), 7, "below"),
        ])
        card.get_by_role("button", name="점검").click()
        pg.wait_for_timeout(1500)
        wait_job(pg, 300)
        s.shot(pg, "preflight-same-job", full=True)
        console_tab(pg, "번들")
        s.shot(pg, "preflight-same-card", clip=bundle_card(pg, "0.1.0-2098e81ffd-m1"))
    # 손상된 번들 — 진짜 m2 폴더를 복사해 파일 하나를 바꾼다
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp())
    bad = tmp / BUNDLE_M2
    shutil.copytree(BUNDLE_DIR / BUNDLE_M2, bad)
    with (bad / "stack" / "compose.yaml").open("a", encoding="utf-8") as fh:
        fh.write("\n# 누군가 덧붙인 줄\n")
    drop_copy(bad)
    shutil.rmtree(tmp, ignore_errors=True)
    drop_copy(BUNDLE_DIR / DIRTY_ZIP)
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "번들")
        dropc = pg.locator("main .card", has_text="반입 폴더").first
        s.shot(pg, "drop-card", clip=dropc, marks=[(dropc.get_by_role("button", name="가져오기").first, 1, "right")])
        dropc.locator(".row", has_text=BUNDLE_M2).get_by_role("button", name="가져오기").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "import-tampered-running")
        wait_job(pg, 600)
        s.shot(pg, "import-tampered-job", full=True)
        console_tab(pg, "번들")
        card = bundle_card(pg, "0.1.0-2098e81ffd-m2")
        s.shot(pg, "tampered-card", clip=card, marks=[(card.locator(".error-text").first, 1)])
        card.get_by_role("button", name="삭제").click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "tampered-removed", full=True)
        dropc = pg.locator("main .card", has_text="반입 폴더").first
        dropc.locator(".row", has_text="dirty").get_by_role("button", name="가져오기").click()
        pg.wait_for_timeout(1500)
        wait_job(pg, 600)
        s.shot(pg, "import-dirty-job", full=True)
        console_tab(pg, "번들")
        s.shot(pg, "dirty-card", full=True)
    drop_sh(f"rm -rf /d/{BUNDLE_M2} /d/{DIRTY_ZIP}")
    # 진짜 업그레이드 번들 — 화면에서 올리기
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "번들")
        for cd in pg.locator("main .card", has=pg.locator(".badge.failed")).all():
            if cd.get_by_role("button", name="삭제").count():
                cd.get_by_role("button", name="삭제").click()
                pg.wait_for_timeout(1500)
        up = pg.locator("main .card", has_text="번들 올리기(.zip)").first
        s.shot(pg, "upload-card", clip=up, marks=[(up.locator("input[type=file]"), 1)])
        up.locator("input[type=file]").set_input_files(str(BUNDLE_DIR / f"{BUNDLE_M2}.zip"))
        pg.wait_for_timeout(2500)
        s.shot(pg, "upload-progress", clip=up)
        pg.wait_for_url("**", timeout=5000)
        for _ in range(600):
            if pg.locator("main .card .badge").count() and pg.get_by_text("← 작업 기록").count():
                break
            pg.wait_for_timeout(1000)
        s.shot(pg, "upload-import-running")
        wait_job(pg, 900)
        s.shot(pg, "upload-import-job", full=True)
        console_tab(pg, "번들")
        s.shot(pg, "bundles-ready", full=True)
        s.shot(pg, "m2-card", clip=bundle_card(pg, "0.1.0-2098e81ffd-m2"))
    t.shot("bundles-ls-after", "wsl -d ontobot -u root -- ls /opt/ontobot/bundles", sh("ls bundles"))
    t.shot("preflight-m2-cli", f"wsl -d ontobot -u root -- ontobotctl preflight /opt/ontobot/bundles/{BUNDLE_M2}",
           sh(f"ontobotctl preflight bundles/{BUNDLE_M2}"))
    t.shot("m2-bundle-env", f"wsl -d ontobot -u root -- cat /opt/ontobot/bundles/{BUNDLE_M2}/bundle.env", sh(f"cat bundles/{BUNDLE_M2}/bundle.env"))
    print("    확인 창:", s.dialogs, flush=True)


def ch_upgrade(c: Ctx) -> None:
    s, t = c.s, c.t
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "번들")
        card = bundle_card(pg, "0.1.0-2098e81ffd-m2")
        card.get_by_role("button", name="업그레이드").click()
        pg.wait_for_timeout(600)
        form = card.locator("form")
        s.shot(pg, "upgrade-form", clip=card, marks=[(form.locator("input"), 1), (form.get_by_role("button", name="실행"), 2, "right")])
        form.locator("input").fill("0.1.0-2098e81ffd")
        s.shot(pg, "upgrade-typo", clip=form, marks=[(form.get_by_role("button", name="실행"), 1, "right")])
        form.locator("input").fill("0.1.0-2098e81ffd-m2")
        s.shot(pg, "upgrade-typed", clip=form, marks=[(form.get_by_role("button", name="실행"), 1, "right")])
        form.get_by_role("button", name="실행").click()
        pg.wait_for_timeout(1500)
        taken: set[int] = set()

        def during(sec: int) -> None:
            n = len(pg.locator("main ol li").all())
            if n not in taken and n > 0:
                taken.add(n)
                s.shot(pg, f"upgrade-step-{n}", full=True)

        result = wait_job(pg, 1200, during)
        print("    업그레이드 결과:", result, flush=True)
        s.shot(pg, "upgrade-done", full=True)
        s.shot(pg, "upgrade-done-top")
        console_tab(pg, "현황")
        status_ready(pg)
        s.shot(pg, "status-after-upgrade", marks=[(pg.locator("main dl.kv dd").nth(0), 1), (pg.locator("main dl.kv dd").nth(1), 2), (pg.locator("main dl.kv dd").nth(3), 3)])
    with c.app() as pg:
        nav(pg, "관리")
        s.shot(pg, "admin-version", clip=pg.locator("main .card").first, marks=[(pg.locator("main .card").first.locator("code"), 1)])
        go(pg, "/chat")
        ask(pg, "위험등급이 3 이상인 시설은 몇 개야?")
        shot_turn(s, pg, "answer-after-upgrade")
    t.shot("ctl-status-after", "wsl -d ontobot -u root -- ontobotctl status", ctl("status"))
    t.shot("state-after", "wsl -d ontobot -u root -- cat /opt/ontobot/state.env", sh("cat state.env"))


def ch_deployer_update(c: Ctx) -> None:
    s, t = c.s, c.t
    t.shot("deployer-next-env", "wsl -d ontobot -u root -- grep DEPLOYER_IMAGE /opt/ontobot/.env", sh("grep DEPLOYER_IMAGE .env"))
    with c.console() as pg:
        status_ready(pg)
        row = pg.locator("main .row", has_text="배포 콘솔 갱신")
        if not row.count():
            print("    새 배포 콘솔이 없습니다 — 건너뜀")
            return
        s.shot(pg, "deployer-next", marks=[(row.locator(".muted"), 1), (row.get_by_role("button", name="배포 콘솔 갱신"), 2, "right")])
        row.get_by_role("button", name="배포 콘솔 갱신").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "deployer-update-job", full=True)
        for _ in range(60):
            pg.wait_for_timeout(3000)
            try:
                pg.reload(wait_until="networkidle", timeout=10_000)
            except Exception:  # noqa: BLE001 — 콘솔이 다시 뜨는 동안은 접속이 끊긴다
                continue
            if pg.get_by_label("아이디").count():
                break
        s.shot(pg, "deployer-relogin")
        console_login(pg, ADMIN)
        status_ready(pg)
        s.shot(pg, "deployer-updated")
    t.shot("deployer-env-after", "wsl -d ontobot -u root -- grep DEPLOYER_IMAGE /opt/ontobot/.env", sh("grep DEPLOYER_IMAGE .env"))
    print("    확인 창:", s.dialogs, flush=True)


def ch_deployer_after(c: Ctx) -> None:
    """갱신 직후에는 새 콘솔의 건강 확인이 끝날 때까지 안내가 남는다 — 잠시 뒤 다시 본다."""
    s, t = c.s, c.t
    with c.console() as pg:
        status_ready(pg)
        s.shot(pg, "deployer-updated-later")
    t.shot("deployer-env-later", "wsl -d ontobot -u root -- grep DEPLOYER_IMAGE /opt/ontobot/.env", sh("grep DEPLOYER_IMAGE .env"))
    t.shot("deployer-ps", wsl("docker compose -f compose.deployer.yaml ps"), sh("docker compose -f compose.deployer.yaml ps"))


def ch_rollback(c: Ctx) -> None:
    s, t = c.s, c.t
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "롤백")
        form = pg.locator("main form")
        s.shot(pg, "rollback-form", marks=[
            (form.locator("label.check").nth(0), 1), (form.locator("label.check").nth(1), 2), (form.get_by_role("button", name="실행"), 3, "right"),
        ])
        form.get_by_role("button", name="실행").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "rollback-running", full=True)
        print("    롤백 결과:", wait_job(pg, 900), flush=True)
        s.shot(pg, "rollback-done", full=True)
        console_tab(pg, "현황")
        status_ready(pg)
        s.shot(pg, "status-after-rollback")
        console_tab(pg, "롤백")
        s.shot(pg, "rollback-none")
    t.shot("state-after-rollback", "wsl -d ontobot -u root -- cat /opt/ontobot/state.env", sh("cat state.env"))
    # 다시 올리고, 그 뒤에 생긴 데이터가 DB 복원으로 사라지는 것을 보인다
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "번들")
        card = bundle_card(pg, "0.1.0-2098e81ffd-m2")
        card.get_by_role("button", name="점검").click()
        pg.wait_for_timeout(1500)
        wait_job(pg, 300)
        console_tab(pg, "번들")
        card = bundle_card(pg, "0.1.0-2098e81ffd-m2")
        s.shot(pg, "m2-card-again", clip=card)
        card.get_by_role("button", name="업그레이드").click()
        card.locator("form input").fill("0.1.0-2098e81ffd-m2")
        card.locator("form").get_by_role("button", name="실행").click()
        pg.wait_for_timeout(1500)
        print("    재업그레이드:", wait_job(pg, 1200), flush=True)
    with c.app() as pg:
        nav(pg, "관리")
        _add_user(pg, "temp.after", "업그레이드 뒤 계정", ["user"], password())
        pg.locator("details.card form").get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "users-before-restore", clip=pg.locator("main table").first, marks=[(_user_row(pg, "temp.after"), None)])
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "롤백")
        form = pg.locator("main form")
        form.locator("label.check").nth(1).locator("input").check()
        pg.wait_for_timeout(400)
        s.shot(pg, "restore-warning", marks=[(form.locator(".error"), 1), (form.locator(".error input"), 2)])
        form.locator(".error input").fill("restore")
        s.shot(pg, "restore-typo", clip=form, marks=[(form.get_by_role("button", name="실행"), 1, "right")])
        form.locator(".error input").fill("RESTORE")
        s.shot(pg, "restore-typed", clip=form, marks=[(form.get_by_role("button", name="실행"), 1, "right")])
        form.get_by_role("button", name="실행").click()
        pg.wait_for_timeout(1500)
        print("    DB 복원 롤백:", wait_job(pg, 1200), flush=True)
        s.shot(pg, "restore-done", full=True)
        console_tab(pg, "현황")
        status_ready(pg)
        s.shot(pg, "status-after-restore")
    with c.app() as pg:
        nav(pg, "관리")
        s.shot(pg, "users-after-restore", clip=pg.locator("main table").first)
    t.shot("state-after-restore", "wsl -d ontobot -u root -- cat /opt/ontobot/state.env", sh("cat state.env"))


def ch_restore_demo(c: Ctx) -> None:
    """DB 까지 복원하는 롤백 — 업그레이드 뒤에 만든 계정이 사라지는 것을 보인다(22장 사진을 다시 찍는다)."""
    s, t = c.s, c.t
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "번들")
        card = bundle_card(pg, "0.1.0-2098e81ffd-m2")
        card.get_by_role("button", name="점검").click()
        pg.wait_for_timeout(1500)
        wait_job(pg, 300)
        console_tab(pg, "번들")
        pg.wait_for_timeout(5000)
        card = bundle_card(pg, "0.1.0-2098e81ffd-m2")
        card.get_by_role("button", name="업그레이드").click()
        card.locator("form input").fill("0.1.0-2098e81ffd-m2")
        card.locator("form").get_by_role("button", name="실행").click()
        pg.wait_for_timeout(1500)
        print("    재업그레이드:", wait_job(pg, 1200), flush=True)
    wait_healthy("api")
    time.sleep(10)
    with c.app() as pg:
        nav(pg, "관리")
        box = _add_user(pg, "temp.after", "업그레이드 뒤 계정", ["user"], password())
        box.locator("form").get_by_role("button", name="추가").click()
        pg.wait_for_timeout(2000)
        go(pg, "/admin")
        assert _user_row(pg, "temp.after").count(), "temp.after 계정을 만들지 못했습니다"
        s.shot(pg, "users-before-restore", clip=pg.locator("main table").first, marks=[(_user_row(pg, "temp.after"), 1)])
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "롤백")
        form = pg.locator("main form")
        form.locator("label.check").nth(1).locator("input").check()
        pg.wait_for_timeout(400)
        s.shot(pg, "restore-warning", marks=[(form.locator(".error"), 1), (form.locator(".error input"), 2)])
        form.locator(".error input").fill("restore")
        s.shot(pg, "restore-typo", clip=form, marks=[(form.get_by_role("button", name="실행"), 1, "right")])
        form.locator(".error input").fill("RESTORE")
        s.shot(pg, "restore-typed", clip=form, marks=[(form.get_by_role("button", name="실행"), 1, "right")])
        form.get_by_role("button", name="실행").click()
        pg.wait_for_timeout(1500)
        print("    DB 복원 롤백:", wait_job(pg, 1200), flush=True)
        s.shot(pg, "restore-done", full=True)
        console_tab(pg, "현황")
        status_ready(pg)
        s.shot(pg, "status-after-restore")
    wait_healthy("api")
    time.sleep(5)
    with c.app() as pg:
        nav(pg, "관리")
        s.shot(pg, "users-after-restore", clip=pg.locator("main table").first)
    t.shot("state-after-restore", "wsl -d ontobot -u root -- cat /opt/ontobot/state.env", sh("cat state.env"))


def ch_jobs(c: Ctx) -> None:
    s, t = c.s, c.t
    with c.console() as pg:
        status_ready(pg)
        console_tab(pg, "작업 기록")
        s.shot(pg, "jobs-list", full=True)
        rows = pg.locator("main tbody tr")
        s.shot(pg, "jobs-columns", clip=pg.locator("main table"), marks=[(pg.locator("main thead th").nth(i), i + 1, "below") for i in range(4)])
        up = rows.filter(has_text="업그레이드").first
        up.get_by_role("button", name="보기").click()
        pg.wait_for_timeout(2000)
        card = pg.locator("main .card").first
        s.shot(pg, "job-view-marked", full=True, marks=[
            (pg.get_by_role("button", name="← 작업 기록"), 1, "right"), (card.locator("strong").first, 2), (card.locator(".badge"), 3, "below"),
            (card.locator(".muted"), 4, "below"), (card.locator("ol"), 5), (pg.get_by_role("link", name="로그 내려받기"), 6, "below"),
            (pg.locator("pre.log"), 7),
        ])
        with pg.expect_download() as dl:
            pg.get_by_role("link", name="로그 내려받기").click()
        path = Path(dl.value.path())
        text = path.read_text(encoding="utf-8", errors="replace")
        t.shot("job-log-file", f"notepad {dl.value.suggested_filename}   (내려받은 로그 파일 내용)", text=text, keep=(45, 12),
               title="메모장 대신 — 내려받은 로그", prompt="")
        pg.get_by_role("button", name="← 작업 기록").click()
        pg.wait_for_timeout(1000)
        for label, name in [("롤백", "job-rollback"), ("백업", "job-backup"), ("사전 점검", "job-preflight")]:
            r = pg.locator("main tbody tr").filter(has_text=label).first
            if r.count():
                r.get_by_role("button", name="보기").click()
                pg.wait_for_timeout(2000)
                s.shot(pg, name, full=True)
                pg.get_by_role("button", name="← 작업 기록").click()
                pg.wait_for_timeout(1000)
    t.shot("deployer-jobs-dir", "wsl -d ontobot -u root -- ls /opt/ontobot/deployer/jobs | tail", sh("ls deployer/jobs 2>/dev/null | tail; ls deployer | head"))


# ---------------------------------------------------------------- 9장 감사 로그(앞 장들이 남긴 기록으로)


def ch_audit(c: Ctx) -> None:
    s = c.s
    with c.app() as pg:
        nav(pg, "관리")
        pg.get_by_role("tab", name="감사 로그").click()
        pg.wait_for_timeout(1500)
        # v1 관리 화면은 감사 로그 표 앞에 성공 지표 목표 표가 있다 — "이벤트" 칸이 있는 표가 감사 로그다.
        table = pg.locator("main table", has=pg.locator("th", has_text="이벤트")).first
        s.shot(pg, "audit-page", marks=[(pg.get_by_label("이벤트로 거르기"), 1), (table.locator("thead"), 2)])
        s.shot(pg, "audit-full", full=True)
        for ev in ["auth.login", "auth.login_failed", "security.user_created", "security.role_changed", "security.user_status_changed",
                   "security.user_contacts_changed", "security.permission_changed", "security.trace_viewed", "security.audit_viewed",
                   "sync.source_changed", "sync.mapping_changed", "sync.manual_run", "graph.rebuild", "ontology.version_published",
                   "action.previewed", "action.executed", "action.rejected", "action.rolled_back", "objects.conflict_acknowledged",
                   "objects.edit_cleared", "deploy.backup", "deploy.bundle_imported", "deploy.upgrade", "deploy.login", "deploy.login_failed",
                   "deploy.rollback", "deploy.deployer_update"]:
            box = pg.get_by_label("이벤트로 거르기")
            box.fill(ev)
            pg.wait_for_timeout(1800)
            s.shot(pg, f"filter-{ev.replace('.', '-')}", clip=table)
        pg.get_by_label("이벤트로 거르기").fill("")
        pg.wait_for_timeout(1500)
        more = pg.get_by_role("button", name="더 보기")
        if more.count():
            s.shot(pg, "more-button", marks=[(more, 1, "right")], full=True)


# ---------------------------------------------------------------- 26장 로그


def ch_logs(c: Ctx) -> None:
    t = c.t
    t.shot("api-logs", wsl("docker compose logs --tail 15 api"), sh("docker compose logs --tail 15 api 2>&1"))
    t.shot("worker-logs", wsl("docker compose logs --tail 15 worker"), sh("docker compose logs --tail 15 worker 2>&1"))
    t.shot("logs-since", wsl("docker compose logs --since 10m api | grep -i error"),
           sh("docker compose logs --since 10m api 2>&1 | grep -i error | tail -10; echo '(끝)'"))
    t.shot("logs-dir", "wsl -d ontobot -u root -- ls -l /opt/ontobot/logs", sh("ls -l logs"))
    t.shot("logs-file-tail", "wsl -d ontobot -u root -- tail -n 5 /opt/ontobot/logs/<파일>",
           sh("f=$(ls -t logs/*.log 2>/dev/null | head -1); [ -n \"$f\" ] && tail -n 5 \"$f\" || echo '(파일 로그 없음)'"))
    t.shot("logs-one-line", wsl("docker compose logs --no-log-prefix --tail 1 api | python3 -m json.tool"),
           sh("docker compose logs --no-log-prefix --tail 30 api 2>&1 | grep '^{' | tail -1 | python3 -m json.tool 2>/dev/null || docker compose logs --no-log-prefix --tail 1 api"))
    t.shot("postgres-logs", wsl("docker compose logs --tail 10 postgres"), sh("docker compose logs --tail 10 postgres 2>&1"))
    t.shot("neo4j-logs", wsl("docker compose logs --tail 10 neo4j"), sh("docker compose logs --tail 10 neo4j 2>&1"))
    t.shot("ollama-logs", wsl("docker compose logs --tail 10 ollama"), sh("docker compose logs --tail 10 ollama 2>&1"))
    t.shot("deployer-history", "wsl -d ontobot -u root -- tail -n 5 /opt/ontobot/deployer/history.jsonl", sh("tail -n 5 deployer/history.jsonl"))


# ---------------------------------------------------------------- 27장 장애 대응(일부러 멈춰 본다)


def ch_incidents(c: Ctx) -> None:
    s, t = c.s, c.t
    # 1) AI 모델(ollama)
    t.shot("ollama-stop", wsl("docker compose stop ollama"), sh("docker compose stop ollama 2>&1"))
    with c.app("kim.user") as pg:
        ask(pg, "위험등급이 3 이상인 시설은 몇 개야?")
        shot_turn(s, pg, "ollama-down-answer")
    with c.app(None, viewport={"width": 1000, "height": 260}) as pg:
        pg.goto(BASE + "/readyz")
        pg.wait_for_timeout(600)
        s.shot(pg, "ollama-down-readyz")
    with c.console() as pg:
        status_ready(pg)
        s.shot(pg, "ollama-down-console", marks=[(pg.locator("main dl.kv dd").nth(2), 1), (pg.locator("tbody tr", has_text="ollama"), 2)])
    t.shot("ollama-start", wsl("docker compose start ollama"), sh("docker compose start ollama 2>&1"))
    wait_healthy("ollama")
    time.sleep(10)
    # 2) 관계 저장소(neo4j) — 현황 화면
    subprocess.run(sh("docker compose stop neo4j"), capture_output=True)
    time.sleep(5)
    with c.console() as pg:
        status_ready(pg)
        s.shot(pg, "neo4j-down-console", marks=[(pg.locator("main dl.kv dd").nth(2), 1), (pg.locator("tbody tr", has_text="neo4j"), 2)])
    subprocess.run(sh("docker compose start neo4j"), capture_output=True)
    wait_healthy("neo4j")
    # 3) 워커
    t.shot("worker-stop", wsl("docker compose stop worker"), sh("docker compose stop worker 2>&1"))
    with c.app("lee.modeler") as pg:
        go(pg, "/sources")
        _mapping(pg).get_by_role("button", name="지금 동기화").click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "worker-down-queued", clip=_mapping(pg))
        _mapping(pg).get_by_role("button", name="지금 동기화").click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "worker-down-already", clip=_mapping(pg))
        pg.wait_for_timeout(30_000)
        go(pg, "/sources")
        s.shot(pg, "worker-down-30s", clip=_mapping(pg))
    with c.console() as pg:
        status_ready(pg)
        s.shot(pg, "worker-down-console", marks=[(pg.locator("tbody tr", has_text="worker"), 1)])
    t.shot("worker-start", wsl("docker compose start worker"), sh("docker compose start worker 2>&1"))
    time.sleep(25)
    with c.app("lee.modeler") as pg:
        go(pg, "/sources")
        s.shot(pg, "worker-back", clip=_mapping(pg))
    # 4) api
    t.shot("api-stop", wsl("docker compose stop api"), sh("docker compose stop api 2>&1"))
    with c.app(None) as pg:
        try:
            pg.goto(BASE + "/login", timeout=15_000)
        except Exception:  # noqa: BLE001 — 접속이 안 되는 화면을 찍는다
            pass
        pg.wait_for_timeout(1500)
        s.shot(pg, "api-down-browser")
    with c.console() as pg:
        status_ready(pg)
        s.shot(pg, "api-down-console", marks=[(pg.locator("main dl.kv dd").nth(2), 1), (pg.locator("tbody tr", has_text="api").first, 2)])
    t.shot("api-start", wsl("docker compose start api"), sh("docker compose start api 2>&1"))
    wait_healthy("api")
    # 5) postgres
    with c.console() as pg:
        status_ready(pg)
        t.shot("postgres-stop", wsl("docker compose stop postgres"), sh("docker compose stop postgres 2>&1"))
        time.sleep(5)
        pg.reload()
        status_ready(pg)
        pg.wait_for_timeout(12_000)
        s.shot(pg, "postgres-down-console", marks=[(pg.locator("main dl.kv dd").nth(2), 1)])
    with c.console(None) as pg:
        console_login(pg, ADMIN)
        s.shot(pg, "postgres-down-console-login", marks=[(".error", None)])
    with c.app(None) as pg:
        go(pg, "/login")
        pg.get_by_label("아이디").fill("kim.user")
        pg.get_by_label("비밀번호").fill(password())
        pg.get_by_role("button", name="로그인", exact=True).click()
        pg.wait_for_timeout(3000)
        s.shot(pg, "postgres-down-web-login", marks=[(".error, [role=alert]", None)])
    t.shot("postgres-start", wsl("docker compose start postgres"), sh("docker compose start postgres 2>&1"))
    wait_healthy("postgres")
    time.sleep(10)
    with c.app(None, viewport={"width": 1000, "height": 260}) as pg:
        pg.goto(BASE + "/readyz")
        pg.wait_for_timeout(600)
        s.shot(pg, "postgres-back-readyz")
    # 6) 배포 콘솔
    t.shot("deployer-stop", f"wsl -d ontobot -u root -- docker stop {DEPLOYER_C}", ["docker", "stop", DEPLOYER_C])
    with c.app(None) as pg:
        try:
            pg.goto(DEPLOYER_URL_FOR_DOWN, timeout=15_000)
        except Exception:  # noqa: BLE001
            pass
        pg.wait_for_timeout(1500)
        s.shot(pg, "deployer-down-browser")
    t.shot("deployer-start", f"wsl -d ontobot -u root -- docker start {DEPLOYER_C}", ["docker", "start", DEPLOYER_C])
    time.sleep(15)
    # 7) 접속 주소가 허용 출처와 다를 때
    with c.app(None) as pg:
        pg.goto("http://127.0.0.1:8000/login")
        pg.wait_for_timeout(1000)
        pg.get_by_label("아이디").fill("kim.user")
        pg.get_by_label("비밀번호").fill(password())
        pg.get_by_role("button", name="로그인", exact=True).click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "origin-mismatch", marks=[(".error, [role=alert]", None)])
    # 8) 일반 사용자가 관리 주소를 직접 열면
    with c.app("kim.user") as pg:
        go(pg, "/admin", wait=8000)
        s.shot(pg, "user-opens-admin")


DEPLOYER_URL_FOR_DOWN = DEPLOY


# ---------------------------------------------------------------- 28장 명령 모음(실행 결과)


def ch_cli(c: Ctx) -> None:
    t = c.t
    t.shot("ctl-unknown", "wsl -d ontobot -u root -- ontobotctl help", ctl("help"), expect_fail=True)
    t.shot("compose-ps-a", wsl("docker compose ps -a"), sh("docker compose ps -a --format 'table {{.Service}}\t{{.Image}}\t{{.Status}}'"))
    t.shot("docker-stats", wsl("docker stats --no-stream"), sh("docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | grep -E 'NAME|ontobot'"))
    t.shot("docker-df", wsl("docker system df"), sh("docker system df"))
    t.shot("ollama-list", wsl("docker compose exec ollama ollama list"), sh("docker compose exec -T ollama ollama list"))
    t.shot("ollama-ps", wsl("docker compose exec ollama ollama ps"), sh("docker compose exec -T ollama ollama ps"))
    t.shot("nvidia-smi", wsl("docker compose exec ollama nvidia-smi"), sh("docker compose exec -T ollama nvidia-smi 2>&1 | head -20"))
    t.shot("restart-api-worker", wsl("docker compose restart api worker"), sh("docker compose restart api worker 2>&1"))
    time.sleep(20)
    t.shot("readyz-curl", wsl("docker compose exec api python -c \"…/readyz…\""),
           sh("docker compose exec -T api python -c \"import urllib.request as u;print(u.urlopen('http://127.0.0.1:8000/readyz').read().decode())\""))
    t.shot("grant-type-cli", wsl("docker compose run --rm migrate python -m ontobot.security.cli grant-type --role user --type Facility"),
           sh("docker compose run --rm -T migrate python -m ontobot.security.cli grant-type --role user --type Facility 2>&1"))
    t.shot("cli-help", wsl("docker compose run --rm migrate python -m ontobot.security.cli --help"),
           sh("docker compose run --rm -T migrate python -m ontobot.security.cli --help 2>&1"))
    t.shot("create-user-cli", wsl("docker compose run --rm -it migrate python -m ontobot.security.cli create-user --username ops2 --display-name 운영자2 --role admin"),
           sh(f"docker compose run --rm -T -e ONTOBOT_NEW_PASSWORD='{password()}' migrate python -m ontobot.security.cli create-user --username ops2 --display-name 운영자2 --role admin 2>&1"))


# ---------------------------------------------------------------- 29장 설정 파일(실제 내용)


def ch_config(c: Ctx) -> None:
    t = c.t
    t.shot("app-env", "wsl -d ontobot -u root -- cat /opt/ontobot/app.env", sh("cat app.env"))
    t.shot("infra-env", "wsl -d ontobot -u root -- cat /opt/ontobot/.env", sh("cat .env"))
    t.shot("llm-yaml", "wsl -d ontobot -u root -- cat /opt/ontobot/config/llm.yaml", sh("cat config/llm.yaml"))
    t.shot("perm", "wsl -d ontobot -u root -- ls -l /opt/ontobot/.env /opt/ontobot/app.env /opt/ontobot/state.env", sh("ls -l .env app.env state.env"))
    t.shot("compose-yaml", "wsl -d ontobot -u root -- grep -n 'ports\\|8000\\|8100' /opt/ontobot/compose*.yaml",
           sh("grep -n 'ports:' -A1 compose.yaml compose.deployer.yaml"))
    t.shot("images-lock", "wsl -d ontobot -u root -- cat /opt/ontobot/releases/<버전>/images.lock", sh("cat releases/$(ls releases | head -1)/images.lock"))


# ---------------------------------------------------------------- 34장 정상 동작 질문 모음(답 + 트레이스)

GALLERY = [
    ("g01", "위험등급이 3 이상인 시설은 몇 개야?"),
    ("g02", "변전소 목록 보여줘"),
    ("g03", "위험등급이 가장 높은 시설 5개 알려줘"),
    ("g04", "시설 유형별 개수 알려줘"),
    ("g05", "F-0007 시설의 설비 용량은?"),
    ("g06", "2015년 이전에 설치된 철탑은?"),
    ("g07", "조직 목록 보여줘"),
    ("g08", "지역 코드가 11인 지역 이름은?"),
    ("g09", "안녕하세요"),
    ("g10", "용량이 1000kW 넘는 변전소의 평균 위험등급은?"),
    ("g11", "위험등급이 9인 시설 보여줘"),
    ("g12", "서울 지역 시설 목록 보여줘"),
    ("g13", "강남지사가 소유한 시설 목록 보여줘"),
    ("g14", "서울에 있는 변전소는?"),
    ("g15", "그 중 위험등급이 4 이상인 것만"),
    ("g16", "그 시설들의 설비 용량 합계는?"),
    ("g17", "시설별 담당 점검원 이름은?"),
    ("g18", "작년 정전 횟수가 가장 많은 지역은?"),
    ("g19", "F-9999 시설 위험등급을 3으로 바꿔줘"),
    ("g20", "수원지사 담당자 연락처 알려줘"),
]
CONTINUES = {"g15", "g16"}  # 앞 질문(g14)과 같은 대화에서


def latest_trace_id(username: str) -> int:
    out = subprocess.run(
        ["docker", "exec", "ontobot-postgres-1", "psql", "-U", "postgres", "-d", "ontobot_db", "-At", "-c",
         f"SELECT max(t.id) FROM trace t JOIN app_user u ON u.id = t.user_id WHERE u.username = '{username}'"],
        capture_output=True, text=True,
    ).stdout.strip()
    return int(out)


def ch_gallery(c: Ctx) -> None:
    s = c.s
    ids: dict[str, int] = {}
    with c.app("kim.user") as pg:
        for key, q in GALLERY:
            if key not in CONTINUES:
                go(pg, "/chat")
            sec = ask(pg, q)
            shot_turn(s, pg, f"{key}-answer")
            ids[key] = latest_trace_id("kim.user")
            print(f"    {key} {sec:.1f}s trace={ids[key]}", flush=True)
    with c.app() as pg:
        nav(pg, "관리")
        for key, _ in GALLERY:
            card = open_trace(pg, ids[key])
            s.shot(pg, f"{key}-trace", clip=card)


# ---------------------------------------------------------------- 35장 역할별로 보이는 화면

ROLE_VIEWS = [
    ("kim.user", ["채팅", "온톨로지", "데이터 소스", "Action"]),
    ("lee.modeler", ["채팅", "인터뷰", "온톨로지", "평가", "데이터 소스", "Action"]),
    ("choi.expert", ["채팅", "인터뷰", "온톨로지", "데이터 소스", "Action"]),
    ("jung.builder", ["채팅", "온톨로지", "평가", "데이터 소스", "Action"]),
    ("park.admin", ["채팅", "온톨로지", "데이터 소스", "Action", "관리"]),
]
MENU_PATH = {"채팅": "/chat", "인터뷰": "/workshop", "온톨로지": "/ontology", "평가": "/eval", "데이터 소스": "/sources",
             "Action": "/actions", "관리": "/admin"}


def ch_roles(c: Ctx) -> None:
    s = c.s
    for user, menus in ROLE_VIEWS:
        with c.app(user) as pg:
            s.shot(pg, f"{user}-topbar", clip=pg.locator("header.topbar"), pad=0)
            for m in menus:
                go(pg, MENU_PATH[m], wait=2500)
                s.shot(pg, f"{user}-{MENU_PATH[m].strip('/')}")


# ---------------------------------------------------------------- 36장 온톨로지 확인(운영자가 보는 것)


def ch_ontology(c: Ctx) -> None:
    s = c.s
    with c.app("lee.modeler") as pg:
        go(pg, "/ontology")
        s.shot(pg, "versions", marks=[(pg.locator("tbody tr").nth(0), 1), (pg.locator("tbody tr").nth(1), 2)])
        pg.locator("tbody tr", has_text="게시됨").get_by_role("link", name="열기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "published-top")
        s.shot(pg, "published-full", full=True)
        for h in ("객체 타입", "링크 타입", "Action"):
            sec = pg.locator("h2", has_text=h).first
            if sec.count():
                sec.scroll_into_view_if_needed()
                pg.wait_for_timeout(400)
                s.shot(pg, f"section-{h.replace(' ', '')}")
        diff = pg.locator("details.card", has_text="버전 비교").first
        diff.locator("summary").click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "diff", clip=diff)
        vid = pg.url.rstrip("/").split("/")[-1]
        yaml_text = pg.evaluate(f"fetch('/api/v1/ontology/versions/{vid}/export?format=yaml').then(r => r.text())")
        c.t.shot("export-yaml", "(브라우저의 내보내기: YAML 로 받은 파일의 앞부분)", text=yaml_text, keep=(60, 0),
                 title="내려받은 ontology.yaml", prompt="")
        go(pg, "/ontology")
        pg.locator("tbody tr", has_text="보관됨").first.get_by_role("link", name="열기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "archived-top")


# ---------------------------------------------------------------- 37장 동시 사용과 용량(실제 부하 측정)

LOAD_USER = "loadtest"


def ch_load(c: Ctx) -> None:
    s, t = c.s, c.t
    with c.app() as pg:
        nav(pg, "관리")
        if not _user_row(pg, LOAD_USER).count():
            box = _add_user(pg, LOAD_USER, "부하 측정", ["user"], password())
            box.locator("form").get_by_role("button", name="추가").click()
            pg.wait_for_timeout(1500)
    out = Path(os.environ.get("TEMP", ".")) / "ontobot-load.json"
    t.shot("stats-idle", wsl("docker stats --no-stream"),
           sh("docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | grep -E 'NAME|ontobot-(api|worker|ollama|postgres|neo4j)'"))
    env = os.environ | {"ONTOBOT_LOAD_PASSWORD": password(), "PYTHONUTF8": "1"}
    proc = subprocess.Popen(
        ["uv", "run", "--project", "backend", "python", "scripts/load_test.py", "--username", LOAD_USER,
         "--users", "1,4,8", "--rounds", "4", "--out", str(out)],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
    )
    time.sleep(70)
    t.shot("stats-busy", wsl("docker stats --no-stream"),
           sh("docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | grep -E 'NAME|ontobot-(api|worker|ollama|postgres|neo4j)'"))
    t.shot("gpu-busy", wsl("docker compose exec ollama nvidia-smi"), sh("docker compose exec -T ollama nvidia-smi 2>&1 | head -20"))
    with c.console() as pg:
        status_ready(pg)
        s.shot(pg, "console-during-load")
    text, _ = proc.communicate(timeout=3000)
    t.shot("load-result", "uv run --project backend python scripts/load_test.py --username loadtest --users 1,4,8 --rounds 4",
           text=text, title="개발팀 PC — 부하 측정", prompt="PS D:\\OntoBot> ")
    t.shot("trace-latency", wsl('docker compose exec -T postgres psql -U postgres -d ontobot_db -c "SELECT … stage_latency_ms …"'),
           psql_app("SELECT t.id, t.status, t.stage_latency_ms FROM trace t JOIN app_user u ON u.id=t.user_id"
                    " WHERE u.username='loadtest' ORDER BY t.id DESC LIMIT 12;"))


# ---------------------------------------------------------------- 배포 콘솔 추가 장면 — 잠금, 준비 확인 실패, 작업 겹침


def ch_console_more(c: Ctx) -> None:
    s, t = c.s, c.t
    s.dir = IMAGES / "17-console"
    with c.console(None) as pg:
        for _ in range(5):
            console_login(pg, "manual.capture", "틀린비밀번호입니다")
        console_login(pg, "manual.capture", "틀린비밀번호입니다")
        s.shot(pg, "login-locked", marks=[(".error", None)])
    # 준비 확인 실패 — AI 모델을 멈춘 채 업그레이드
    s.dir = IMAGES / "21-upgrade"
    t.dir = IMAGES / "21-upgrade"
    subprocess.run(sh("docker compose stop ollama"), capture_output=True)
    time.sleep(310)  # 잠금(같은 PC 5번 실패 → 5분)이 풀리길 기다린다
    with c.console("park.admin") as pg:
        status_ready(pg)
        console_tab(pg, "번들")
        card = bundle_card(pg, "0.1.0-2098e81ffd-m2")
        card.get_by_role("button", name="점검").click()
        pg.wait_for_timeout(1500)
        wait_job(pg, 300)
        console_tab(pg, "번들")
        pg.wait_for_timeout(5000)
        card = bundle_card(pg, "0.1.0-2098e81ffd-m2")
        card.get_by_role("button", name="업그레이드").click()
        card.locator("form input").fill("0.1.0-2098e81ffd-m2")
        card.locator("form").get_by_role("button", name="실행").click()
        pg.wait_for_timeout(8000)
        t.shot("lock-busy", "wsl -d ontobot -u root -- ontobotctl backup", ctl("backup"), expect_fail=True)
        print("    준비 확인 실패 업그레이드:", wait_job(pg, 600), flush=True)
        s.shot(pg, "not-ready-job", full=True)
        s.shot(pg, "not-ready-top")
        console_tab(pg, "현황")
        status_ready(pg)
        s.shot(pg, "not-ready-status")
    subprocess.run(sh("docker compose start ollama"), capture_output=True)
    wait_healthy("ollama")
    time.sleep(10)
    with c.console("park.admin") as pg:
        status_ready(pg)
        s.shot(pg, "not-ready-recovered")
        console_tab(pg, "롤백")
        pg.locator("main form").get_by_role("button", name="실행").click()
        pg.wait_for_timeout(1500)
        print("    롤백:", wait_job(pg, 600), flush=True)
    wait_healthy("api")


# ---------------------------------------------------------------- 19장 보강 — 백업이 정말 되살아나는지(별도 DB 에 풀어 본다)

RC_SQL = ("SELECT (SELECT count(*) FROM object) AS 객체, (SELECT count(*) FROM app_user) AS 계정,"
          " (SELECT count(*) FROM action_run) AS action, (SELECT count(*) FROM audit_log) AS 감사,"
          " to_char((SELECT max(ts) FROM audit_log) AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS 마지막_감사;")


def ch_restore_check(c: Ctx) -> None:
    t = c.t
    t.dir = IMAGES / "19-backup"
    t.shot("rc-backup", "wsl -d ontobot -u root -- ontobotctl backup --label restore-check", ctl("backup", "--label", "restore-check"))
    latest = subprocess.run(sh("ls -t backups | head -1"), capture_output=True, text=True).stdout.strip()
    t.shot("rc-create", wsl("docker compose exec -T postgres createdb -U postgres restore_check"),
           sh("docker compose exec -T postgres dropdb -U postgres --if-exists restore_check; docker compose exec -T postgres createdb -U postgres restore_check && echo '(만들었음)'"))
    t.shot("rc-restore", wsl(f"docker compose exec -T postgres pg_restore -U postgres --no-owner -d restore_check /backups/{latest}"),
           sh(f"docker compose exec -T postgres pg_restore -U postgres --no-owner -d restore_check /backups/{latest} 2>&1 | tail -5; echo \"(끝, 종료 코드 $?)\""))
    t.shot("rc-count-restored", wsl('docker compose exec -T postgres psql -U postgres -d restore_check -c "SELECT … 건수 …"'),
           psql_app(RC_SQL, db="restore_check"))
    t.shot("rc-count-live", wsl('docker compose exec -T postgres psql -U postgres -d ontobot_db -c "SELECT … 건수 …"'), psql_app(RC_SQL))
    t.shot("rc-drop", wsl("docker compose exec -T postgres dropdb -U postgres restore_check"),
           sh("docker compose exec -T postgres dropdb -U postgres restore_check && echo '(지웠음)'"))


# ---------------------------------------------------------------- 12장 보강 — 증분 모드를 실제로


def ch_incremental(c: Ctx) -> None:
    s, t = c.s, c.t
    s.dir = IMAGES / "12-mapping"
    t.dir = IMAGES / "12-mapping"
    src_sql(
        "DROP TABLE IF EXISTS region_master; CREATE TABLE region_master (region_code text primary key, region_name text not null,"
        " updated_at timestamptz not null default now());"
        " INSERT INTO region_master (region_code, region_name, updated_at) VALUES"
        " ('11','서울', now() - interval '2 day'), ('26','부산', now() - interval '2 day'), ('27','대구', now() - interval '2 day'),"
        " ('41','경기', now() - interval '2 day'), ('48','경남', now() - interval '2 day');"
        " GRANT SELECT ON region_master TO ontobot_reader;"
    )
    t.shot("inc-source", "psql … -c \"SELECT * FROM region_master\"",
           ["docker", "exec", "ontobot-postgres-1", "psql", "-U", "postgres", "-d", SRC_DB, "-c",
            "SELECT region_code, region_name, to_char(updated_at AT TIME ZONE 'Asia/Seoul','MM-DD HH24:MI') AS updated_at FROM region_master ORDER BY 1"],
           title="원천 DB 담당자 PC — psql", prompt="> ")
    with c.app("lee.modeler") as pg:
        go(pg, "/sources")
        add = pg.locator("details.card", has_text="매핑 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="데이터 소스").locator("select").select_option(label="시설 원장 DB")
        f.locator("label", has_text="객체 타입").locator("select").select_option("Region")
        f.locator("label", has_text="원천 표").locator("input").fill("region_master")
        f.get_by_role("button", name="열 불러오기").click()
        pg.wait_for_timeout(2000)
        for prop, col in {"region_code": "region_code", "name": "region_name"}.items():
            f.locator("tbody tr", has=pg.locator(f"code:text-is('{prop}')")).locator("select").select_option(col)
        f.locator("label", has_text="모드").locator("select").select_option("incremental")
        pg.wait_for_timeout(300)
        f.locator("label", has_text="워터마크 열").locator("select").select_option("updated_at")
        s.shot(pg, "inc-form", clip=add)
        f.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(2000)
        go(pg, "/sources")
        sync_and_wait(pg, "region_master")
        s.shot(pg, "inc-first", clip=_mapping(pg, "region_master"))
        sync_and_wait(pg, "region_master")
        s.shot(pg, "inc-nothing", clip=_mapping(pg, "region_master"))
        src_sql("UPDATE region_master SET region_name = '경상남도', updated_at = now() WHERE region_code = '48';")
        sync_and_wait(pg, "region_master")
        s.shot(pg, "inc-changed", clip=_mapping(pg, "region_master"))
        src_sql("DELETE FROM region_master WHERE region_code = '41';")
        sync_and_wait(pg, "region_master")
        s.shot(pg, "inc-deleted", clip=_mapping(pg, "region_master"))
    with c.app("kim.user") as pg:
        ask(pg, "지역 목록 보여줘")
        shot_turn(s, pg, "inc-answer")
    # 되돌리기 — 원래 이름, 지운 행 복구
    src_sql("UPDATE region_master SET region_name = '경남', updated_at = now() WHERE region_code = '48';"
            " INSERT INTO region_master VALUES ('41','경기', now());")
    with c.app("lee.modeler") as pg:
        go(pg, "/sources")
        sync_and_wait(pg, "region_master")
        s.shot(pg, "inc-restored", clip=_mapping(pg, "region_master"))


# ---------------------------------------------------------------- 14장 보강 — 사전 조건과 승인 순간의 재검증


def ch_preconditions(c: Ctx) -> None:
    s = c.s
    s.dir = IMAGES / "14-actions"
    with c.app("kim.user") as pg:
        panel = request_on_screen(pg, "request_inspection", "F-0001", {"긴급도": "일반"})
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "precondition-blocked", clip=panel)
        panel = request_on_screen(pg, "request_inspection", "F-0028", {"긴급도": "긴급"})
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "precondition-ok", clip=panel)
        panel = request_on_screen(pg, "adjust_risk_grade", "F-0028", {"새 위험등급": "2", "사유": "보강 공사 완료"})
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
    with c.app("park.admin") as pg:
        go(pg, "/actions")
        adj = run_card(pg, "F-0028", "위험등급 조정")
        _open(adj)
        adj.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(2500)
        go(pg, "/actions")
        ins = run_card(pg, "F-0028", "점검 요청")
        _open(ins)
        s.shot(pg, "revalidate-before", clip=ins)
        ins.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(2500)
        _tab(pg, 2)
        ins = run_card(pg, "F-0028", "점검 요청")
        _open(ins)
        s.shot(pg, "revalidate-failed", clip=ins)
        adj = run_card(pg, "F-0028", "위험등급 조정")
        _open(adj)
        adj.get_by_role("button", name="롤백").click()
        pg.wait_for_timeout(2500)
    print("    확인 창:", s.dialogs, flush=True)


# ---------------------------------------------------------------- 평가(골든셋)로 회귀 확인 — 업그레이드 뒤 정확도 재기


def ch_eval(c: Ctx) -> None:
    s, t = c.s, c.t
    t.shot("eval-run", wsl("docker compose run --rm -w /app migrate python scripts/run_eval.py --pack sample"),
           sh("docker compose run --rm -T -w /app migrate python scripts/run_eval.py --pack sample 2>&1"), keep=(30, 25), timeout=1800,
           expect_fail=True)
    with c.app("jung.builder") as pg:
        go(pg, "/eval", wait=2000)
        s.shot(pg, "eval-page")
        link = pg.locator("aside nav.chat-list a").first
        if link.count():
            link.click()
            pg.wait_for_timeout(2000)
        s.shot(pg, "eval-set", full=True)
        opn = pg.get_by_role("link", name="열기").first
        if opn.count():
            opn.click()
            pg.wait_for_timeout(2500)
            s.shot(pg, "eval-run-view", full=True)
            s.shot(pg, "eval-run-top")


# ---------------------------------------------------------------- 10장 보강 — 실패 종류별 트레이스


def ch_eval_view(c: Ctx) -> None:
    s = c.s
    s.dir = IMAGES / "eval"
    with c.app("jung.builder") as pg:
        go(pg, "/eval", wait=2000)
        pg.locator("aside nav.chat-list a").first.click()
        pg.wait_for_timeout(2000)
        pg.get_by_role("link", name="보기").first.click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "eval-run-top")
        s.shot(pg, "eval-run-view", full=True)


def ch_trace_kinds(c: Ctx) -> None:
    s = c.s
    s.dir = IMAGES / "10-trace"
    rows = trace_ids()
    want = {}
    for i, st, fr in rows:
        if st == "failed" and fr and fr not in want:
            want[fr] = i
    want.setdefault("empty", next((i for i, st, _ in rows if st == "empty"), 0))
    print("    ", want, flush=True)
    with c.app() as pg:
        nav(pg, "관리")
        for kind, tid in want.items():
            if not tid:
                continue
            card = open_trace(pg, tid)
            s.shot(pg, f"kind-{kind}", clip=card)


# ---------------------------------------------------------------- 34장 보강 — 근거 카드


def ch_gallery_cards(c: Ctx) -> None:
    s = c.s
    s.dir = IMAGES / "34-gallery"
    picks = [("g05", "F-0007 시설의 설비 용량은?"), ("g08", "지역 코드가 11인 지역 이름은?"),
             ("g10", "용량이 1000kW 넘는 변전소의 평균 위험등급은?"), ("g13", "강남지사가 소유한 시설 목록 보여줘"),
             ("g20", "수원지사 담당자 연락처 알려줘"), ("g01", "위험등급이 3 이상인 시설은 몇 개야?")]
    with c.app("kim.user") as pg:
        for key, q in picks:
            go(pg, "/chat")
            ask(pg, q)
            btn = last_answer(pg).locator(".citations button").first
            if btn.count():
                btn.click()
                pg.wait_for_timeout(1500)
                card = pg.locator(".object-card, .aggregate-card, .card", has_text="근거").last
                s.shot(pg, f"{key}-card", clip=card if card.count() else pg.locator("main"))


# ---------------------------------------------------------------- 사진 다시 찍기(앞 장에서 상태가 덜 잡힌 것)


# ---------------------------------------------------------------- 39~40장 서버 들여다보기(자원·AI 모델·DB·관계 저장소)


def _busy(c: Ctx, question: str, scripts: dict[str, str], delay: float = 3.0) -> dict[str, str]:
    """kim.user 가 질문하는 동안 ``scripts`` 를 실행해 출력을 모은다(답을 쓰는 중의 자원 사용)."""
    import threading

    out: dict[str, str] = {}

    def probe() -> None:
        time.sleep(delay)
        for k, script in scripts.items():
            out[k] = run_text(sh(script))

    th = threading.Thread(target=probe)
    with c.app("kim.user") as pg:
        th.start()
        ask(pg, question)
    th.join()
    return out


def run_text(real: list[str]) -> str:
    from term import run

    return run(real)[1]


def ch_resources(c: Ctx) -> None:
    t = c.t
    t.shot("ps", wsl("docker compose ps"), sh("docker compose ps 2>&1"))
    t.shot("uptime", "wsl -d ontobot -u root -- bash -c \"nproc; cat /proc/loadavg; grep -E 'MemTotal|MemAvailable|SwapTotal' /proc/meminfo\"",
           sh("nproc; cat /proc/loadavg; grep -E 'MemTotal|MemAvailable|SwapTotal' /proc/meminfo"))
    t.shot("stats-idle", "wsl -d ontobot -u root -- docker stats --no-stream", sh("docker stats --no-stream"))
    t.shot("df", "wsl -d ontobot -u root -- df -h /opt/ontobot /var/lib/docker", sh('df -h "$ONTOBOT_HOME" /var/lib/docker 2>/dev/null || df -h "$ONTOBOT_HOME"'))
    t.shot("du", "wsl -d ontobot -u root -- du -sh /opt/ontobot/backups /opt/ontobot/bundles /opt/ontobot/releases /opt/ontobot/logs",
           sh("du -sh backups bundles releases logs 2>&1"))
    t.shot("system-df", "wsl -d ontobot -u root -- docker system df", sh("docker system df"))
    t.shot("volumes", "wsl -d ontobot -u root -- docker system df -v", sh("docker system df -v 2>&1 | sed -n '/Local Volumes space usage/,/Build cache usage/p' | sed '$d'"))
    t.shot("images", "wsl -d ontobot -u root -- docker images", sh("docker images"))
    t.shot("dangling", "wsl -d ontobot -u root -- docker images --filter dangling=true", sh("docker images --filter dangling=true; echo '(끝)'"))
    t.shot("gpu-idle", wsl("docker compose exec ollama nvidia-smi"), sh("docker compose exec -T ollama nvidia-smi 2>&1"))
    t.shot("ollama-list", wsl("docker compose exec ollama ollama list"), sh("docker compose exec -T ollama ollama list 2>&1"))
    t.shot("ollama-show", wsl("docker compose exec ollama ollama show gemma4:e4b"), sh("docker compose exec -T ollama ollama show gemma4:e4b 2>&1"))
    sh_quiet = sh("docker compose exec -T ollama ollama stop gemma4:e4b 2>&1; true")
    run_text(sh_quiet)
    t.shot("ollama-ps-idle", wsl("docker compose exec ollama ollama ps"), sh("docker compose exec -T ollama ollama ps 2>&1"))
    busy = _busy(c, "변전소 목록 보여줘", {
        "stats": "docker stats --no-stream",
        "gpu": "docker compose exec -T ollama nvidia-smi 2>&1",
        "ps": "docker compose exec -T ollama ollama ps 2>&1",
    })
    t.shot("stats-busy", "wsl -d ontobot -u root -- docker stats --no-stream", text=busy["stats"])
    t.shot("gpu-busy", wsl("docker compose exec ollama nvidia-smi"), text=busy["gpu"])
    t.shot("ollama-ps-busy", wsl("docker compose exec ollama ollama ps"), text=busy["ps"])
    t.shot("gpu-query", wsl("docker compose exec ollama nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv"),
           sh("docker compose exec -T ollama nvidia-smi --query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total --format=csv 2>&1"))
    t.shot("healthz", wsl("docker compose exec api python -c \\\"import urllib.request as u; print(u.urlopen('http://localhost:8000/healthz').read().decode())\\\""),
           sh("docker compose exec -T api python -c \"import urllib.request as u; print(u.urlopen('http://localhost:8000/healthz').read().decode())\" 2>&1"))
    t.shot("readyz", wsl("docker compose exec api python -c \\\"import urllib.request as u; print(u.urlopen('http://localhost:8000/readyz').read().decode())\\\""),
           sh("docker compose exec -T api python -c \"import urllib.request as u, json; print(json.dumps(json.loads(u.urlopen('http://localhost:8000/readyz').read()), ensure_ascii=False, indent=2))\" 2>&1"))
    t.shot("restarts", "wsl -d ontobot -u root -- docker inspect -f \"{{.Name}} 재시작={{.RestartCount}} 시작={{.State.StartedAt}}\" $(docker ps -q)",
           sh("docker inspect -f '{{.Name}} 재시작={{.RestartCount}} 시작={{.State.StartedAt}}' $(docker ps -q)"))
    t.shot("log-config", "wsl -d ontobot -u root -- docker inspect -f \"{{.Name}} {{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}}\" $(docker ps -q)",
           sh("docker inspect -f '{{.Name}} {{.HostConfig.LogConfig.Type}} {{.HostConfig.LogConfig.Config}}' $(docker ps -q)"))


PSQL_RO = "docker compose exec -T -e PGOPTIONS='-c default_transaction_read_only=on' postgres psql -U postgres -d ontobot_db -X -q -P pager=off -f -"
PSQL_PROMPT = "ontobot_db=# "
PSQL_TITLE = "psql — ontobot_db (읽기 전용)"

DB_QUERIES: list[tuple[str, str]] = [
    ("db-readonly", "SHOW default_transaction_read_only;"),
    ("db-version", "SELECT version_num AS 스키마_버전 FROM alembic_version;\nSELECT pg_size_pretty(pg_database_size('ontobot_db')) AS DB_크기;"),
    ("db-tables", "SELECT relname AS 표, n_live_tup AS 행수, pg_size_pretty(pg_total_relation_size(relid)) AS 크기\n"
                  "  FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 15;"),
    ("db-objects", "SELECT type AS 타입, count(*) FILTER (WHERE deleted_at IS NULL) AS 살아있음,\n"
                   "       count(*) FILTER (WHERE deleted_at IS NOT NULL) AS 삭제표시,\n"
                   "       to_char(max(synced_at) AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS 마지막_동기화\n"
                   "  FROM object GROUP BY type ORDER BY type;"),
    ("db-links", "SELECT link_type AS 링크, count(*) FILTER (WHERE deleted_at IS NULL) AS 살아있음,\n"
                 "       count(*) FILTER (WHERE deleted_at IS NOT NULL) AS 삭제표시\n  FROM link GROUP BY link_type;"),
    ("db-ontology", "SELECT version_no AS 버전, status AS 상태,\n"
                    "       to_char(published_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD HH24:MI') AS 게시\n"
                    "  FROM ontology_version ORDER BY version_no;"),
    ("db-sync", "SELECT r.id, s.name AS 데이터소스, m.object_type AS 타입, r.status AS 상태,\n"
                "       r.rows_read AS 읽음, r.rows_upserted AS 반영, r.rows_deleted AS 삭제,\n"
                "       to_char(r.started_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS 시작,\n"
                "       round(extract(epoch FROM r.finished_at - r.started_at)::numeric, 1) AS 초\n"
                "  FROM sync_run r JOIN mapping m ON m.id = r.mapping_id JOIN data_source s ON s.id = m.source_id\n"
                " ORDER BY r.id DESC LIMIT 12;"),
    ("db-sync-failed", "SELECT r.id, m.object_type AS 타입, left(r.error, 70) AS 오류\n"
                       "  FROM sync_run r JOIN mapping m ON m.id = r.mapping_id\n WHERE r.status = 'failed' ORDER BY r.id DESC LIMIT 5;"),
    ("db-jobs", "SELECT kind AS 종류, status AS 상태, count(*) AS 건수, max(attempts) AS 최대시도\n"
                "  FROM job GROUP BY kind, status ORDER BY kind, status;"),
    ("db-jobs-waiting", "SELECT id, kind AS 종류, status AS 상태, attempts AS 시도,\n"
                        "       to_char(run_after AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS 실행예정, left(error, 50) AS 오류\n"
                        "  FROM job WHERE status NOT IN ('succeeded') ORDER BY id DESC LIMIT 10;"),
    ("db-outbox", "SELECT count(*) FILTER (WHERE processed_at IS NULL) AS 투영_대기,\n"
                  "       count(*) AS 전체,\n"
                  "       to_char(max(processed_at) AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS') AS 마지막_투영\n  FROM graph_outbox;"),
    ("db-delivery", "SELECT channel AS 채널, status AS 상태, count(*) AS 건수, max(attempts) AS 최대시도\n"
                    "  FROM notification_delivery GROUP BY channel, status ORDER BY channel, status;"),
    ("db-actions", "SELECT action_type AS Action, status AS 상태, origin AS 출처, count(*) AS 건수\n"
                   "  FROM action_run GROUP BY action_type, status, origin ORDER BY action_type, status;"),
    ("db-edits", "SELECT type AS 타입, pk, property AS 속성, value AS 편집값, base_source_value AS 원천값,\n"
                 "       to_char(edited_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS 편집\n"
                 "  FROM object_edit WHERE cleared_at IS NULL ORDER BY edited_at DESC LIMIT 10;"),
    ("db-users", "SELECT u.username AS 아이디, u.status AS 상태, string_agg(r.name, ',' ORDER BY r.name) AS 역할\n"
                 "  FROM app_user u LEFT JOIN user_role ur ON ur.user_id = u.id LEFT JOIN role r ON r.id = ur.role_id\n"
                 " GROUP BY u.username, u.status ORDER BY u.username;"),
    ("db-sessions", "SELECT u.username AS 아이디, s.auth_method AS 방식, count(*) AS 세션수,\n"
                    "       to_char(max(s.expires_at) AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS 만료\n"
                    "  FROM user_session s JOIN app_user u ON u.id = s.user_id\n"
                    " WHERE s.revoked_at IS NULL AND s.expires_at > now() GROUP BY u.username, s.auth_method ORDER BY 1;"),
    ("db-audit", "SELECT event AS 이벤트, count(*) AS 건수\n  FROM audit_log WHERE ts > now() - interval '7 days'\n"
                 " GROUP BY event ORDER BY count(*) DESC LIMIT 20;"),
    ("db-trace-status", "SELECT status AS 상태, coalesce(failure_reason, '-') AS 실패이유, count(*) AS 건수\n"
                        "  FROM trace GROUP BY status, failure_reason ORDER BY count(*) DESC;"),
    ("db-trace-grade", "SELECT coalesce(grade::text, '-') AS 등급, count(*) AS 건수,\n"
                       "       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS 비율\n"
                       "  FROM trace WHERE status = 'ok' GROUP BY grade ORDER BY grade;"),
    ("db-trace-latency", "SELECT to_char(created_at AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24') AS 시간대, count(*) AS 질문,\n"
                         "       round(avg((stage_latency_ms->>'planning')::int)) AS 계획_ms,\n"
                         "       round(avg((stage_latency_ms->>'answering')::int)) AS 답변_ms\n"
                         "  FROM trace GROUP BY 1 ORDER BY 1 DESC LIMIT 10;"),
    ("db-activity", "SELECT coalesce(nullif(application_name, ''), '(이름 없음)') AS 프로그램, usename AS 계정,\n"
                    "       state AS 상태, count(*) AS 연결\n  FROM pg_stat_activity WHERE datname = 'ontobot_db'\n"
                    " GROUP BY 1, 2, 3 ORDER BY 4 DESC;"),
    ("db-long", "SELECT pid, usename AS 계정, state AS 상태, now() - xact_start AS 트랜잭션_경과, left(query, 40) AS 질의\n"
                "  FROM pg_stat_activity WHERE datname = 'ontobot_db' AND xact_start IS NOT NULL\n"
                " ORDER BY xact_start LIMIT 5;"),
]


def ch_database(c: Ctx) -> None:
    t = c.t
    t.shot("db-enter", wsl("docker compose exec -e PGOPTIONS='-c default_transaction_read_only=on' postgres psql -U postgres -d ontobot_db"),
           sh("docker compose exec -T postgres psql -U postgres -d ontobot_db -X -c '\\conninfo' 2>&1"))
    for name, sql in DB_QUERIES:
        t.shot(name, sql.split("\n"), sh(PSQL_RO + " 2>&1"), stdin=sql + "\n", title=PSQL_TITLE, prompt=PSQL_PROMPT)
    t.shot("db-readonly-try", "UPDATE app_user SET display_name = display_name WHERE false;", sh(PSQL_RO + " 2>&1"),
           stdin="UPDATE app_user SET display_name = display_name WHERE false;\n", expect_fail=True, title=PSQL_TITLE, prompt=PSQL_PROMPT)
    cy = "docker compose exec -T neo4j sh -c 'cypher-shell -u neo4j -p \"${NEO4J_AUTH#neo4j/}\" --format plain'"
    cy_shown = wsl("docker compose exec neo4j cypher-shell -u neo4j")
    t.shot("neo4j-enter", cy_shown, text="password: ****\nConnected to Neo4j using Bolt protocol at neo4j://localhost:7687 as user neo4j.\nType :help for a list of available commands or :exit to exit the shell.\nNote that Cypher queries must end with a semicolon.")
    for name, q in [
        ("neo4j-labels", "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS nodes, sum(CASE WHEN n.stub THEN 1 ELSE 0 END) AS stubs ORDER BY label;"),
        ("neo4j-rels", "MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS n ORDER BY rel;"),
        ("neo4j-keys", "MATCH (n) UNWIND keys(n) AS k RETURN labels(n)[0] AS label, collect(DISTINCT k) AS keys ORDER BY label;"),
        ("neo4j-sample", "MATCH (f:Facility)-[r]->(o) RETURN f.pk, f.title, type(r), o.pk, o.title ORDER BY f.pk LIMIT 8;"),
        ("neo4j-orphans", "MATCH (n) WHERE NOT (n)--() RETURN labels(n)[0] AS label, count(*) AS isolated;"),
    ]:
        t.shot(name, q, sh(cy + " 2>&1"), stdin=q + "\n", title="cypher-shell — neo4j", prompt="neo4j@neo4j> ")
    t.shot("graph-check", wsl("docker compose run --rm migrate python scripts/graph_rebuild.py --check"),
           sh("docker compose run --rm -T -w /app migrate python scripts/graph_rebuild.py --check 2>&1"), keep=(30, 12))


SEC_QUERIES: list[tuple[str, str]] = [
    ("sec-admins", "SELECT u.username AS 아이디, u.status AS 상태, to_char(u.created_at AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD') AS 만든날\n"
                   "  FROM app_user u JOIN user_role ur ON ur.user_id = u.id JOIN role r ON r.id = ur.role_id\n"
                   " WHERE r.name = 'admin' ORDER BY u.username;"),
    ("sec-login-failed", "SELECT to_char(ts AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI:SS') AS 시각, payload->>'username' AS 아이디\n"
                         "  FROM audit_log WHERE event = 'auth.login_failed' AND ts > now() - interval '30 days'\n ORDER BY ts DESC;"),
    ("sec-masked", "SELECT event AS 이벤트, payload AS 기록된_값\n  FROM audit_log WHERE event = 'security.user_contacts_changed'\n ORDER BY id DESC LIMIT 3;"),
    ("sec-perm-changes", "SELECT to_char(a.ts AT TIME ZONE 'Asia/Seoul', 'MM-DD HH24:MI') AS 시각, u.username AS 한사람, a.target AS 대상\n"
                         "  FROM audit_log a LEFT JOIN app_user u ON u.id = a.actor_id\n"
                         " WHERE a.event IN ('security.permission_changed', 'security.role_changed', 'security.user_created')\n"
                         " ORDER BY a.id DESC LIMIT 10;"),
]


def ch_security(c: Ctx) -> None:
    t = c.t
    t.shot("sec-ports", "wsl -d ontobot -u root -- docker ps --format \"{{.Names}}  {{.Ports}}\"", sh("docker ps --format '{{.Names}}  {{.Ports}}'"))
    t.shot("sec-app-env", "wsl -d ontobot -u root -- grep -nE \"SESSION|COOKIE|ORIGINS|RETENTION\" /opt/ontobot/app.env",
           sh("grep -nE 'SESSION|COOKIE|ORIGINS|RETENTION' app.env"))
    t.shot("sec-keys", "wsl -d ontobot -u root -- bash -c \"ls -l /opt/ontobot/trusted-keys; cat /opt/ontobot/trusted-keys/*.pub\"",
           sh("ls -l trusted-keys; cat trusted-keys/*.pub"))
    for name, sql in SEC_QUERIES:
        t.shot(name, sql.split("\n"), sh(PSQL_RO + " 2>&1"), stdin=sql + "\n", title=PSQL_TITLE, prompt=PSQL_PROMPT)
    t.shot("sec-log-pii", wsl("docker compose logs api worker | grep -cE '01[0-9]-[0-9]{3,4}-[0-9]{4}'"),
           sh("docker compose logs api worker 2>&1 | grep -cE '01[0-9]-[0-9]{3,4}-[0-9]{4}'; true"))


def ch_fixes2(c: Ctx) -> None:
    """배포 콘솔 멈춤·시작 명령 화면 — 실제로 쓴 docker stop/start 명령으로 다시 그린다(출력은 컨테이너 이름 한 줄)."""
    t = c.t
    t.dir = IMAGES / "27-incidents"
    t.shot("deployer-stop", f"wsl -d ontobot -u root -- docker stop {DEPLOYER_C}", text=DEPLOYER_C)
    t.shot("deployer-start", f"wsl -d ontobot -u root -- docker start {DEPLOYER_C}", text=DEPLOYER_C)


def ch_fixes(c: Ctx) -> None:
    s, t = c.s, c.t
    # readyz 를 보기 좋게
    s.dir = IMAGES / "05-verify"
    with c.app(None, viewport={"width": 1000, "height": 300}) as pg:
        for name, url in [("healthz", BASE + "/healthz"), ("readyz", BASE + "/readyz"), ("deployer-healthz", DEPLOY + "/healthz")]:
            pg.goto(url)
            pg.wait_for_timeout(600)
            box = pg.locator("input[type=checkbox]")
            if box.count():
                box.first.check()
                pg.wait_for_timeout(300)
            s.shot(pg, name)
    t.dir = IMAGES / "05-verify"
    for name, url in [("curl-healthz", "http://localhost:8000/healthz"), ("curl-readyz", "http://localhost:8000/readyz"),
                      ("curl-deployer", "http://localhost:8100/healthz")]:
        t.shot(name, f"curl.exe -s {url.replace('localhost', 'ontobot.corp.local')}", ["curl.exe", "-s", url],
               title="Windows PowerShell — 관리자 PC", prompt=r"PS C:\Users\admin> ")
    # 깨끗한 메일 수신 기록 — 새 Action 하나로
    s.dir = IMAGES / "15-notify"
    t.dir = IMAGES / "15-notify"
    subprocess.run(["docker", "rm", "-f", MAILSINK], capture_output=True)
    subprocess.run(
        ["docker", "run", "-d", "--name", MAILSINK, "--network", "ontobot_default", "--entrypoint", "python", app_image(), "-u", "-c", MAILSINK_PY],
        check=True, capture_output=True,
    )
    time.sleep(3)
    with c.app("kim.user") as pg:
        panel = request_on_screen(pg, "urgent_notice", "F-0027", {"통보 내용": "애자 파손 확인"})
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
    with c.app("park.admin") as pg:
        go(pg, "/actions")
        cd = run_card(pg, "F-0027", "긴급 정비 통보")
        _open(cd)
        cd.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(2500)
        for _ in range(40):
            pg.wait_for_timeout(3000)
            go(pg, "/actions")
            _tab(pg, 2)
            cd = run_card(pg, "F-0027", "긴급 정비 통보")
            _open(cd)
            if "대기" not in cd.inner_text():
                break
        s.shot(pg, "deliveries-sent-2", clip=cd)
    t.shot("mailsink-log", f"(사내 메일 서버의 받은 편지 — 촬영 PC 의 시험용 수신기 로그) docker logs {MAILSINK}", ["docker", "logs", MAILSINK],
           title="메일 서버 관리자 화면", prompt="$ ")
    t.shot("ums-rows-2", "(UMS 담당자) SELECT * FROM ums_send;",
           ["docker", "exec", "ontobot-postgres-1", "psql", "-U", "postgres", "-d", SRC_DB, "-c",
            "SELECT id, dest_phone, msg_text, callback, to_char(created_at AT TIME ZONE 'Asia/Seoul','HH24:MI:SS') AS 시각 FROM ums_send ORDER BY id"],
           title="UMS 담당자 PC — psql", prompt="> ")
    print("    확인 창:", s.dialogs, flush=True)


CHAPTERS: dict[str, Callable[[Ctx], None]] = {
    "02-structure": ch_structure,
    "04-bundle": ch_bundle,
    "05-verify": ch_verify,
    "06-web": ch_web,
    "07-users": ch_users,
    "08-perms": ch_perms,
    "10-trace": ch_trace,
    "11-sources": ch_sources,
    "12-mapping": ch_mapping,
    "13-graph": ch_graph,
    "14-actions": ch_actions,
    "15-notify": ch_notify,
    "16-conflicts": ch_conflicts,
    "17-console": ch_console,
    "18-status": ch_status,
    "19-backup": ch_backup,
    "20-bundles": ch_bundles,
    "21-upgrade": ch_upgrade,
    "23-deployer-update": ch_deployer_update,
    "23-deployer-after": ch_deployer_after,
    "22-rollback": ch_rollback,
    "22-restore": ch_restore_demo,
    "24-jobs": ch_jobs,
    "09-audit": ch_audit,
    "26-logs": ch_logs,
    "27-incidents": ch_incidents,
    "28-cli": ch_cli,
    "29-config": ch_config,
    "fixes": ch_fixes,
    "fixes2": ch_fixes2,
    "34-gallery": ch_gallery,
    "35-roles": ch_roles,
    "36-ontology": ch_ontology,
    "37-load": ch_load,
    "console-more": ch_console_more,
    "19-restore-check": ch_restore_check,
    "12-incremental": ch_incremental,
    "14-preconditions": ch_preconditions,
    "eval": ch_eval,
    "10-kinds": ch_trace_kinds,
    "34-cards": ch_gallery_cards,
    "eval-view": ch_eval_view,
    "39-resources": ch_resources,
    "40-database": ch_database,
    "31-security": ch_security,
}


def main(only: list[str]) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        term_page = browser.new_page(device_scale_factor=1)
        try:
            for key, fn in CHAPTERS.items():
                if only and key not in only:
                    continue
                print(f"== {key}", flush=True)
                fn(Ctx(browser, term_page, key))
        finally:
            browser.close()
    print("BASE", BASE)


if __name__ == "__main__":
    main(sys.argv[1:])
