"""사용자 매뉴얼 화면 촬영 — 장(章)마다 실제 화면을 순서대로 조작하며 찍는다.

    # 매뉴얼 환경(manual_env.py prepare)을 가리키는 API(:8000)·워커·웹(:5173)이 떠 있어야 한다.
    ONTOBOT_MANUAL_PASSWORD=... uv run --with playwright python docs/manual/tools/capture_user.py [장 ...]

장은 앞 장이 만든 상태(대화·요청·버전)에 기대므로 처음부터 다시 찍을 때는 환경을 새로 준비하고 순서대로
돌린다. AI 답은 실행할 때마다 문장이 조금씩 다를 수 있다 — 다시 찍으면 본문의 인용 문장을 확인할 것.
"""

from __future__ import annotations

import sys

import subprocess
import time

from shots import Shooter, ask, go, last_answer, run, shot_turn

# ---------------------------------------------------------------- 1. 로그인


def ch_login(s: Shooter) -> None:
    with s.as_user(None) as pg:
        go(pg, "/login")
        s.shot(pg, "login-empty")
        s.shot(
            pg,
            "login-marked",
            marks=[("label:has-text('아이디') input", 1), ("input[type=password]", 2), ("button[type=submit]", 3)],
        )
        pg.get_by_label("아이디").fill("kim.user")
        pg.get_by_label("비밀번호").fill("wrong-password-1")
        s.shot(pg, "login-filled", marks=[("button[type=submit]", None)])
        pg.get_by_role("button", name="로그인").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "login-wrong-password", marks=[(".error, [role=alert]", None)])
        go(pg, "/chat")
        s.shot(pg, "redirect-to-login")
    with s.as_user("kim.user") as pg:
        s.shot(pg, "after-login")
        s.shot(pg, "logout-button", marks=[("header button.link", 1, "right")])
        pg.locator("header button.link").click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "after-logout")


# ---------------------------------------------------------------- 2. 화면 구성


def ch_layout(s: Shooter) -> None:
    for user in ("kim.user", "choi.expert", "lee.modeler", "jung.builder", "park.admin"):
        with s.as_user(user) as pg:
            s.shot(pg, f"topbar-{user}", clip=pg.locator("header.topbar"), pad=0)
            if user == "park.admin":
                links = pg.locator("header nav a")
                marks = [(links.nth(i), i + 1, "below") for i in range(links.count())]
                s.shot(pg, "topbar-numbered", marks=[*marks, ("header .muted", len(marks) + 1, "below")])
    with s.as_user("kim.user") as pg:
        s.shot(
            pg,
            "chat-areas",
            marks=[(".chat-list", 1), (".chat-thread h1", 2), (".chat-thread .notice", 3), (".chat-input", 4)],
        )
        go(pg, "/ontology")
        s.shot(pg, "user-ontology")
        go(pg, "/sources")
        s.shot(pg, "user-sources", full=True)
        go(pg, "/eval")
        s.shot(pg, "user-eval-forbidden")
        go(pg, "/admin")
        s.shot(pg, "user-admin-forbidden")
    with s.as_user("lee.modeler", viewport={"width": 900, "height": 700}) as pg:
        s.shot(pg, "narrow-window")


# ---------------------------------------------------------------- 3. 첫 질문


def ch_first(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        s.shot(pg, "empty-chat", marks=[(".chat-input textarea", 1), (".chat-input button", 2, "right")])
        box = pg.locator(".chat-input textarea")
        box.fill("위험등급이 3 이상인 시설은 몇 개야?")
        s.shot(pg, "typed", marks=[(".chat-input button", 1, "right")])
        box.press("Enter")
        seen: set[str] = set()
        for _ in range(400):
            stage = pg.locator(".chat-thread .stage")
            if stage.count():
                text = stage.first.inner_text()
                key = text.strip()[:6]
                if key and key not in seen:
                    seen.add(key)
                    s.shot(pg, f"stage-{len(seen)}", marks=[(".chat-thread .stage", None)])
            if not pg.locator(".chat-input textarea[disabled]").count():
                break
            pg.wait_for_timeout(150)
        pg.wait_for_timeout(800)
        ans = last_answer(pg)
        s.shot(pg, "first-answer")
        s.shot(
            pg,
            "first-answer-parts",
            marks=[
                (ans.locator(".conditions"), 1),
                (ans.locator(".answer-text"), 2),
                (ans.locator(".badge").first, 3),
                (ans.locator(".citations"), 4, "right"),
            ],
            clip=ans,
            pad=40,
        )
        s.shot(pg, "conversation-list", marks=[(".chat-list a", 1, "right")])


# ---------------------------------------------------------------- 4. 답 읽기


def ch_reading(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "변전소 목록 보여줘")
        ans = last_answer(pg)
        s.shot(pg, "list-answer", full=True)
        s.shot(pg, "list-conditions", clip=ans.locator(".conditions"), pad=16)
        s.shot(pg, "list-grade", clip=ans.locator(".row").last, pad=10)
        chip = ans.locator(".citations button").first
        chip.click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "card-open", marks=[(chip, 1), (".object-card", 2, "right")], full=True)
        card = pg.locator(".object-card")
        s.shot(pg, "card-detail", clip=card, pad=16)
        s.shot(pg, "card-close", marks=[(card.locator("button.link").first, 1, "right")])
        card.locator("button.link").first.click()
        go(pg, "/chat")
        ask(pg, "부산 철탑 02호의 위험등급과 설치일 알려줘")
        ans = last_answer(pg)
        s.shot(pg, "single-answer", clip=ans, pad=12)
        ans.locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "single-card-bold", clip=pg.locator(".object-card"), pad=16)
        s.shot(pg, "single-with-card")
        go(pg, "/chat")
        ask(pg, "시설 유형별 개수 알려줘")
        ans = last_answer(pg)
        s.shot(pg, "aggregate-answer", clip=ans, pad=12)
        ans.locator(".citations button").first.click()
        pg.wait_for_timeout(1200)
        s.shot(pg, "aggregate-card", marks=[(".object-card", 1, "right")])


# ---------------------------------------------------------------- 5. 질문 유형별 실습

QUESTION_TYPES: list[tuple[str, list[str]]] = [
    ("count", ["철탑은 몇 개 있어?", "위험등급이 5인 전주는 몇 개야?"]),
    ("list", ["철탑 목록 보여줘", "위험등급이 5인 시설 목록 알려줘"]),
    ("top", ["위험등급이 가장 높은 시설 5개 알려줘", "설비 용량이 큰 순서로 변전소 3개 보여줘"]),
    ("group", ["위험등급별 시설 수 알려줘", "지역 코드별 시설 수 알려줘"]),
    ("single", ["F-0007 시설의 설비 용량은?", "강남 변전소 13호 정보 알려줘"]),
    ("date", ["2010년 이전에 설치된 철탑은?", "2020년 이후에 설치된 시설 목록 보여줘"]),
    ("stat", ["변전소의 평균 설비 용량은?", "철탑 설비 용량 합계는?", "용량이 1000kW 넘는 변전소의 평균 위험등급은?"]),
    ("link", ["강남지사가 소유한 시설 목록 보여줘", "서울에 있는 변전소는?", "부산지사가 소유한 시설 중 위험등급이 4 이상인 것은?"]),
    ("other-types", ["조직 목록 보여줘", "지역 목록 알려줘", "강남지사 담당자 연락처 알려줘"]),
    ("combined", ["경기 지역 철탑 중 위험등급이 4 이상인 것 보여줘", "위험등급 3 이상인 변전소의 평균 설비 용량은?"]),
]


def ch_types(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        for kind, questions in QUESTION_TYPES:
            go(pg, "/chat")
            for i, q in enumerate(questions, 1):
                took = ask(pg, q)
                print(f"    {q} — {took:.0f}s", flush=True)
                shot_turn(s, pg, f"{kind}-{i}")


# ---------------------------------------------------------------- 6. 이어서 묻기와 대화 관리


def ch_followup(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "부산지사가 소유한 시설 목록 보여줘")
        shot_turn(s, pg, "base-list")
        ask(pg, "그 중 위험등급이 4 이상인 것만")
        shot_turn(s, pg, "narrow", marks=[(last_answer(pg).locator(".conditions"), 1)])
        ask(pg, "그 시설들의 설비 용량 합계는?")
        shot_turn(s, pg, "sum-of-those")
        ask(pg, "그 중 철탑만")
        shot_turn(s, pg, "after-number")
        s.shot(pg, "whole-conversation", full=True)
        s.shot(pg, "list-current", marks=[(".chat-list a.active", 1, "right")])
        s.shot(pg, "new-conversation-button", marks=[(".chat-list button", 1, "right")])
        pg.locator(".chat-list button").click()
        pg.wait_for_timeout(1000)
        s.shot(pg, "new-conversation-empty")
        pg.locator(".chat-list a").nth(3).click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "reopen-old", marks=[(".chat-list a.active", 1, "right")])
        go(pg, "/chat")
        ask(pg, "그 중 위험등급이 4 이상인 것만")
        shot_turn(s, pg, "no-context")


# ---------------------------------------------------------------- 7. 답이 이상할 때


