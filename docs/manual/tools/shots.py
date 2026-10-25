"""매뉴얼 화면 촬영 공통 부분 — Playwright(설치된 Edge)로 실제 화면을 찍는다.

사진은 ``docs/manual/user/images/<장>/<이름>.png`` 에 저장한다. 본문(Markdown)이 이 경로를 그대로 쓴다.
번호 표시(``mark``)는 화면 위에 빨간 테두리와 동그라미 번호를 잠시 그렸다가 찍고 지운다 — 앱 코드를
바꾸지 않는다.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from playwright.sync_api import Browser, Locator, Page, sync_playwright

IMAGES = Path(__file__).resolve().parents[1] / "user" / "images"
BASE = os.environ.get("ONTOBOT_MANUAL_URL", "http://localhost:5173")
VIEWPORT = {"width": 1280, "height": 800}


def password() -> str:
    pw = os.environ.get("ONTOBOT_MANUAL_PASSWORD")
    if not pw:
        raise SystemExit("ONTOBOT_MANUAL_PASSWORD 를 주세요")
    return pw


_MARK_JS = """
(items) => {
  const made = [];
  for (const it of items) {
    const r = it.rect;
    const box = document.createElement('div');
    box.className = '__manual_mark';
    Object.assign(box.style, {
      position: 'absolute', left: (r.x - 4) + 'px', top: (r.y - 4) + 'px',
      width: (r.width + 8) + 'px', height: (r.height + 8) + 'px', border: '3px solid #e11d48',
      borderRadius: '6px', zIndex: 99998, pointerEvents: 'none', boxSizing: 'border-box',
    });
    document.body.appendChild(box);
    made.push(box);
    if (it.n !== null) {
      const b = document.createElement('div');
      b.className = '__manual_mark';
      b.textContent = String(it.n);
      let left = it.side === 'right' ? r.x + r.width + 6 : r.x - 30;
      let top = r.y - 14;
      if (it.side === 'below') { left = r.x + r.width / 2 - 13; top = r.y + r.height + 6; }
      Object.assign(b.style, {
        position: 'absolute', left: Math.max(2, left) + 'px', top: top + 'px',
        width: '26px', height: '26px', borderRadius: '13px', background: '#e11d48', color: '#fff',
        font: 'bold 15px/26px sans-serif', textAlign: 'center', zIndex: 99999, pointerEvents: 'none',
        boxShadow: '0 1px 3px rgba(0,0,0,.4)',
      });
      document.body.appendChild(b);
      made.push(b);
    }
  }
  return made.length;
}
"""


class Shooter:
    def __init__(self, browser: Browser, chapter: str) -> None:
        self.browser = browser
        self.dir = IMAGES / chapter
        self.dir.mkdir(parents=True, exist_ok=True)
        self.count = 0
        self.dialogs: list[str] = []

    @contextmanager
    def as_user(self, username: str | None, viewport: dict[str, int] | None = None) -> Iterator[Page]:
        ctx = self.browser.new_context(viewport=viewport or VIEWPORT, locale="ko-KR")
        page = ctx.new_page()
        page.set_default_timeout(30_000)
        # 확인 창(window.confirm)은 사진에 찍히지 않는다 — 문구를 남기고 "확인"을 누른다.
        page.on("dialog", lambda d: (self.dialogs.append(d.message), d.accept()))
        if username:
            login(page, username)
        try:
            yield page
        finally:
            ctx.close()

    def shot(
        self,
        page: Page,
        name: str,
        *,
        marks: Sequence[tuple[Any, int | None] | tuple[Any, int | None, str]] = (),
        full: bool = False,
        clip: Locator | None = None,
        pad: int = 12,
        region: dict[str, float] | None = None,
    ) -> Path:
        """한 장 찍는다. ``marks`` 는 (선택자 또는 Locator, 번호[, 'right']) — 번호가 None 이면 테두리만."""
        page.wait_for_timeout(300)
        items = []
        for m in marks:
            target, n = m[0], m[1]
            side = m[2] if len(m) > 2 else "left"
            loc = page.locator(target) if isinstance(target, str) else target
            loc = loc.first
            if not loc.count():
                continue  # 찍기 직전에 사라진 요소(진행 단계 등)
            loc.scroll_into_view_if_needed(timeout=3000)
            box = loc.bounding_box(timeout=3000)
            if box:
                # 문서 좌표로 바꿔 둔다 — 다음 표시가 스크롤해도 어긋나지 않게.
                sy = page.evaluate("window.scrollY")
                sx = page.evaluate("window.scrollX")
                box = {**box, "x": box["x"] + sx, "y": box["y"] + sy}
                items.append({"rect": box, "n": n, "side": side})
        if items:
            page.evaluate(_MARK_JS, items)
        path = self.dir / f"{name}.png"
        try:
            if region is not None:
                page.screenshot(path=str(path), full_page=True, clip=region)  # type: ignore[arg-type]
            elif clip is not None:
                box = clip.first.bounding_box()
                assert box, f"{name}: 영역을 찾지 못했습니다"
                page.screenshot(
                    path=str(path),
                    full_page=True,
                    clip={
                        "x": max(0, box["x"] - pad),
                        "y": max(0, box["y"] + page.evaluate("window.scrollY") - pad),
                        "width": box["width"] + 2 * pad,
                        "height": box["height"] + 2 * pad,
                    },
                )
            elif full:
                # 긴 줄(트레이스 JSON 등)이 가로로 넘쳐도 화면 폭까지만 찍는다.
                height = page.evaluate("document.documentElement.scrollHeight")
                width = page.viewport_size["width"] if page.viewport_size else VIEWPORT["width"]
                page.screenshot(path=str(path), full_page=True, clip={"x": 0, "y": 0, "width": width, "height": height})
            else:
                page.screenshot(path=str(path))
        finally:
            page.evaluate("document.querySelectorAll('.__manual_mark').forEach(e => e.remove())")
        self.count += 1
        print(f"  [{self.dir.name}] {name}.png", flush=True)
        return path


def login(page: Page, username: str) -> None:
    page.goto(BASE + "/login")
    page.get_by_label("아이디").fill(username)
    page.get_by_label("비밀번호").fill(password())
    page.get_by_role("button", name="로그인").click()
    page.wait_for_url("**/chat**")
    page.wait_for_timeout(500)


def go(page: Page, path: str, wait: int = 1200) -> None:
    page.goto(BASE + path)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(wait)


def ask(page: Page, question: str, *, timeout: int = 240_000) -> float:
    """채팅에 질문하고 답이 끝날 때까지 기다린다. 걸린 초를 돌려준다."""
    box = page.locator(".chat-input textarea")
    box.fill(question)
    t0 = time.time()
    box.press("Enter")
    page.wait_for_timeout(500)
    page.wait_for_selector(".chat-input textarea:not([disabled])", timeout=timeout)
    page.wait_for_timeout(700)
    return time.time() - t0


def shot_turn(s: "Shooter", page: Page, name: str, **kw: Any) -> Path:
    """마지막 질문 말풍선부터 그 답까지만 잘라 찍는다."""
    q = page.locator(".chat-thread .bubble-user").last
    a = last_answer(page)
    a.scroll_into_view_if_needed()
    qb, ab = q.bounding_box(), a.bounding_box()
    assert qb and ab
    sy = page.evaluate("window.scrollY")
    top = min(qb["y"], ab["y"]) + sy - 10
    bottom = ab["y"] + ab["height"] + sy + 10
    left = ab["x"] - 10
    width = max(qb["x"] + qb["width"], ab["x"] + ab["width"]) - ab["x"] + 20
    return s.shot(page, name, region={"x": left, "y": top, "width": width, "height": bottom - top}, **kw)


def last_answer(page: Page) -> Locator:
    return page.locator(".chat-thread > .answer").last


def run(chapters: dict[str, Any], only: Sequence[str]) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        try:
            for key, fn in chapters.items():
                if only and key not in only:
                    continue
                print(f"== {key}", flush=True)
                fn(Shooter(browser, key))
        finally:
            browser.close()
