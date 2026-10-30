"""운영자 매뉴얼 화면 촬영 — 웹 앱(5173)의 관리·데이터 소스·온톨로지 화면을 실제로 열어 찍는다.

API(:8000)가 웹 화면까지 서빙한다(app.env 의 허용 출처 http://localhost:8000). 계정은 루트 .env 의 촬영 전용 관리자.
    uv run --with playwright python docs/manual/tools/capture_app.py
사진은 docs/manual/ops/images/<장>/<이름>.png 에 저장한다(배포 콘솔 사진과 같은 자리).
"""

from __future__ import annotations

import os

from capture_ops import ROOT, env_value, shot  # type: ignore[import-not-found]
from playwright.sync_api import Page, sync_playwright

BASE = os.environ.get("ONTOBOT_APP_URL", "http://localhost:8000")
USER = "manual.capture"


def login(page: Page) -> None:
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.get_by_label("아이디").fill(USER)
    page.get_by_label("비밀번호").fill(env_value("ONTOBOT_MANUAL_PASSWORD"))
    page.get_by_role("button", name="로그인", exact=True).click()
    page.wait_for_url(lambda u: "/login" not in u, timeout=20_000)
    page.wait_for_load_state("networkidle")


def open_page(page: Page, path: str) -> None:
    page.goto(f"{BASE}{path}", wait_until="networkidle")
    page.wait_for_timeout(800)


def tab(page: Page, label: str) -> None:
    page.get_by_role("tab", name=label).click()
    page.wait_for_load_state("networkidle")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(f"{BASE}/login", wait_until="networkidle")
        shot(page, "02-login", "web-login")
        login(page)
        open_page(page, "/admin")
        shot(page, "10-admin", "admin-users")
        for label, name in [("역할별 읽기 권한", "admin-permissions"), ("감사 로그", "admin-audit"), ("트레이스", "admin-trace")]:
            tab(page, label)
            shot(page, "10-admin", name)
        open_page(page, "/sources")
        shot(page, "11-sources", "sources-list")
        open_page(page, "/ontology")
        shot(page, "12-ontology", "ontology-versions")
        open_page(page, "/actions")
        shot(page, "13-actions", "actions-list")
        open_page(page, "/sources")
        page.get_by_text("데이터 소스 추가", exact=True).click()
        shot(page, "14-setup", "sources-add-form")
        page.get_by_text("매핑 추가", exact=True).click()
        shot(page, "14-setup", "mapping-add-form")
        open_page(page, "/admin")
        page.get_by_text("로컬 계정 추가", exact=True).click()
        shot(page, "14-setup", "admin-add-user-form")
        browser.close()


if __name__ == "__main__":
    main()