def ch_failures(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        for name, q in [
            ("plan-invalid", "시설 점검 담당자 이름 알려줘"),
            ("empty", "경남에 있는 시설 알려줘"),
            ("empty-2", "위험등급이 9인 시설 보여줘"),
            ("not-query", "안녕하세요"),
            ("not-query-2", "오늘 날씨 어때?"),
        ]:
            go(pg, "/chat")
            ask(pg, q)
            shot_turn(s, pg, name)
    with s.as_user("choi.expert") as pg:
        go(pg, "/chat")
        ask(pg, "변전소는 몇 개야?")
        shot_turn(s, pg, "forbidden")
    # 관계 저장소(Neo4j)를 잠시 멈춰 "관계 저장소 연결 불가"를 보인다.
    subprocess.run(["docker", "stop", "ontobot-test-neo4j-1"], check=True, capture_output=True)
    try:
        with s.as_user("kim.user") as pg:
            go(pg, "/chat")
            ask(pg, "강남지사가 소유한 시설 목록 보여줘")
            shot_turn(s, pg, "graph-down")
            go(pg, "/chat")
            ask(pg, "강남 변전소 01호 위험등급 알려줘")
            shot_turn(s, pg, "graph-down-plain")
    finally:
        subprocess.run(["docker", "start", "ontobot-test-neo4j-1"], check=True, capture_output=True)
        time.sleep(20)


# ---------------------------------------------------------------- 8~11. Action


def run_card(pg, text: str):
    """Action 화면에서 대상 pk 가 들어간 요청 카드(details)."""
    return pg.locator("details.card", has_text=text).first


def ch_action_chat(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "F-0004 시설의 위험등급을 3으로 바꿔줘")
        ans = last_answer(pg)
        shot_turn(s, pg, "preview-ok")
        s.shot(
            pg,
            "preview-ok-parts",
            marks=[
                (ans.locator(".badge").first, 1),
                (ans.locator(".row").first.locator("a"), 2, "right"),
                (ans.locator("table").first, 3),
                (ans.locator("details summary .badge").first, 4),
            ],
            full=True,
        )
        go(pg, "/chat")
        ask(pg, "부산 철탑 02호 긴급 점검 요청해줘")
        shot_turn(s, pg, "inspection-ok")
        go(pg, "/chat")
        ask(pg, "강남 변전소 01호 일반 점검 요청해줘")
        shot_turn(s, pg, "blocked")
        go(pg, "/chat")
        ask(pg, "제주 변전소 99호 위험등급을 2로 바꿔줘")
        shot_turn(s, pg, "unknown-target")
        go(pg, "/chat")
        ask(pg, "F-0005 시설 위험등급 바꿔줘")
        shot_turn(s, pg, "missing-value")
        ans = last_answer(pg)
        field = ans.locator("label", has_text="새 위험등급").locator("input")
        if field.count():
            field.fill("3")
            btn = ans.get_by_role("button", name="미리보기")
            s.shot(pg, "missing-value-filled", marks=[(field, 1), (btn, 2, "right")], full=True)
            btn.click()
            pg.wait_for_timeout(2500)
            s.shot(pg, "missing-value-previewed", full=True)
        go(pg, "/chat")
        ask(pg, "F-0009 시설 위험등급을 5로 바꿔줘")
        ans = last_answer(pg)
        btn = ans.get_by_role("button", name="요청 거두기")
        if btn.count():
            s.shot(pg, "withdraw-button", marks=[(btn, 1, "right")], full=True)
            btn.click()
            pg.wait_for_timeout(2000)
            shot_turn(s, pg, "withdrawn")


def ch_action_screen(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        go(pg, "/actions")
        s.shot(pg, "actions-page", full=True)
        panel = pg.locator(".card", has_text="Action 요청").first
        s.shot(pg, "request-panel", clip=panel)
        panel.locator("select").first.select_option("adjust_risk_grade")
        pg.wait_for_timeout(500)
        s.shot(pg, "request-select", clip=panel)
        panel.get_by_label("대상 객체 pk").fill("F-0012")
        panel.get_by_label("새 위험등급").fill("2")
        panel.get_by_label("사유").fill("정밀 점검 결과 이상 없음")
        s.shot(
            pg,
            "request-filled",
            clip=panel,
            marks=[
                (panel.locator("select").first, 1),
                (panel.get_by_label("대상 객체 pk"), 2),
                (panel.get_by_label("새 위험등급"), 3),
                (panel.get_by_label("사유"), 4),
                (panel.get_by_role("button", name="미리보기"), 5, "right"),
            ],
        )
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "request-previewed", clip=panel)
        panel.locator("select").first.select_option("request_inspection")
        pg.wait_for_timeout(300)
        panel.get_by_label("대상 객체 pk").fill("F-0013")
        panel.locator("label", has_text="긴급도").locator("select").select_option("일반")
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "request-blocked", clip=panel)
        go(pg, "/actions")
        tabs = pg.locator("nav[role=tablist] button")
        s.shot(pg, "tabs", marks=[(tabs.nth(0), 1, "below"), (tabs.nth(1), 2, "below"), (tabs.nth(2), 3, "below")])
        tabs.nth(1).click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "mine-tab", full=True)


def _open(card):
    if card.get_attribute("open") is None:
        card.locator("summary").click()
        card.page.wait_for_timeout(500)


def _tab(pg, i: int) -> None:
    pg.locator("nav[role=tablist] button").nth(i).click()
    pg.wait_for_timeout(1500)


def ch_approve(s: Shooter) -> None:
    with s.as_user("park.admin") as pg:
        go(pg, "/actions")
        s.shot(pg, "approver-to-decide", full=True)
        card = run_card(pg, "F-0004")
        if card.count():
            _open(card)
            approve = card.get_by_role("button", name="승인하고 실행")
            s.shot(
                pg,
                "approver-expanded",
                marks=[(approve, 1), (card.locator("input"), 2), (card.get_by_role("button", name="거부"), 3, "right")],
                full=True,
            )
            approve.click()
            pg.wait_for_timeout(2500)
            s.shot(pg, "approver-after-approve", full=True)
        for pk in ("F-0002", "F-0005"):
            c = run_card(pg, pk)
            if c.count():
                _open(c)
                c.get_by_role("button", name="승인하고 실행").click()
                pg.wait_for_timeout(2500)
        c = run_card(pg, "F-0012")
        if c.count():
            _open(c)
            c.locator("input").fill("점검 보고서 첨부 후 다시 요청하세요")
            s.shot(pg, "reject-reason", clip=c, marks=[(c.locator("input"), 1), (c.get_by_role("button", name="거부"), 2, "right")])
            c.get_by_role("button", name="거부").click()
            pg.wait_for_timeout(2500)
        s.shot(pg, "to-decide-empty", full=True)
        _tab(pg, 2)
        s.shot(pg, "all-records", full=True)
        c = run_card(pg, "F-0004")
        _open(c)
        s.shot(pg, "approved", clip=c)
        c = run_card(pg, "F-0002")
        _open(c)
        s.shot(pg, "approved-inspection", clip=c)
        c = run_card(pg, "F-0012")
        _open(c)
        s.shot(pg, "rejected-card", clip=c)
        s.shot(pg, "nav-badge", clip=pg.locator("header.topbar"), pad=0)
        inbox = pg.locator(".card", has_text="알림함")
        s.shot(pg, "inbox", clip=inbox, marks=[(inbox.get_by_role("button", name="모두 읽음"), 1, "right")])
        # 롤백 — F-0005 를 되돌린다(F-0004 는 편집 충돌 장에서 쓴다).
        c = run_card(pg, "F-0005")
        _open(c)
        rb = c.get_by_role("button", name="롤백")
        s.shot(pg, "rollback-button", clip=c, marks=[(rb, 1, "right")])
        rb.click()
        pg.wait_for_timeout(2500)
        c = run_card(pg, "F-0005")
        _open(c)
        s.shot(pg, "rolled-back", clip=c)
        go(pg, "/actions")
        inbox = pg.locator(".card", has_text="알림함")
        s.shot(pg, "inbox-compensation", clip=inbox)
        inbox.get_by_role("button", name="모두 읽음").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "inbox-read", clip=pg.locator(".card", has_text="알림함"))
        s.shot(pg, "nav-no-badge", clip=pg.locator("header.topbar"), pad=0)
    print("    확인 창:", s.dialogs, flush=True)


def ch_after_action(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        go(pg, "/actions")
        pg.locator("nav[role=tablist] button").nth(1).click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "mine-after", full=True)
        go(pg, "/chat")
        ask(pg, "F-0004 시설 위험등급 알려줘")
        ans = last_answer(pg)
        shot_turn(s, pg, "edited-answer")
        ans.locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        card = pg.locator(".object-card")
        s.shot(pg, "edited-card", clip=card, marks=[(card.locator(".edited"), 1)])
        go(pg, "/chat")
        ask(pg, "F-0005 시설 위험등급 알려줘")
        shot_turn(s, pg, "rolled-back-answer")
        go(pg, "/chat")
        ask(pg, "위험등급이 3 이상인 시설은 몇 개야?")
        shot_turn(s, pg, "count-after-edit")


# ---------------------------------------------------------------- 12. 인터뷰 워크숍

# 샘플 팩 interview.yaml 의 대본 답(모호한 답 a01 로 되묻기를 보인다). 대본이 끝나면 모른다고 답한다.
INTERVIEW = [
    "그냥 이것저것 해요.",
    "저는 지역본부에서 변전소와 전주 같은 전력 시설의 안전 관리를 맡고 있습니다.",
    "매주 시설 목록을 보고 위험등급이 높은 시설부터 점검 일정을 잡습니다. 시설은 각 운영 조직이 소유하고, 시설마다 속한 지역이 있어요.",
    "위험등급이 4 이상이면 바로 현장 점검을 요청하고, 점검 결과가 나쁘면 사용 중지를 결정합니다.",
    "시설마다 시설 코드, 이름, 유형, 위험등급, 설비 용량, 마지막 점검일을 봅니다. 유형은 변전소, 전주, 배전함 중 하나예요.",
    "태풍 경보가 뜨면 그 지역 시설을 전부 긴급 점검합니다.",
    "위험등급이 3 이상인 시설은 몇 개야? 이건 보통 18개 정도 나와요. 그리고 변전소 목록도 자주 봐요.",
]


def ch_workshop(s: Shooter) -> None:
    with s.as_user("choi.expert") as pg:
        go(pg, "/workshop")
        s.shot(pg, "list-empty")
        title = pg.locator("form.row input")
        title.fill("변전소 안전 점검 업무")
        s.shot(pg, "new-session", marks=[(title, 1), (pg.get_by_role("button", name="새 인터뷰"), 2, "right")])
        pg.get_by_role("button", name="새 인터뷰").click()
        pg.wait_for_timeout(3000)
        s.shot(pg, "session-start", full=True)
        url = pg.url
        go(pg, "/workshop")
        s.shot(pg, "list-one", marks=[(pg.get_by_role("link", name="열기").first, 1, "right")])
        pg.goto(url)
        pg.wait_for_timeout(2500)
        s.shot(pg, "session-areas", marks=[("ol.topics", 1), (".bubble-agent", 2), (".chat-input", 3), ("aside", 4, "right")])
        answers = list(INTERVIEW)
        for turn in range(1, 16):
            box = pg.locator(".chat-input textarea")
            if not box.count():
                break
            text = answers.pop(0) if answers else "그 부분은 잘 모르겠습니다."
            box.fill(text)
            box.press("Control+Enter")
            pg.wait_for_timeout(800)
            pg.wait_for_selector("p.stage", state="detached", timeout=300_000)
            pg.wait_for_timeout(1500)
            s.shot(pg, f"turn-{turn:02d}", full=True)
            print(f"    답 {turn}: {text[:20]}…", flush=True)
            if pg.locator("p.notice", has_text="인터뷰를 마쳤습니다").count():
                break
        s.shot(pg, "finished", marks=[("p.notice", 1), ("ol.topics", 2)])
        s.shot(pg, "review-list", clip=pg.locator("aside"))


