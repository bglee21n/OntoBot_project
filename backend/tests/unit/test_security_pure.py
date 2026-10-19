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
