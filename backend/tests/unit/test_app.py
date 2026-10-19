from fastapi.testclient import TestClient

from ontobot.main import create_app


def test_healthz_reports_process_alive_without_touching_db() -> None:
    # DB 설정이 없어도 200이어야 한다 — /healthz는 외부 의존을 보지 않는다.
    with TestClient(create_app()) as client:
        res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