def ch_workshop_review(s: Shooter) -> None:
    """가장 최근 인터뷰의 후보를 검토·제출하고, 모델러가 초안으로 게시한다."""
    with s.as_user("choi.expert") as pg:
        go(pg, "/workshop")
        pg.get_by_role("link", name="열기").first.click()
        pg.wait_for_timeout(2500)
        aside = pg.locator("aside")
        cards = aside.locator(".element")
        # 위치로 고정한다 — 수정 중인 카드는 확정 단추가 사라져 이름표 선택이 다음 카드로 넘어간다.
        done = {"confirm": 0, "edit": 0, "golden": 0, "reject": 0}
        for idx in range(cards.count()):
            card = cards.nth(idx)
            if "검토 전" not in card.locator(".badge").first.inner_text():
                continue
            card.scroll_into_view_if_needed()
            text = card.inner_text()
            if done["confirm"] == 0 and done["edit"] == 0:
                s.shot(pg, "element-card", clip=card, marks=[
                    (card.get_by_role("button", name="확정", exact=True), 1),
                    (card.get_by_role("button", name="수정", exact=True), 2),
                    (card.get_by_role("button", name="거절", exact=True), 3, "right"),
                ])
            if card.locator(".evidence-bad").count():
                s.shot(pg, f"element-bad-evidence-{done['reject']}", clip=card)
                card.get_by_role("button", name="거절", exact=True).click()
                done["reject"] += 1
            elif "기대 답변" in text:
                card.get_by_role("button", name="수정", exact=True).click()
                pg.wait_for_timeout(500)
                exp = card.locator("label", has_text="기대 답변").locator("textarea, input").first
                if not exp.input_value().strip():
                    exp.fill("변전소 10곳의 목록")
                s.shot(pg, f"edit-golden-{done['golden']}", clip=card)
                card.get_by_role("button", name="수정 확정", exact=True).click()
                done["golden"] += 1
            elif "typhoon" in text or "사용 중지" in text:
                # 데이터에 없는 개념(태풍 경보 관계·사용 중지)은 거절하는 예시
                s.shot(pg, f"reject-{done['reject']}", clip=card, marks=[(card.get_by_role("button", name="거절", exact=True), 1, "right")])
                card.get_by_role("button", name="거절", exact=True).click()
                done["reject"] += 1
            elif done["edit"] == 0 and "Pole" in text:
                card.get_by_role("button", name="수정", exact=True).click()
                pg.wait_for_timeout(500)
                s.shot(pg, "edit-form", clip=card)
                name = card.locator("label", has_text="표시 이름").locator("input").first
                name.fill("전주(배전용)")
                s.shot(pg, "edit-element", clip=card, marks=[(name, 1), (card.get_by_role("button", name="수정 확정", exact=True), 2, "right")])
                card.get_by_role("button", name="수정 확정", exact=True).click()
                done["edit"] += 1
            else:
                card.get_by_role("button", name="확정", exact=True).click()
                done["confirm"] += 1
            pg.wait_for_timeout(900)
        print("    검토:", done, flush=True)
        # 확정이 거부된 후보(예: enum 인데 값 목록이 없음) — 자료형을 고쳐 수정 확정한다.
        for idx in range(cards.count()):
            card = cards.nth(idx)
            if "검토 전" not in card.locator(".badge").first.inner_text():
                continue
            card.scroll_into_view_if_needed()
            s.shot(pg, "invalid-element", clip=card)
            card.get_by_role("button", name="수정", exact=True).click()
            pg.wait_for_timeout(500)
            dtype = card.locator("label", has_text="자료형").locator("select").first
            dtype.select_option("int")
            s.shot(pg, "invalid-fix", clip=card, marks=[(dtype, 1), (card.get_by_role("button", name="수정 확정", exact=True), 2, "right")])
            card.get_by_role("button", name="수정 확정", exact=True).click()
            pg.wait_for_timeout(1200)
            s.shot(pg, "invalid-fixed", clip=card)
        s.shot(pg, "review-done", clip=aside)
        submit = pg.get_by_role("button", name="검토 마치고 제출")
        s.shot(pg, "submit-button", marks=[(submit, 1)])
        submit.click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "submitted", full=True)
    with s.as_user("lee.modeler") as pg:
        go(pg, "/workshop")
        s.shot(pg, "modeler-list")
        pg.get_by_role("link", name="열기").first.click()
        pg.wait_for_timeout(2500)
        pub = pg.get_by_role("button", name="온톨로지 초안으로 게시")
        s.shot(pg, "publish-button", marks=[(pub, 1)])
        pub.click()
        pg.wait_for_timeout(4000)
        done = pg.locator("aside .card.ok")
        s.shot(pg, "published", clip=done, marks=[(done.get_by_role("link"), 1, "right")])
        done.get_by_role("link").first.click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "draft-from-workshop", full=True)


# ---------------------------------------------------------------- 14. 온톨로지(모델러)


def _type_article(pg, api_name: str):
    return pg.locator("article.card", has=pg.locator("h3 code", has_text=api_name)).first


def _prop_row(article, api_name: str):
    return article.locator("tbody tr", has=article.page.locator("td:first-child code", has_text=api_name)).first


def _open_version(pg, label: str) -> None:
    go(pg, "/ontology")
    row = pg.locator("tbody tr", has_text=label).first
    row.get_by_role("link", name="열기").click()
    pg.wait_for_timeout(2000)


def ch_ontology(s: Shooter) -> None:
    with s.as_user("lee.modeler") as pg:
        go(pg, "/ontology")
        s.shot(pg, "versions", marks=[("tbody tr >> nth=0", 1), ("tbody tr >> nth=1", 2)])
        _open_version(pg, "게시됨")
        s.shot(pg, "published-readonly", marks=[(".notice, .muted >> text=수정할 수 없습니다", 1)])
        _open_version(pg, "draft")
        issues = pg.locator(".card", has_text="게시 전 검사").first
        s.shot(pg, "draft-issues", clip=issues)
        diff = pg.locator("details.card", has_text="버전 비교")
        diff.locator("summary").click()
        diff.locator("select").select_option(index=1)
        pg.wait_for_timeout(1500)
        s.shot(pg, "diff-before", clip=diff)
        diff.locator("summary").click()
        # 1) pk 없는 타입(Substation·Pole)을 지운다.
        for name in ("Substation", "Pole"):
            art = _type_article(pg, name)
            if art.count():
                if name == "Substation":
                    s.shot(pg, "delete-type", clip=art, marks=[(art.locator(".row button.danger").first, 1, "right")])
                art.locator(".row button.danger").first.click()
                pg.wait_for_timeout(1500)
        # 2) 기존 속성과 겹치는 속성을 지운다.
        fac = _type_article(pg, "Facility")
        first = True
        for prop in ("region", "facility_code", "type", "capacity"):
            row = fac.locator("tbody tr").filter(has=pg.locator(f"td:first-child code:text-is('{prop}')")).first
            if row.count():
                if first:
                    s.shot(pg, "delete-dup-prop", clip=fac, marks=[(row.get_by_role("button", name="삭제"), 1, "right")])
                    first = False
                row.get_by_role("button", name="삭제").click()
                pg.wait_for_timeout(1200)
        s.shot(pg, "facility-cleaned", clip=_type_article(pg, "Facility"))
        # 3) 속성 고치기 — 마지막 점검일에 인덱스
        fac = _type_article(pg, "Facility")
        row = fac.locator("tbody tr").filter(has=pg.locator("td:first-child code:text-is('last_inspection_date')")).first
        row.get_by_role("button", name="수정").click()
        pg.wait_for_timeout(600)
        form = fac.locator("tbody tr form").first
        s.shot(pg, "edit-property", clip=form)
        form.locator("label.check", has_text="인덱스").locator("input").check()
        form.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(1500)
        # 4) 속성 추가
        fac = _type_article(pg, "Facility")
        add = fac.locator("details", has_text="속성 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="api_name").locator("input").fill("inspection_cycle_days")
        f.locator("label", has_text="표시 이름").locator("input").fill("점검 주기(일)")
        f.locator("label", has_text="자료형").locator("select").select_option("int")
        s.shot(pg, "add-property", clip=add)
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1500)
        # 5) 객체 타입 추가 — 점검 기록
        add = pg.locator("details.card", has_text="객체 타입 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="api_name").locator("input").fill("Inspection")
        f.locator("label", has_text="표시 이름").locator("input").fill("점검 기록")
        f.locator("label", has_text="pk").locator("input").fill("inspection_id")
        f.locator("label", has_text="제목").locator("input").fill("inspection_id")
        s.shot(pg, "add-type", clip=add)
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "issues-after-type", clip=pg.locator(".card", has_text="게시 전 검사").first)
        ins = _type_article(pg, "Inspection")
        for api, disp, dtype, nullable in [
            ("inspection_id", "점검 ID", "string", False),
            ("facility_id", "시설 ID", "string", False),
            ("inspected_on", "점검일", "date", True),
            ("result", "점검 결과", "string", True),
        ]:
            add = ins.locator("details", has_text="속성 추가").first
            if add.get_attribute("open") is None:
                add.locator("summary").click()
            f = add.locator("form")
            f.locator("label", has_text="api_name").locator("input").fill(api)
            f.locator("label", has_text="표시 이름").locator("input").fill(disp)
            f.locator("label", has_text="자료형").locator("select").select_option(dtype)
            box = f.locator("label.check", has_text="빈 값 허용").locator("input")
            if box.is_checked() != nullable:
                box.click()
            f.get_by_role("button", name="추가").click()
            pg.wait_for_timeout(1300)
            ins = _type_article(pg, "Inspection")
        s.shot(pg, "inspection-type", clip=ins)
        # 6) 링크 타입 추가
        add = pg.locator("details.card", has_text="링크 타입 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="api_name").locator("input").fill("inspects")
        f.locator("label", has_text="표시 이름").locator("input").fill("점검 대상 시설")
        f.locator("label", has_text="출발 타입").locator("select").select_option("Inspection")
        pg.wait_for_timeout(300)
        f.locator("label", has_text="도착 타입").locator("select").select_option("Facility")
        f.locator("label", has_text="카디널리티").locator("select").select_option("one")
        fk = f.locator("label", has_text="FK").locator("input, select").first
        if fk.evaluate("e => e.tagName") == "SELECT":
            fk.select_option("facility_id")
        else:
            fk.fill("facility_id")
        s.shot(pg, "add-link", clip=add)
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "links", clip=pg.locator("h2:has-text('링크 타입') + table, table:near(h2:has-text('링크 타입'))").first)
        s.shot(pg, "issues-pass", clip=pg.locator(".card", has_text="게시 전 검사").first)
        diff = pg.locator("details.card", has_text="버전 비교")
        diff.locator("summary").click()
        diff.locator("select").select_option(index=1)
        pg.wait_for_timeout(1500)
        s.shot(pg, "diff-after", clip=diff)
        imp = pg.locator("details.card", has_text="임포트").first
        imp.locator("summary").click()
        s.shot(pg, "import-panel", clip=imp)
        imp.locator("summary").click()
        head = pg.locator("main .row").first
        s.shot(pg, "export-publish", clip=head, marks=[(head.get_by_role("link", name="YAML"), 1, "below"), (head.get_by_role("button", name="게시"), 2, "below")], pad=40)
        s.shot(pg, "draft-full", full=True)
        head.get_by_role("button", name="게시").click()
        pg.wait_for_timeout(3000)
        s.shot(pg, "after-publish", full=False)
        go(pg, "/ontology")
        s.shot(pg, "versions-after", marks=[(pg.get_by_role("button", name="이 버전으로 새 draft").first, 1, "right")])
    print("    확인 창:", s.dialogs, flush=True)


