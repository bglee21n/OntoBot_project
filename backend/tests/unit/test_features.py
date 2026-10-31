"""Phase 18 — 음성 입력 플래그(결정 13): 폐쇄망이면 설정과 상관없이 꺼진다."""

from __future__ import annotations

import pytest

from ontobot.config import Settings
from ontobot.features import features


@pytest.mark.parametrize(
    ("closed", "voice", "expected"),
    [(True, True, False), (True, False, False), (False, False, False), (False, True, True)],
)
def test_voice_input_only_outside_closed_network(closed: bool, voice: bool, expected: bool) -> None:
    f = features(Settings(closed_network=closed, voice_input=voice))
    assert f.voice_input is expected and f.closed_network is closed


def test_defaults_are_closed_network_without_voice() -> None:
    f = features(Settings(_env_file=None))  # type: ignore[call-arg]
    assert (f.closed_network, f.voice_input) == (True, False)
