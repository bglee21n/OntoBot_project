"""운영자 매뉴얼의 명령 화면 — 명령을 실제로 실행하고, 그 출력을 터미널 창 모양의 사진으로 만든다.

창 맨 위 줄에는 운영 서버에서 칠 명령(``shown``)을, 그 아래에는 촬영 PC 에서 실제로 실행한 명령(``real``)의
출력을 그대로 싣는다. 촬영 PC 의 설치본은 Docker Desktop VM 안에 있어 ``wsl -d ontobot -u root --`` 대신
배포 콘솔 컨테이너 안에서 같은 ``ontobotctl``·``docker compose`` 를 부른다(00장 "촬영 환경"에 밝힌다).
출력은 고치지 않는다 — 비밀값으로 보이는 값만 ``****`` 로 가린다.
"""

from __future__ import annotations

import html
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from playwright.sync_api import Page

DEPLOYER = "ontobot-deployer-deployer-1"
SECRET = re.compile(r"((?:PASSWORD|SECRET|TOKEN|_KEY)[A-Z_]*=)(\S+)")

_CSS = """
@font-face { font-family: TermLatin; src: local('Consolas'); unicode-range: U+0000-024F, U+2000-22FF, U+2500-257F; }
@font-face { font-family: TermKo; src: local('Malgun Gothic'); }
body { margin: 0; background: #fff; }
.win { width: 1180px; border: 1px solid #3c3c3c; border-radius: 8px; overflow: hidden;
       font: 13.5px/1.45 TermLatin, TermKo, monospace; box-shadow: 0 2px 8px rgba(0,0,0,.25); }
.bar { background: #2b2b2b; color: #ddd; padding: 7px 12px; font: 13px 'Malgun Gothic', sans-serif; }
.bar span { float: right; letter-spacing: 10px; color: #aaa; }
pre { font: inherit; margin: 0; padding: 12px 14px 16px; background: #0c0c0c; color: #cccccc; white-space: pre-wrap;
      word-break: break-all; }
.ps { color: #f9f1a5; }
.cmd { color: #ffffff; }
.step { color: #61d6d6; }
.err { color: #f48771; }
.cut { color: #888; }
"""


def ctl(*args: str) -> list[str]:
    """설치본의 ontobotctl(배포 콘솔 컨테이너 안)."""
    return ["docker", "exec", DEPLOYER, "ontobotctl", *args]


def sh(script: str) -> list[str]:
    """설치 폴더($ONTOBOT_HOME)에서 셸 한 줄 — docker compose·cat 등."""
    return ["docker", "exec", "-i", DEPLOYER, "sh", "-c", f'cd "$ONTOBOT_HOME" && {script}']


def run(real: Sequence[str], *, timeout: int = 900, stdin: str | None = None) -> tuple[int, str]:
    p = subprocess.run(
        list(real), capture_output=True, input=stdin, timeout=timeout, encoding="utf-8", errors="replace"
    )
    return p.returncode, (p.stdout + p.stderr).replace("\r\n", "\n")


def render(page: Page, out: Path, shown: str | Sequence[str], text: str, *, title: str, prompt: str) -> None:
    def line(s: str) -> str:
        e = html.escape(s)
        if s.startswith("==> "):
            return f"<span class='step'>{e}</span>"
        if s.startswith(("오류:", "경고:", "Error", "error")):
            return f"<span class='err'>{e}</span>"
        if s.startswith("…"):
            return f"<span class='cut'>{e}</span>"
        return e

    cmds = [shown] if isinstance(shown, str) else list(shown)
    head = "\n".join(f"<span class='ps'>{html.escape(prompt)}</span><span class='cmd'>{html.escape(c)}</span>" for c in cmds)
    body = "\n".join(line(s) for s in text.rstrip("\n").split("\n")) if text.strip() else ""
    tail = f"\n<span class='ps'>{html.escape(prompt)}</span>"
    doc = (
        f"<!doctype html><meta charset='utf-8'><style>{_CSS}</style>"
        f"<div class='win'><div class='bar'>{html.escape(title)}<span>—▢✕</span></div>"
        f"<pre>{head}{chr(10) + body if body else ''}{tail}</pre></div>"
    )
    page.set_content(doc)
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(150)
    out.parent.mkdir(parents=True, exist_ok=True)
    page.locator(".win").screenshot(path=str(out))


class Term:
    """한 장(章)의 명령 화면들. ``shot`` 은 실행한 출력을 돌려준다(본문 확인용)."""

    def __init__(self, page: Page, root: Path, chapter: str) -> None:
        self.page = page
        self.dir = root / chapter
        self.count = 0

    def shot(
        self,
        name: str,
        shown: str | Sequence[str],
        real: Sequence[str] | None = None,
        *,
        text: str | None = None,
        keep: tuple[int, int] | None = None,
        grep: str | None = None,
        timeout: int = 900,
        stdin: str | None = None,
        expect_fail: bool = False,
        title: str = "관리자: Windows PowerShell",
        prompt: str = "PS C:\\Users\\ontobot-svc> ",
    ) -> str:
        """``real`` 을 실행해 찍는다(``text`` 를 주면 실행하지 않고 그 출력을 싣는다 — 이미 실행한 결과).

        ``keep=(앞, 뒤)`` 는 긴 출력의 앞·뒤 줄만 남기고 가운데를 "… (n줄 생략)" 으로 줄인다.
        """
        if text is None:
            assert real is not None
            code, text = run(real, timeout=timeout, stdin=stdin)
            if code != 0 and not expect_fail:
                print(f"  ! {name}: 종료 코드 {code}\n{text[-800:]}")
        text = SECRET.sub(lambda m: m.group(1) + "****", text)
        lines = text.rstrip("\n").split("\n")
        if grep:
            lines = [s for s in lines if re.search(grep, s)]
        if keep and len(lines) > keep[0] + keep[1] + 1:
            cut = len(lines) - keep[0] - keep[1]
            lines = [*lines[: keep[0]], f"… ({cut}줄 생략)", *(lines[-keep[1] :] if keep[1] else [])]
        out = self.dir / f"{name}.png"
        if os.environ.get("ONTOBOT_CAPTURE_WEB_ONLY") and out.exists():
            # 웹 화면만 다시 찍을 때 — 명령은 실행(뒤 장이 기대는 상태)하되 이미 있는 명령 사진은 그대로 둔다.
            return text
        render(self.page, out, shown, "\n".join(lines), title=title, prompt=prompt)
        self.count += 1
        print(f"  [{self.dir.name}] {name}.png (명령)", flush=True)
        return text