# ---------------------------------------------------------------- 15. Action 정의(모델러)


def ch_action_types(s: Shooter) -> None:
    with s.as_user("lee.modeler") as pg:
        go(pg, "/ontology")
        pg.locator("tbody tr", has_text="게시됨").get_by_role("button", name="이 버전으로 새 draft").click()
        pg.wait_for_timeout(2000)
        go(pg, "/ontology")
        s.shot(pg, "new-draft-row", marks=[("tbody tr >> nth=0", 1)])
        _open_version(pg, "draft")
        sec = pg.locator("h2:has-text('Action')").locator("xpath=..")
        s.shot(pg, "action-section", clip=sec)
        cand = sec.locator("details", has_text="워크숍 Action 후보에서 시작").first
        if cand.count():
            cand.locator("summary").click()
            pg.wait_for_timeout(800)
            s.shot(pg, "from-candidate", clip=cand)
            cand.locator("summary").click()
        sec.get_by_role("button", name="Action 추가").click()
        pg.wait_for_timeout(800)
        form = sec.locator("form").last
        s.shot(pg, "form-empty", clip=form)
        form.locator("label", has_text="api_name").locator("input").fill("correct_capacity")
        form.locator("label", has_text="표시 이름").locator("input").fill("설비 용량 정정")
        form.locator("label", has_text="대상 타입").locator("select").select_option("Facility")
        form.locator("label", has_text="설명").locator("input").fill("현장 확인 결과로 시설의 설비 용량(kW)을 바로잡는다")
        form.locator("label", has_text="승인 역할").locator("input").fill("admin")
        params = form.locator("fieldset", has=pg.locator("legend", has_text="파라미터"))
        params.get_by_role("button", name="+ 추가").click()
        row = params.locator(".row").nth(0)
        row.locator("input").nth(0).fill("capacity_kw")
        row.locator("input").nth(1).fill("새 설비 용량(kW)")
        row.locator("select").select_option("decimal")
        params.get_by_role("button", name="+ 추가").click()
        row = params.locator(".row").nth(1)
        row.locator("input").nth(0).fill("reason")
        row.locator("input").nth(1).fill("사유")
        row.locator("label.check input").uncheck()
        s.shot(pg, "form-params", clip=params)
        conds = form.locator("fieldset", has=pg.locator("legend", has_text="사전 조건"))
        conds.get_by_role("button", name="+ 추가").click()
        crow = conds.locator(".row").nth(0)
        crow.locator("select").nth(0).select_option("capacity_kw")
        crow.locator("select").nth(1).select_option("not_null")
        s.shot(pg, "form-conditions", clip=conds)
        chg = form.locator("fieldset", has=pg.locator("legend", has_text="바꿀 속성"))
        chg.get_by_role("button", name="+ 추가").click()
        r = chg.locator(".row").nth(0)
        r.locator("select").nth(0).select_option("capacity_kw")
        r.locator("select").nth(1).select_option("p:capacity_kw")
        s.shot(pg, "form-changes", clip=chg)
        nt = form.locator("fieldset", has=pg.locator("legend", has_text="알림"))
        nt.get_by_role("button", name="+ 추가").click()
        nt.locator(".row input").first.fill("admin")
        nt.locator(":scope > .stack > input").first.fill("'{target}'({target_pk}) 설비 용량을 {capacity_kw}kW로 정정합니다. 사유: {reason}")
        s.shot(pg, "form-notify", clip=nt)
        nt.locator(".row select").first.select_option("email")
        pg.wait_for_timeout(400)
        s.shot(pg, "form-notify-email", clip=form)
        nt.locator(".row select").first.select_option("app")
        s.shot(pg, "form-full", clip=form)
        form.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "saved", clip=pg.locator("h2:has-text('Action')").locator("xpath=.."))
        s.shot(pg, "issues", clip=pg.locator(".card", has_text="게시 전 검사").first)
        # 금지 규칙 보기: pk 를 바꾸는 Action 은 게시 전 검사가 막는다.
        sec = pg.locator("h2:has-text('Action')").locator("xpath=..")
        sec.get_by_role("button", name="Action 추가").click()
        form = sec.locator("form").last
        form.locator("label", has_text="api_name").locator("input").fill("rename_facility")
        form.locator("label", has_text="표시 이름").locator("input").fill("시설 이름 바꾸기")
        form.locator("label", has_text="대상 타입").locator("select").select_option("Facility")
        chg = form.locator("fieldset", has=pg.locator("legend", has_text="바꿀 속성"))
        chg.get_by_role("button", name="+ 추가").click()
        r = chg.locator(".row").nth(0)
        r.locator("select").nth(0).select_option("name")
        r.locator("input").fill("새 이름")
        form.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "forbidden-change", full=True)
        err = pg.locator(".error, [role=alert]")
        if err.count():
            s.shot(pg, "forbidden-change-error", clip=err.first)
        cancel = sec.get_by_role("button", name="취소")
        if cancel.count():
            cancel.first.click()
        art = pg.locator("h2:has-text('Action')").locator("xpath=..").locator(".card", has_text="rename_facility")
        if art.count():
            art.get_by_role("button", name="삭제").click()
            pg.wait_for_timeout(1500)
        s.shot(pg, "issues-final", clip=pg.locator(".card", has_text="게시 전 검사").first)
        pg.locator("main .row").first.get_by_role("button", name="게시").click()
        pg.wait_for_timeout(3000)
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "F-0010 시설 설비 용량을 1500으로 정정해줘. 사유는 현장 실측")
        shot_turn(s, pg, "chat-new-action")
        go(pg, "/actions")
        panel = pg.locator(".card", has_text="Action 요청").first
        panel.locator("select").first.select_option("correct_capacity")
        pg.wait_for_timeout(500)
        s.shot(pg, "screen-new-action", clip=panel)
    print("    확인 창:", s.dialogs, flush=True)


# ---------------------------------------------------------------- 16~17. 데이터 소스·편집 충돌·그래프

MAPPING = {
    "facility_id": "fac_id",
    "name": "fac_name",
    "facility_type": "fac_type",
    "risk_grade": "risk",
    "capacity_kw": "cap_kw",
    "installed_on": "inst_date",
    "owner_org_id": "org_code",
    "region_code": "region",
}


def src_sql(sql: str) -> None:
    subprocess.run(
        ["docker", "exec", "-i", "ontobot-test-postgres-1", "psql", "-q", "-U", "ontobot", "-d", "ontobot_manual_src"],
        input=sql, text=True, check=True, capture_output=True,
    )


def _mapping(pg, table: str = "fac_master"):
    return pg.locator("article.card", has_text=table).first


def _sync_and_wait(pg, table: str = "fac_master") -> None:
    # 최근 실행 표는 몇 줄까지만 보인다 — 줄 수가 아니라 맨 윗줄이 바뀌었는지로 판단한다.
    rows = _mapping(pg, table).locator("tbody tr")
    before = rows.first.inner_text() if rows.count() else ""
    _mapping(pg, table).get_by_role("button", name="지금 동기화").click()
    for _ in range(120):
        pg.wait_for_timeout(1500)
        go(pg, "/sources", wait=500)
        rows = _mapping(pg, table).locator("tbody tr")
        top = rows.first.inner_text() if rows.count() else ""
        if top and top != before and "실행 중" not in top:
            return
    raise RuntimeError("동기화가 끝나지 않았습니다(워커 확인)")


def ch_sources(s: Shooter) -> None:
    src_sql("DELETE FROM fac_master WHERE fac_id = 'F-0031';")
    with s.as_user("lee.modeler") as pg:
        go(pg, "/sources")
        s.shot(pg, "page", full=True)
        add = pg.locator("details.card", has_text="데이터 소스 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="이름").locator("input").fill("시설 원장 DB")
        f.locator("label", has_text="시크릿 참조").locator("input").fill("env:ONTOBOT_SRC_FAC")
        s.shot(pg, "add-source", clip=add)
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1500)
        add = pg.locator("details.card", has_text="데이터 소스 추가").first
        if add.get_attribute("open") is None:
            add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="이름").locator("input").fill("연습용(잘못된 참조)")
        f.locator("label", has_text="시크릿 참조").locator("input").fill("env:ONTOBOT_SRC_NONE")
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(1500)
        go(pg, "/sources")
        rows = pg.locator("h2:has-text('데이터 소스') + table tbody tr, table:has(th:has-text('시크릿 참조')) tbody tr")
        ok = rows.filter(has_text="시설 원장 DB").first
        ok.get_by_role("button", name="접속 시험").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "test-ok", clip=pg.locator("table:has(th:has-text('시크릿 참조'))").first, marks=[(ok.get_by_role("button", name="접속 시험"), 1, "right")])
        bad = rows.filter(has_text="연습용").first
        bad.get_by_role("button", name="접속 시험").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "test-fail", full=True)
        # 매핑
        add = pg.locator("details.card", has_text="매핑 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="데이터 소스").locator("select").select_option(label="시설 원장 DB")
        f.locator("label", has_text="객체 타입").locator("select").select_option("Facility")
        f.locator("label", has_text="원천 표").locator("input").fill("public.fac_master")
        s.shot(pg, "mapping-start", clip=add)
        f.get_by_role("button", name="열 불러오기").click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "mapping-columns", clip=add)
        for prop, col in MAPPING.items():
            f.locator("tbody tr", has=pg.locator(f"code:text-is('{prop}')")).locator("select").select_option(col)
        mode = f.locator("label", has_text="모드").locator("select")
        mode.select_option("incremental")
        pg.wait_for_timeout(300)
        s.shot(pg, "mapping-incremental", clip=add)
        mode.select_option("full")
        for box in f.locator("label.check input").all():
            box.check()
        s.shot(pg, "mapping-filled", clip=add)
        f.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(2000)
        go(pg, "/sources")
        s.shot(pg, "mapping-created", clip=_mapping(pg), marks=[(_mapping(pg).get_by_role("button", name="지금 동기화"), 1, "right")])
        _sync_and_wait(pg)
        s.shot(pg, "sync-ok", clip=_mapping(pg))
        # 원천에 허용되지 않는 값(시설 유형 '배전함') → 동기화 전체 실패
        src_sql(
            "INSERT INTO fac_master VALUES ('F-0031','대구 배전함 31호','배전함',2,5,'2024-03-02','ORG-03','27', now());"
        )
        _sync_and_wait(pg)
        s.shot(pg, "sync-fail", clip=_mapping(pg))
        src_sql("UPDATE fac_master SET fac_type = '전주', updated_at = now() WHERE fac_id = 'F-0031';")
        _sync_and_wait(pg)
        s.shot(pg, "sync-fixed", clip=_mapping(pg))
        src_sql("DELETE FROM fac_master WHERE fac_id = 'F-0031';")
        _sync_and_wait(pg)
        s.shot(pg, "sync-deleted", clip=_mapping(pg))
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "강남 변전소 01호 정보 알려줘")
        last_answer(pg).locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        card = pg.locator(".object-card")
        s.shot(pg, "card-lineage", clip=card, marks=[(card.locator("text=값 출처").first, 1)])


