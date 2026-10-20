import pytest

from ontobot.security.auth import MIN_PASSWORD_LENGTH, hash_password
from ontobot.security.fingerprint import fingerprint
from ontobot.security.pii import MASK, Tokenizer, mask_payload, mask_text
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


# ---------------------------------------------- Phase 5: 값 마스킹·가역 토큰화·권한 지문


def test_known_values_are_masked_longest_first() -> None:
    text = "담당 김가상 팀장(김가상) 연락처 010-0000-0001"
    assert mask_text(text, ["김가상", "김가상 팀장"]) == "담당 ***(***) 연락처 ***"


def test_tokenizer_round_trip() -> None:
    tok = Tokenizer(["김가상", "강남지사 김가상 팀장"])
    original = "강남지사 김가상 팀장에게 010-0000-0001 로 연락. 김가상 님 확인."
    sent = tok.tokenize(original)
    assert "김가상" not in sent and "010-0000-0001" not in sent
    assert sent.count("⟦P") == 3
    assert tok.restore(sent) == original
    # 모델 응답에 같은 토큰이 다시 나오면 같은 원값으로 돌아온다.
    reply = "담당자는 " + sent.split(" ")[0] + " 입니다."
    assert "강남지사 김가상 팀장" in tok.restore(reply)


def test_tokenizer_same_value_same_token_and_unknown_token_stays() -> None:
    tok = Tokenizer(["김가상"])
    assert tok.tokenize("김가상, 김가상") == "⟦P1⟧, ⟦P1⟧"
    # 모델이 지어낸 토큰은 되돌릴 원값이 없다 — 그대로 남아 근거 대조에 실패한다(안전한 쪽).
    assert tok.restore("⟦P9⟧") == "⟦P9⟧"


def test_tokenizer_ignores_one_letter_values() -> None:
    tok = Tokenizer(["김", ""])
    assert tok.tokenize("김밥 김치") == "김밥 김치"


def test_fingerprint_depends_on_role_set_and_schema_only() -> None:
    a = fingerprint(["user", "modeler"], "h1")
    assert a == fingerprint(["modeler", "user", "user"], "h1")
    assert a != fingerprint(["user"], "h1")
    assert a != fingerprint(["user", "modeler"], "h2")
