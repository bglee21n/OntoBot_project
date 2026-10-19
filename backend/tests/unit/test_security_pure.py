import pytest

from ontobot.security.auth import MIN_PASSWORD_LENGTH, hash_password
from ontobot.security.pii import MASK, mask_payload, mask_text
from ontobot.security.rbac import has_role


@pytest.mark.parametrize(
    "text",
    ["담당자 010-1234-5678", "01012345678", "02-123-4567", "a.b+c@example.co.kr", "900101-1234567"],
)
def test_masks_personal_values(text: str) -> None:
    assert MASK in mask_text(text)


@pytest.mark.parametrize(
    "text",
    ["202609091730", "20260909", "F-001", "risk_grade=3", "2026-09-09 17:30", "1,234,567"],
)
def test_does_not_mask_codes_and_datetimes(text: str) -> None:
    # 업무 코드·일시를 가리면 감사 로그로 무슨 일이 있었는지 읽을 수 없다.
    assert mask_text(text) == text


def test_payload_masks_recursively_and_hides_secret_keys() -> None:
    original = {
        "username": "kim",
        "password": "hunter2-hunter2",
        "contacts": [{"phone": "010-1111-2222"}],
        "nested": {"api_key": "x"},
    }
    masked = mask_payload(original)
    assert masked == {
        "username": "kim",
        "password": MASK,
        "contacts": [{"phone": MASK}],
        "nested": {"api_key": MASK},
    }
    assert original["password"] == "hunter2-hunter2"  # 원본은 그대로


def test_has_role_any_of() -> None:
    assert has_role({"modeler"}, "modeler", "admin")
    assert not has_role({"user"}, "modeler", "admin")
    assert not has_role(set(), "user")


def test_short_password_is_refused() -> None:
    with pytest.raises(ValueError):
        hash_password("x" * (MIN_PASSWORD_LENGTH - 1))
    assert hash_password("x" * MIN_PASSWORD_LENGTH).startswith("$argon2")


def test_env_secret_ref_falls_back_to_env_file(tmp_path, monkeypatch) -> None:
    from ontobot import config
    from ontobot.security import secrets

    env = tmp_path / ".env"
    env.write_text(
        "SRCX_HOST=h\nSRCX_DB=d\nSRCX_USER=u\nSRCX_PASSWORD=p@ss:w/rd\n", encoding="utf-8"
    )
    monkeypatch.setitem(config.Settings.model_config, "env_file", env)
    config.get_settings.cache_clear()
    monkeypatch.setenv("SRCX_HOST", "from-process")
    try:
        creds = secrets.resolve_db("env:SRCX")
    finally:
        config.get_settings.cache_clear()
    assert creds.host == "from-process"  # 프로세스 환경변수가 먼저
    assert (creds.database, creds.user, creds.port) == ("d", "u", 5432)
    assert creds.password.get_secret_value() == "p@ss:w/rd"
    assert "p@ss" not in repr(creds)