def ch_conflicts(s: Shooter) -> None:
    # F-0004 는 Action 으로 위험등급 1 → 3 이 된 상태. 원천에서 1 → 2 로 바꾼다.
    src_sql("UPDATE fac_master SET risk = 2, updated_at = now() WHERE fac_id = 'F-0004';")
    with s.as_user("lee.modeler") as pg:
        go(pg, "/sources")
        _sync_and_wait(pg)
        panel = pg.locator(".card", has_text="편집 충돌").first
        s.shot(pg, "conflict-open", clip=panel)
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "F-0004 시설 위험등급 알려줘")
        shot_turn(s, pg, "conflict-answer")
        last_answer(pg).locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        card = pg.locator(".object-card")
        s.shot(pg, "conflict-card", clip=card, marks=[(card.locator(".edited"), 1)])
    with s.as_user("lee.modeler") as pg:
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
        graph = pg.locator(".card", has_text="그래프 투영").first
        s.shot(pg, "graph-panel", clip=graph, marks=[
            (graph.get_by_role("button", name="지금 대사"), 1, "below"),
            (graph.get_by_role("button", name="재구축"), 2, "below"),
        ])
        graph.get_by_role("button", name="지금 대사").click()
        pg.wait_for_timeout(5000)
        go(pg, "/sources")
        s.shot(pg, "graph-reconciled", clip=pg.locator(".card", has_text="그래프 투영").first)
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "F-0004 시설 위험등급 알려줘")
        shot_turn(s, pg, "after-clear-answer")
    print("    확인 창:", s.dialogs, flush=True)


# ---------------------------------------------------------------- 18. 평가(챗봇 빌더)

EVAL_ITEMS = [
    # 질문, 기대, 필수 값, 답할 수 있음, 장애 주입
    ("위험등급이 3 이상인 시설은 몇 개야?", "ok", "18", True, "none"),
    ("변전소는 몇 개야?", "ok", "10", True, "none"),
    ("부산 철탑 02호 위험등급 알려줘", "ok", "3", True, "none"),
    ("위험등급이 5인 전주는 몇 개야?", "ok", "2", True, "none"),
    ("경남에 있는 시설 알려줘", "empty", "", True, "none"),
    ("안녕하세요", "not_query", "", True, "none"),
    ("시설 점검 담당자 이름 알려줘", "failed", "", False, "none"),
    ("강남지사가 소유한 시설 목록 보여줘", "failed", "", True, "graph_down"),
    ("F-0007 시설 위험등급을 5로 바꿔줘", "action_preview", "", True, "none"),
]


def db_value(sql: str) -> str:
    out = subprocess.run(
        ["docker", "exec", "ontobot-test-postgres-1", "psql", "-tA", "-U", "ontobot", "-d", "ontobot_manual", "-c", sql],
        text=True, check=True, capture_output=True,
    )
    return out.stdout.strip().splitlines()[0] if out.stdout.strip() else ""


def ch_eval(s: Shooter) -> None:
    with s.as_user("jung.builder") as pg:
        go(pg, "/eval")
        s.shot(pg, "eval-page", full=True)
        name = pg.locator("form input").first
        name.fill("매뉴얼 기본 점검")
        s.shot(pg, "new-set", marks=[(name, 1), (pg.get_by_role("button", name="만들기"), 2, "right")])
        pg.get_by_role("button", name="만들기").click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "set-empty", full=True)
        for i, (q, exp, vals, answerable, fault) in enumerate(EVAL_ITEMS, 1):
            pg.get_by_role("button", name="문항 추가").click()
            pg.wait_for_timeout(400)
            f = pg.locator("form.grid-form").first
            f.locator("label", has_text="id").locator("input").first.fill(f"m{i:02d}")
            f.locator("label", has_text="질문").locator("input").fill(q)
            f.locator("label", has_text="기대").locator("select").select_option(exp)
            if exp == "failed":
                f.locator("label", has_text="failure").locator("input").fill(
                    "graph_unavailable" if fault == "graph_down" else "plan_invalid"
                )
            f.locator("label", has_text="필수 값").locator("input").fill(vals)
            f.locator("label", has_text="장애 주입").locator("select").select_option(fault)
            box = f.locator("label.check input")
            if box.is_checked() != answerable:
                box.click()
            if i in (1, 7, 8):
                s.shot(pg, f"item-form-{i}", clip=f)
            f.get_by_role("button", name="저장").click()
            pg.wait_for_timeout(1200)
        s.shot(pg, "items", full=True)
        run_btn = pg.get_by_role("button", name="평가 실행")
        s.shot(pg, "run-button", marks=[(run_btn, 1, "right")])
        run_btn.click()
        pg.wait_for_timeout(3000)
        s.shot(pg, "running", full=True)
        for _ in range(200):
            pg.wait_for_timeout(5000)
            pg.reload()
            pg.wait_for_timeout(1500)
            if not pg.locator("text=실행 중").count():
                break
        s.shot(pg, "run-done", full=True)
        pg.get_by_role("link", name="보기").first.click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "run-view", full=True)
        s.shot(pg, "gate-table", clip=pg.locator("h3:has-text('게이트 판정') + table, table:near(h3:has-text('게이트 판정'))").first)
        go(pg, "/eval")
        others = pg.locator("nav a, .stack a").filter(has_text="워크숍")
        if others.count():
            others.first.click()
            pg.wait_for_timeout(2000)
            s.shot(pg, "workshop-set", full=True)


# ---------------------------------------------------------------- 19. 관리(관리자)


def ch_admin(s: Shooter) -> None:
    with s.as_user("park.admin") as pg:
        go(pg, "/admin")
        s.shot(pg, "users", full=True)
        s.shot(pg, "version-card", clip=pg.locator("main .card").first)
        add = pg.locator("details", has_text="로컬 계정 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="아이디").locator("input").fill("han.new")
        f.locator("label", has_text="표시 이름").locator("input").fill("한신입")
        f.locator("label", has_text="초기 비밀번호").locator("input").fill(s_password())
        f.locator("label.check", has_text="user").locator("input").check()
        s.shot(pg, "add-user", clip=add)
        f.get_by_role("button", name="추가").click()
        pg.wait_for_timeout(2000)
        go(pg, "/admin")
        row = pg.locator("tbody tr", has_text="han.new").first
        row.locator("label.check", has_text="bot_builder").locator("input").check()
        s.shot(pg, "role-change", clip=row, marks=[(row.get_by_role("button", name="역할 저장"), 1, "right")])
        row.get_by_role("button", name="역할 저장").click()
        pg.wait_for_timeout(1500)
        row = pg.locator("tbody tr", has_text="han.new").first
        row.get_by_role("button", name="정지").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "disabled", clip=pg.locator("tbody tr", has_text="han.new").first)
        row = pg.locator("tbody tr", has_text="kim.user").first
        row.get_by_role("button", name="고치기").click()
        pg.wait_for_timeout(500)
        row.locator("input[placeholder='메일 주소']").fill("kim.user@example.local")
        row.locator("input[placeholder^='휴대전화']").fill("010-0000-1234")
        s.shot(pg, "contacts-edit", clip=row)
        row.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "contacts-saved", clip=pg.locator("tbody tr", has_text="kim.user").first)
        s.shot(pg, "self-row", clip=pg.locator("tbody tr", has_text="park.admin").first)
        tabs = pg.locator("main nav button, main [role=tablist] button")
        tabs.filter(has_text="역할별 읽기 권한").first.click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "permissions", full=True)
        card = pg.locator(".card", has=pg.locator("strong", has_text="user")).first
        card.locator("label.check", has_text="점검 기록").locator("input").check()
        s.shot(pg, "permission-change", clip=card, marks=[(card.get_by_role("button", name="권한 저장"), 1, "right")])
        card.get_by_role("button", name="권한 저장").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "permission-saved", clip=card)
        tabs.filter(has_text="감사 로그").first.click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "audit", full=False)
        flt = pg.locator("label", has_text="이벤트로 거르기").locator("input")
        flt.fill("action.executed")
        pg.wait_for_timeout(2000)
        s.shot(pg, "audit-filter", full=False, marks=[(flt, 1)])
        tabs.filter(has_text="트레이스").first.click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "trace-empty")
        tid = db_value("SELECT id FROM trace WHERE cypher_masked IS NOT NULL ORDER BY id DESC LIMIT 1") or db_value(
            "SELECT max(id) FROM trace"
        )
        box = pg.locator("label", has_text="트레이스 id").locator("input")
        box.fill(tid)
        pg.get_by_role("button", name="열기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "trace", full=True)
        for d in pg.locator("main details").all():
            if d.get_attribute("open") is None:
                d.locator("summary").first.click()
        pg.wait_for_timeout(800)
        s.shot(pg, "trace-open", full=True)
        go(pg, "/admin")
        tabs = pg.locator("main nav button, main [role=tablist] button")
        tabs.filter(has_text="감사 로그").first.click()
        pg.wait_for_timeout(1500)
        pg.locator("label", has_text="이벤트로 거르기").locator("input").fill("security.trace_viewed")
        pg.wait_for_timeout(2000)
        s.shot(pg, "audit-trace-view")


def s_password() -> str:
    from shots import password

    return password()


# ---------------------------------------------------------------- 22. 업무 시나리오


def _turns(s: Shooter, pg, prefix: str, questions: list[str]) -> None:
    for i, q in enumerate(questions, 1):
        ask(pg, q)
        shot_turn(s, pg, f"{prefix}-{i}")


def ch_scenarios(s: Shooter) -> None:
    # S1 월요일 아침 — 위험 시설 현황과 점검 요청
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        _turns(s, pg, "s1", [
            "위험등급이 4 이상인 시설은 몇 개야?",
            "위험등급이 4 이상인 시설 목록 보여줘",
            "그 중 부산지사가 소유한 것만",
            "그 시설들 설치일 알려줘",
            "부산 철탑 26호 긴급 점검 요청해줘",
        ])
        s.shot(pg, "s1-whole", full=True)
    # S2 지사별 보고 자료
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        _turns(s, pg, "s2", [
            "강남지사 소유 시설은 몇 개야?",
            "부산지사 소유 시설은 몇 개야?",
            "대구지사 소유 시설은 몇 개야?",
            "수원지사 소유 시설은 몇 개야?",
            "시설 유형별 평균 위험등급은?",
            "가장 오래된 시설 5개 보여줘",
        ])
    # S3 점검 결과 반영 — 요청 → 승인 → 확인
    with s.as_user("kim.user") as pg:
        go(pg, "/actions")
        panel = pg.locator(".card", has_text="Action 요청").first
        panel.locator("select").first.select_option("adjust_risk_grade")
        pg.wait_for_timeout(400)
        panel.get_by_label("대상 객체 pk").fill("F-0011")
        panel.get_by_label("새 위험등급").fill("3")
        panel.get_by_label("사유").fill("보수 공사 완료")
        panel.get_by_role("button", name="미리보기").click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "s3-request", clip=panel)
    with s.as_user("park.admin") as pg:
        go(pg, "/actions")
        c = run_card(pg, "F-0011")
        _open(c)
        s.shot(pg, "s3-approver", clip=c)
        c.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(2500)
        _tab(pg, 2)
        c = run_card(pg, "F-0011")
        _open(c)
        s.shot(pg, "s3-executed", clip=c)
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "대구 철탑 11호 위험등급 알려줘")
        shot_turn(s, pg, "s3-answer")
        last_answer(pg).locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "s3-card", clip=pg.locator(".object-card"))
    # S5 조직 데이터 연결(모델러)
    with s.as_user("lee.modeler") as pg:
        go(pg, "/sources")
        add = pg.locator("details.card", has_text="매핑 추가").first
        add.locator("summary").click()
        f = add.locator("form")
        f.locator("label", has_text="데이터 소스").locator("select").select_option(label="시설 원장 DB")
        f.locator("label", has_text="객체 타입").locator("select").select_option("Organization")
        f.locator("label", has_text="원천 표").locator("input").fill("public.org_master")
        f.get_by_role("button", name="열 불러오기").click()
        pg.wait_for_timeout(2000)
        for prop, col in {"org_id": "org_id", "name": "org_name", "manager_name": "manager", "manager_phone": "phone"}.items():
            f.locator("tbody tr", has=pg.locator(f"code:text-is('{prop}')")).locator("select").select_option(col)
        s.shot(pg, "s5-mapping", clip=add)
        f.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(2000)
        go(pg, "/sources")
        m = pg.locator("article.card", has_text="org_master").first
        m.get_by_role("button", name="지금 동기화").click()
        for _ in range(60):
            pg.wait_for_timeout(1500)
            go(pg, "/sources", wait=500)
            m = pg.locator("article.card", has_text="org_master").first
            if m.locator("tbody tr").count() and "실행 중" not in m.locator("tbody tr").first.inner_text():
                break
        s.shot(pg, "s5-synced", clip=m)
        s.shot(pg, "s5-mappings", full=True)
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "부산지사 담당자 알려줘")
        shot_turn(s, pg, "s5-answer")
        last_answer(pg).locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "s5-card", clip=pg.locator(".object-card"))
    # S7 워크숍 골든셋 다듬어 평가(챗봇 빌더)
    with s.as_user("jung.builder") as pg:
        go(pg, "/eval")
        pg.locator("nav a, .stack a").filter(has_text="workshop").first.click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "s7-set", full=True)
        rows = pg.locator("tbody tr").filter(has=pg.get_by_role("button", name="고치기"))
        for i in range(rows.count()):
            row = pg.locator("tbody tr").filter(has=pg.get_by_role("button", name="고치기")).nth(i)
            q = row.inner_text()
            row.get_by_role("button", name="고치기").click()
            pg.wait_for_timeout(500)
            f = pg.locator("form.grid-form").first
            f.locator("label", has_text="필수 값").locator("input").fill("18" if "3 이상" in q else "강남 변전소 01호, 수원 변전소 28호")
            if i == 0:
                s.shot(pg, "s7-edit", clip=f)
            f.get_by_role("button", name="저장").click()
            pg.wait_for_timeout(1200)
        pg.get_by_role("button", name="평가 실행").click()
        for _ in range(120):
            pg.wait_for_timeout(5000)
            pg.reload()
            pg.wait_for_timeout(1500)
            if not pg.locator("text=실행 중").count():
                break
        pg.get_by_role("link", name="보기").first.click()
        pg.wait_for_timeout(2500)
        s.shot(pg, "s7-result", full=True)


