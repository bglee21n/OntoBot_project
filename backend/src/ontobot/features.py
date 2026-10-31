"""화면 기능 플래그(Phase 18, TRD 17.1 결정 13) — 화면은 ``GET /config/features`` 로 받는다.

판정은 여기 하나뿐이다. 폐쇄망(``ONTOBOT_CLOSED_NETWORK``, 기본 켜짐)이면 외부 서버를 부르는 기능은
설정과 상관없이 끈다 — 화면은 꺼진 기능의 단추 자체를 그리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ontobot.config import Settings


@dataclass(frozen=True)
class Features:
    closed_network: bool
    # 브라우저 음성 인식 — 음성이 브라우저 공급자 서버로 간다(화면이 알린다).
    voice_input: bool


def features(settings: Settings) -> Features:
    return Features(
        closed_network=settings.closed_network,
        voice_input=settings.voice_input and not settings.closed_network,
    )
