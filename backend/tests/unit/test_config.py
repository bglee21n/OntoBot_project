from pydantic import SecretStr
from sqlalchemy.engine import make_url

from ontobot.config import Settings


def _settings(password: str) -> Settings:
    return Settings(_env_file=None, db_password=SecretStr(password), db_host="db.local")


def test_password_with_url_delimiters_keeps_host() -> None:
    # 문자열 조립이면 '@'가 호스트 구분자로 읽혀 엉뚱한 호스트로 간다.
    url = _settings("t3st!p@ss").database_url()
    rendered = url.render_as_string(hide_password=False)
    parsed = make_url(rendered)
    assert parsed.host == "db.local"
    assert parsed.password == "t3st!p@ss"
    assert parsed.database == "ontobot_db"


def test_password_never_appears_in_repr() -> None:
    s = _settings("t3st!p@ss")
    assert "t3st!p@ss" not in repr(s)
    assert "t3st!p@ss" not in str(s.database_url())