# ---------------------------------------------------------------- 23. 답 화면 모음

GALLERY = [
    "시설은 모두 몇 개야?", "전주 개수 알려줘", "위험등급이 4 이상인 시설 수는?", "위험등급이 1인 시설은 몇 곳이야?",
    "위험등급이 5인 변전소는 몇 개야?", "위험등급이 2 이하인 철탑은 몇 개?", "설비 용량이 1000kW 넘는 시설은 몇 개야?",
    "조직은 몇 개야?", "지역은 몇 개 등록돼 있어?", "전주 목록 알려줘", "위험등급이 4인 철탑 보여줘",
    "설비 용량이 2000kW 이상인 시설 보여줘", "F-0001 정보 알려줘", "수원 전주 12호는 어느 조직 소유야?",
    "대구 변전소 19호 설치일은 언제야?", "지역 코드 41은 어디야?", "강남변전소1호 위험등급 알려줘",
    "설비 용량이 가장 작은 시설 3개는?", "가장 최근에 설치된 시설 5개 보여줘", "가장 오래된 철탑 3개 알려줘",
    "소유 조직별 시설 수는?", "시설 유형별 평균 위험등급은?", "시설 유형별 설비 용량 합계 알려줘",
    "전체 시설의 평균 위험등급은?", "설비 용량이 가장 큰 값은 얼마야?", "전주의 최소 설비 용량은?",
    "2015년에 설치된 시설 알려줘", "2010년부터 2015년 사이에 설치된 변전소는?", "설치된 지 10년 넘은 전주는 몇 개야?",
    "부산지사 소유 시설은 몇 개야?", "경기에 있는 시설 목록", "대구 지역의 위험등급 4 이상 시설은?",
    "수원지사 소유 철탑의 평균 설비 용량은?", "서울에 있는 위험등급 2 이하 시설", "2015년 이후 설치된 위험등급 4 이상 철탑",
    "강남지사 소유 변전소 중 용량 2000kW 이상인 것", "다음 달 점검 일정 알려줘", "변전소 고장 원인이 뭐야?",
    "2030년 이후에 설치된 시설은?", "고마워",
]


