"""Neo4j 접속 스모크 — 개발·테스트 두 인스턴스가 따로 떠 있는지 본다(ADR-0007)."""

import pytest

from ontobot.config import get_settings
from ontobot.graph.client import create_driver, server_info

pytestmark = pytest.mark.integration


def _targets() -> list[tuple[str, str, str, str]]:
    s = get_settings()
    return [
        ("dev", s.neo4j_uri, s.neo4j_user, s.neo4j_password.get_secret_value()),
        ("test", s.test_neo4j_uri, s.test_neo4j_user, s.test_neo4j_password.get_secret_value()),
    ]


@pytest.mark.parametrize("target", _targets(), ids=lambda t: t[0])
async def test_instance_is_community_5(target: tuple[str, str, str, str]) -> None:
    _, uri, user, password = target
    if not password:
        pytest.skip("Neo4j 비밀번호 미설정 (.env)")
    driver = create_driver(uri, user, password)
    try:
        info = await server_info(driver)
    finally:
        await driver.close()
    assert info.edition == "community"
    assert info.version.startswith("5.")


def test_dev_and_test_instances_are_distinct() -> None:
    # 같은 인스턴스를 가리키면 테스트가 개발 그래프를 비운다.
    s = get_settings()
    assert s.neo4j_uri != s.test_neo4j_uri