def ch_gallery(s: Shooter) -> None:
    import json

    meta = []
    with s.as_user("kim.user") as pg:
        for i, q in enumerate(GALLERY, 1):
            go(pg, "/chat", wait=600)
            ask(pg, q)
            name = f"g{i:02d}"
            shot_turn(s, pg, name)
            ans = last_answer(pg)
            cond = ans.locator(".conditions")
            badge = ans.locator(".badge")
            meta.append({
                "image": name,
                "question": q,
                "conditions": cond.first.inner_text() if cond.count() else None,
                "badge": badge.first.inner_text() if badge.count() else None,
                "text": ans.inner_text()[:600],
            })
    (s.dir / "gallery.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 31. 화면별 해설(참조용)


def _numbered(s: Shooter, pg, name: str, targets: list, full: bool = True) -> None:
    marks = []
    n = 0
    for t in targets:
        loc = pg.locator(t) if isinstance(t, str) else t
        if loc.first.count():
            n += 1
            marks.append((loc.first, n, "right"))
    s.shot(pg, name, marks=marks, full=full)


def ch_screens(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "위험등급이 5인 시설 목록 알려줘")
        ans = last_answer(pg)
        ans.locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        _numbered(s, pg, "chat", [
            "header nav", "header .muted", ".chat-list button", ".chat-list a.active", ".chat-thread .bubble-user",
            ans.locator(".conditions"), ans.locator(".answer-text"), ans.locator(".badge"), ans.locator(".citations"),
            ".object-card", ".chat-input textarea", ".chat-input button",
        ])
    with s.as_user("park.admin") as pg:
        go(pg, "/actions")
        _tab(pg, 2)
        c = pg.locator("details.card").first
        _open(c)
        _numbered(s, pg, "actions", [
            ".card:has-text('Action 요청') select", ".card:has-text('Action 요청') input", "button:has-text('미리보기')",
            "nav[role=tablist]", c.locator("summary .badge"), c.locator("summary"), c.locator("table"),
            c.locator("text=알림").first, ".card:has-text('알림함')",
        ])
        go(pg, "/sources")
        _numbered(s, pg, "sources", [
            ".card:has-text('그래프 투영')", ".card:has-text('편집 충돌') nav, .card:has-text('편집 충돌') button",
            "table:has(th:has-text('시크릿 참조'))", "details:has-text('데이터 소스 추가')", "article.card",
            "article.card button", "details:has-text('매핑 추가')",
        ])
        go(pg, "/admin")
        _numbered(s, pg, "admin-users", [
            "main .card", "main nav, main [role=tablist]", "tbody tr >> nth=0", "tbody tr td:nth-child(4) >> nth=0",
            "tbody tr td:nth-child(5) >> nth=1", "tbody tr td:nth-child(6) >> nth=0", "details:has-text('로컬 계정 추가')",
        ])
    with s.as_user("lee.modeler") as pg:
        go(pg, "/ontology")
        _numbered(s, pg, "ontology-list", ["main h1", "button:has-text('빈 draft 만들기')", "tbody tr >> nth=0",
                                          "a:has-text('열기') >> nth=0", "button:has-text('이 버전으로 새 draft') >> nth=0"])
        _open_version(pg, "게시됨")
        _numbered(s, pg, "ontology-detail", [
            "main h1", "main .row a >> nth=0", "main .notice, main .muted >> nth=0", "details:has-text('버전 비교')",
            "article.card >> nth=0", "article.card table >> nth=0", "h2:has-text('링크 타입')", "h2:has-text('Action')",
        ])
        go(pg, "/workshop")
        _numbered(s, pg, "workshop-list", ["main h1", "form.row input", "button:has-text('새 인터뷰')", "tbody tr >> nth=0",
                                          "a:has-text('열기') >> nth=0"])
    with s.as_user("jung.builder") as pg:
        go(pg, "/eval")
        pg.locator("nav a, .stack a").filter(has_text="매뉴얼").first.click()
        pg.wait_for_timeout(2000)
        _numbered(s, pg, "eval", [
            "h2:has-text('골든셋')", "main nav a, main .stack a >> nth=0", "form input", "button:has-text('평가 실행')",
            "h3:has-text('실행 기록') + table, table >> nth=0", "a:has-text('보기')", "button:has-text('문항 추가')",
        ])


# ---------------------------------------------------------------- 32. 트레이스 읽기

TRACE_QUESTIONS = [
    ("t1-count", "위험등급이 3 이상인 시설은 몇 개야?", "first"),
    ("t2-list", "변전소 목록 보여줘", "last"),
    ("t3-traverse", "강남지사가 소유한 시설 목록 보여줘", "first"),
    ("t4-followup", "그 중 위험등급이 4 이상인 것만", "first"),
    ("t5-invalid", "시설 점검 담당자 이름 알려줘", "first"),
    ("t6-estimate", "위험등급이 가장 높은 시설 5개 알려줘", "first"),
    ("t7-action", "F-0004 시설의 위험등급을 3으로 바꿔줘", "first"),
    ("t8-misread", "위험등급이 5인 전주는 몇 개야?", "first"),
    ("t9-grade3", "지역은 몇 개 등록돼 있어?", "last"),
]


def ch_traces(s: Shooter) -> None:
    import json

    found = []
    with s.as_user("park.admin") as pg:
        for name, q, which in TRACE_QUESTIONS:
            order = "ASC" if which == "first" else "DESC"
            esc = q.replace("'", "''")
            tid = db_value(
                "SELECT a.trace_id FROM message u JOIN message a ON a.conversation_id = u.conversation_id "
                "AND a.id = (SELECT min(id) FROM message m WHERE m.conversation_id = u.conversation_id AND m.id > u.id "
                f"AND m.role = 'assistant') WHERE u.role = 'user' AND u.content = '{esc}' AND a.trace_id IS NOT NULL "
                f"ORDER BY u.id {order} LIMIT 1"
            )
            if not tid:
                print("    트레이스 없음:", q, flush=True)
                continue
            go(pg, "/admin")
            pg.locator("main nav button, main [role=tablist] button").filter(has_text="트레이스").first.click()
            pg.wait_for_timeout(800)
            pg.locator("label", has_text="트레이스 id").locator("input").fill(tid)
            pg.get_by_role("button", name="열기").click()
            pg.wait_for_timeout(2000)
            for d in pg.locator("main details").all():
                if d.get_attribute("open") is None:
                    d.locator("summary").first.click()
            pg.wait_for_timeout(600)
            s.shot(pg, name, full=True)
            found.append({"image": name, "question": q, "trace": tid})
    (s.dir / "traces.json").write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 33. 새 객체 타입을 끝까지(점검 기록)

INSPECTIONS = [
    ("I-001", "F-0003", "2024-03-12", "이상"), ("I-002", "F-0006", "2024-03-15", "이상"),
    ("I-003", "F-0010", "2024-04-02", "정상"), ("I-004", "F-0014", "2024-04-20", "이상"),
    ("I-005", "F-0020", "2024-05-08", "정상"), ("I-006", "F-0023", "2024-05-30", "이상"),
    ("I-007", "F-0025", "2024-06-11", "정상"), ("I-008", "F-0026", "2024-07-01", "정상"),
    ("I-009", "F-0026", "2025-01-15", "이상"), ("I-010", "F-0002", "2025-02-03", "정상"),
    ("I-011", "F-0011", "2025-03-21", "정상"), ("I-012", "F-0007", "2025-04-09", "이상"),
]


def ch_inspection(s: Shooter) -> None:
    rows = ",".join(f"('{a}','{b}','{c}','{d}')" for a, b, c, d in INSPECTIONS)
    src_sql(
        "CREATE TABLE IF NOT EXISTS inspection_log (insp_id text primary key, fac_id text not null, insp_date date, result text);"
        f"DELETE FROM inspection_log; INSERT INTO inspection_log VALUES {rows};"
    )
    with s.as_user("lee.modeler") as pg:
        go(pg, "/sources")
        if not pg.locator("article.card", has_text="inspection_log").count():
            add = pg.locator("details.card", has_text="매핑 추가").first
            add.locator("summary").click()
            f = add.locator("form")
            f.locator("label", has_text="데이터 소스").locator("select").select_option(label="시설 원장 DB")
            f.locator("label", has_text="객체 타입").locator("select").select_option("Inspection")
            f.locator("label", has_text="원천 표").locator("input").fill("public.inspection_log")
            f.get_by_role("button", name="열 불러오기").click()
            pg.wait_for_timeout(2000)
            cols = {"inspection_id": "insp_id", "facility_id": "fac_id", "inspected_on": "insp_date", "result": "result"}
            for prop, col in cols.items():
                f.locator("tbody tr", has=pg.locator(f"code:text-is('{prop}')")).locator("select").select_option(col)
            for box in f.locator("label.check input").all():
                box.check()
            s.shot(pg, "mapping", clip=add)
            f.get_by_role("button", name="저장").click()
            pg.wait_for_timeout(2000)
            go(pg, "/sources")
        _sync_and_wait(pg, "inspection_log")
        s.shot(pg, "synced", clip=_mapping(pg, "inspection_log"))
        pg.wait_for_timeout(5000)
        go(pg, "/sources")
        s.shot(pg, "graph", clip=pg.locator(".card", has_text="그래프 투영").first)
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        _turns(s, pg, "q", [
            "점검 기록은 몇 건이야?",
            "점검 결과가 이상인 점검 기록 보여줘",
            "2025년에 한 점검 기록 목록 알려줘",
            "점검 대상 시설이 부산 철탑 26호인 점검 기록 보여줘",
            "점검 결과별 점검 기록 수 알려줘",
        ])
        go(pg, "/chat")
        ask(pg, "I-009 점검 기록 알려줘")
        shot_turn(s, pg, "q-single")
        last_answer(pg).locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "card", clip=pg.locator(".object-card"))
    with s.as_user("choi.expert") as pg:
        go(pg, "/chat")
        ask(pg, "점검 기록은 몇 건이야?")
        shot_turn(s, pg, "no-permission")


# ---------------------------------------------------------------- 34. 이름이 같은 대상이 여럿일 때


def ch_duplicates(s: Shooter) -> None:
    src_sql("DELETE FROM fac_master WHERE fac_id = 'F-0031';"
            "INSERT INTO fac_master VALUES ('F-0031','강남 변전소 01호','변전소',3,1000,'2025-06-01','ORG-01','11', now());")
    try:
        with s.as_user("lee.modeler") as pg:
            go(pg, "/sources")
            _sync_and_wait(pg)
            s.shot(pg, "synced", clip=_mapping(pg))
        with s.as_user("kim.user") as pg:
            go(pg, "/chat")
            ask(pg, "강남 변전소 01호 위험등급 알려줘")
            shot_turn(s, pg, "ask")
            go(pg, "/chat")
            ask(pg, "강남 변전소 01호 위험등급을 4로 바꿔줘")
            shot_turn(s, pg, "action")
            ans = last_answer(pg)
            pk = ans.get_by_label("대상 객체 pk")
            if pk.count():
                pk.first.fill("F-0031")
                ans.get_by_role("button", name="미리보기").click()
                pg.wait_for_timeout(2500)
                shot_turn(s, pg, "action-picked")
                btn = last_answer(pg).get_by_role("button", name="요청 거두기")
                if btn.count():
                    btn.last.click()
                    pg.wait_for_timeout(1500)
    finally:
        src_sql("DELETE FROM fac_master WHERE fac_id = 'F-0031';")
        with s.as_user("lee.modeler") as pg:
            go(pg, "/sources")
            _sync_and_wait(pg)


# ---------------------------------------------------------------- 35. 답 화면 모음 2

GALLERY2 = [
    "조직 목록을 담당자와 함께 보여줘", "ORG-03 조직 이름 알려줘", "담당자 연락처가 010-0000-0004인 조직은?",
    "경남 지역 코드 알려줘", "지역별 시설 수 알려줘", "시설이 가장 많은 지역은?", "조직별 평균 위험등급 알려줘",
    "점검 기록 목록 보여줘", "점검 결과가 정상인 점검 기록은 몇 건?", "2024년에 한 점검 기록 보여줘",
    "가장 최근 점검 기록 3개 알려줘", "I-005 점검 기록 알려줘", "F-0026 시설의 점검 기록 보여줘",
    "점검일이 2025-03-21인 점검 기록은?", "강남 변전소 1호 위험등급 알려줘", "강남변전소 01호 위험등급 알려줘",
    "f-0007 설비 용량", "변전쏘 몇 개야?", "강남 지사 소유 시설 몇 개야?",
    "이번 주 점검 계획을 세우려고 하는데, 위험등급이 4 이상인 변전소가 몇 개인지 알려줄래?",
    "보고서를 써야 해서 그런데요, 부산지사가 소유한 철탑 목록 좀 부탁드립니다.",
    "위험등급이 5인 시설 중에서 서울에 있는 것만 알고 싶어요. 가능할까요?",
    "위험등급 3 초과인 시설 몇 개?", "위험등급 3 미만인 시설 몇 개?", "위험등급이 2에서 4 사이인 시설은 몇 개야?",
    "설비 용량이 1200kW 이상 2400kW 이하인 변전소", "2017년 1월 1일 이후 설치된 철탑",
    "2017년 1월 1일 포함 이후에 설치된 철탑", "2016년 말까지 설치된 전주는 몇 개?", "변전소가 총 몇 곳인지 알려주세요",
    "철탑 중에 위험등급 5인 거", "부산지사에서 관리하는 시설 보여줘", "위험등급이 3보다 크거나 같은 시설은 몇 개?",
    "설비 용량이 1200kW인 시설 보여줘", "점검 결과별 점검 기록 수", "대구지사 담당자는 누구야?",
]


def ch_gallery2(s: Shooter) -> None:
    import json

    meta = []
    with s.as_user("kim.user") as pg:
        for i, q in enumerate(GALLERY2, 1):
            go(pg, "/chat", wait=600)
            ask(pg, q)
            name = f"h{i:02d}"
            shot_turn(s, pg, name)
            ans = last_answer(pg)
            cond = ans.locator(".conditions")
            badge = ans.locator(".badge")
            meta.append({
                "image": name, "question": q,
                "conditions": cond.first.inner_text() if cond.count() else None,
                "badge": badge.first.inner_text() if badge.count() else None,
                "text": ans.inner_text()[:600],
            })
    (s.dir / "gallery.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 36. 설계도 다듬기 실험

PROBES = ["위험등급이 5인 전주는 몇 개야?", "I-005 점검 기록 알려줘", "점검 대상 시설이 대구 변전소 07호인 점검 기록은?",
          "지역별 시설 수 알려줘"]


def _probe(s: Shooter, label: str, rounds: int = 3) -> list[dict]:
    out = []
    with s.as_user("kim.user") as pg:
        for q in PROBES:
            for r in range(1, rounds + 1):
                go(pg, "/chat", wait=500)
                ask(pg, q)
                ans = last_answer(pg)
                cond = ans.locator(".conditions")
                fail = ans.locator(".failure")
                out.append({
                    "phase": label, "question": q, "round": r,
                    "conditions": cond.first.inner_text() if cond.count() else None,
                    "failure": fail.first.inner_text() if fail.count() else None,
                    "badge": ans.locator(".badge").first.inner_text() if ans.locator(".badge").count() else None,
                })
                if r == 1:
                    shot_turn(s, pg, f"{label}-{PROBES.index(q) + 1}")
    return out


def ch_naming(s: Shooter) -> None:
    import json

    before = _probe(s, "before")
    with s.as_user("lee.modeler") as pg:
        go(pg, "/ontology")
        pg.locator("tbody tr", has_text="게시됨").get_by_role("button", name="이 버전으로 새 draft").click()
        pg.wait_for_timeout(2000)
        _open_version(pg, "draft")
        # 1) 시설 유형 표시 이름에 값 목록을 드러낸다.
        fac = _type_article(pg, "Facility")
        row = fac.locator("tbody tr").filter(has=pg.locator("td:first-child code:text-is('facility_type')")).first
        row.get_by_role("button", name="수정").click()
        pg.wait_for_timeout(500)
        form = fac.locator("tbody tr form").first
        form.locator("label", has_text="표시 이름").locator("input").fill("시설 유형(변전소·철탑·전주 중 하나)")
        s.shot(pg, "edit-facility-type", clip=form)
        form.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(1500)
        # 2) 점검 기록 타입의 표시 이름·제목을 다듬는다 — 제목 속성을 점검 ID 로 쓰므로 "점검 ID"로 묻는 말을 돕는다.
        ins = _type_article(pg, "Inspection")
        row = ins.locator("tbody tr").filter(has=pg.locator("td:first-child code:text-is('inspection_id')")).first
        row.get_by_role("button", name="수정").click()
        pg.wait_for_timeout(500)
        form = ins.locator("tbody tr form").first
        form.locator("label", has_text="표시 이름").locator("input").fill("점검 ID(I-001 형식)")
        s.shot(pg, "edit-inspection-id", clip=form)
        form.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(1500)
        diff = pg.locator("details.card", has_text="버전 비교")
        diff.locator("summary").click()
        diff.locator("select").select_option(index=1)
        pg.wait_for_timeout(1500)
        s.shot(pg, "diff", clip=diff)
        pg.locator("main .row").first.get_by_role("button", name="게시").click()
        pg.wait_for_timeout(3000)
    after = _probe(s, "after")
    (s.dir / "probes.json").write_text(json.dumps(before + after, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------- 37. 두 번째 인터뷰

INTERVIEW2 = [
    "점검 기록을 관리합니다.",
    "현장 점검원이 점검을 마치면 점검 기록을 남기고, 저는 그 기록을 검토해서 결과가 이상인 시설을 다시 점검 대상으로 올립니다. 점검 기록마다 점검 ID, 점검한 시설, 점검일, 점검 결과가 있어요.",
    "점검 결과가 이상이면 그 시설의 위험등급을 한 단계 올리고, 두 번 연속 이상이면 긴급 점검을 요청합니다.",
    "점검 기록에서는 점검일과 점검 결과를 가장 많이 보고, 시설에서는 위험등급과 마지막 점검일을 봅니다. 점검 결과는 정상, 이상, 보류 중 하나입니다.",
    "그 부분은 잘 모르겠습니다.",
    "점검원이 기록을 늦게 올리면 점검일이 실제와 다르게 들어가는 경우가 있어서 그때는 점검원에게 다시 확인합니다.",
    "올해 점검 결과가 이상인 시설은 몇 곳이야? 보통 5곳 안팎이에요. 그리고 점검을 1년 넘게 안 한 시설 목록도 자주 봐요.",
]


def ch_interview2(s: Shooter) -> None:
    with s.as_user("choi.expert") as pg:
        go(pg, "/workshop")
        pg.locator("form.row input").fill("점검 기록 관리 업무")
        pg.get_by_role("button", name="새 인터뷰").click()
        pg.wait_for_timeout(3000)
        answers = list(INTERVIEW2)
        for turn in range(1, 16):
            box = pg.locator(".chat-input textarea")
            if not box.count():
                break
            text = answers.pop(0) if answers else "그 부분은 잘 모르겠습니다."
            box.fill(text)
            box.press("Control+Enter")
            pg.wait_for_timeout(800)
            pg.wait_for_selector("p.stage", state="detached", timeout=300_000)
            pg.wait_for_timeout(1500)
            print(f"    답 {turn}: {text[:20]}…", flush=True)
            if pg.locator("p.notice", has_text="인터뷰를 마쳤습니다").count():
                break
        s.shot(pg, "done", full=True)
        s.shot(pg, "review", clip=pg.locator("aside"))


# ---------------------------------------------------------------- 38. 좁은 화면


def ch_narrow(s: Shooter) -> None:
    vp = {"width": 820, "height": 1000}
    with s.as_user("kim.user", viewport=vp) as pg:
        go(pg, "/chat")
        ask(pg, "부산지사가 소유한 시설 중 위험등급이 4 이상인 것은?")
        last_answer(pg).locator(".citations button").first.click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "chat", full=True)
        go(pg, "/actions")
        s.shot(pg, "actions", full=False)
    with s.as_user("lee.modeler", viewport=vp) as pg:
        go(pg, "/sources")
        s.shot(pg, "sources", full=False)
        _open_version(pg, "게시됨")
        s.shot(pg, "ontology", full=False)
    with s.as_user("park.admin", viewport=vp) as pg:
        go(pg, "/admin")
        s.shot(pg, "admin", full=False)


# ---------------------------------------------------------------- 39~41. 메일·SMS 알림, 승인 순간의 실패
# 30-notify-setup 뒤에 API·워커를 ONTOBOT_NOTIFY_CONFIG_PATH=config/notify.dev.yaml(가짜 게이트웨이)로 다시 띄운다.


def ch_notify_setup(s: Shooter) -> None:
    with s.as_user("park.admin") as pg:
        go(pg, "/admin")
        row = pg.locator("tbody tr", has_text="park.admin").first
        row.get_by_role("button", name="고치기").click()
        pg.wait_for_timeout(500)
        row.locator("input[placeholder='메일 주소']").fill("park.admin@example.local")
        row.locator("input[placeholder^='휴대전화']").fill("010-0000-9999")
        row.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(1500)
        s.shot(pg, "admin-contacts", clip=pg.locator("tbody tr", has_text="park.admin").first)
    with s.as_user("lee.modeler") as pg:
        go(pg, "/ontology")
        pg.locator("tbody tr", has_text="게시됨").get_by_role("button", name="이 버전으로 새 draft").click()
        pg.wait_for_timeout(2000)
        _open_version(pg, "draft")
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
        s.shot(pg, "action-form", clip=form)
        form.get_by_role("button", name="저장").click()
        pg.wait_for_timeout(2000)
        s.shot(pg, "issues", clip=pg.locator(".card", has_text="게시 전 검사").first)
        pg.locator("main .row").first.get_by_role("button", name="게시").click()
        pg.wait_for_timeout(3000)


def ch_notify_flow(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "F-0020 시설 긴급 정비 통보 요청해줘. 통보 내용은 철탑 기울어짐 확인")
        shot_turn(s, pg, "chat-preview")
    with s.as_user("park.admin") as pg:
        go(pg, "/actions")
        c = run_card(pg, "F-0020")
        _open(c)
        s.shot(pg, "approver", clip=c)
        c.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(2500)
        _tab(pg, 2)
        c = run_card(pg, "F-0020")
        _open(c)
        s.shot(pg, "executed-pending", clip=c)
        for _ in range(30):
            pg.wait_for_timeout(3000)
            go(pg, "/actions")
            _tab(pg, 2)
            c = run_card(pg, "F-0020")
            _open(c)
            if "대기" not in c.inner_text():
                break
        s.shot(pg, "executed-sent", clip=c)
        rb = c.get_by_role("button", name="롤백")
        rb.click()
        pg.wait_for_timeout(2500)
        for _ in range(30):
            pg.wait_for_timeout(3000)
            go(pg, "/actions")
            _tab(pg, 2)
            c = run_card(pg, "F-0020")
            _open(c)
            if c.inner_text().count("보냄") >= 4 or "대기" not in c.inner_text():
                break
        s.shot(pg, "rolled-back", clip=c)
    print("    확인 창:", s.dialogs, flush=True)


def ch_approve_fail(s: Shooter) -> None:
    with s.as_user("kim.user") as pg:
        go(pg, "/chat")
        ask(pg, "F-0030 시설 일반 점검 요청해줘")
        shot_turn(s, pg, "inspection-preview")
        go(pg, "/chat")
        ask(pg, "F-0030 시설 위험등급을 2로 바꿔줘. 사유는 보강 공사 완료")
        shot_turn(s, pg, "adjust-preview")
    with s.as_user("park.admin") as pg:
        go(pg, "/actions")
        adj = pg.locator("details.card", has_text="F-0030").filter(has_text="위험등급 조정").first
        _open(adj)
        adj.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(2500)
        go(pg, "/actions")
        ins = pg.locator("details.card", has_text="F-0030").filter(has_text="점검 요청").first
        _open(ins)
        s.shot(pg, "before-approve", clip=ins)
        ins.get_by_role("button", name="승인하고 실행").click()
        pg.wait_for_timeout(2500)
        _tab(pg, 2)
        ins = pg.locator("details.card", has_text="F-0030").filter(has_text="점검 요청").first
        _open(ins)
        s.shot(pg, "failed", clip=ins)


CHAPTERS = {
    "01-login": ch_login,
    "02-layout": ch_layout,
    "03-first-question": ch_first,
    "04-reading": ch_reading,
    "05-question-types": ch_types,
    "06-followup": ch_followup,
    "07-failures": ch_failures,
    "08-action-chat": ch_action_chat,
    "09-action-screen": ch_action_screen,
    "10-approve": ch_approve,
    "11-after-action": ch_after_action,
    "12-workshop": ch_workshop,
    "13-workshop-review": ch_workshop_review,
    "14-ontology": ch_ontology,
    "15-action-types": ch_action_types,
    "16-sources": ch_sources,
    "17-conflicts": ch_conflicts,
    "18-eval": ch_eval,
    "19-admin": ch_admin,
    "20-scenarios": ch_scenarios,
    "21-gallery": ch_gallery,
    "22-screens": ch_screens,
    "23-traces": ch_traces,
    "24-inspection": ch_inspection,
    "25-duplicates": ch_duplicates,
    "26-gallery2": ch_gallery2,
    "27-naming": ch_naming,
    "28-interview2": ch_interview2,
    "29-narrow": ch_narrow,
    "30-notify-setup": ch_notify_setup,
    "31-notify-flow": ch_notify_flow,
    "32-approve-fail": ch_approve_fail,
}

if __name__ == "__main__":
    run(CHAPTERS, sys.argv[1:])
