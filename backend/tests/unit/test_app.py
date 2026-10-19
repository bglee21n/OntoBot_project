import httpx

from ontobot.main import create_app


async def test_healthz_reports_process_alive_without_touching_db() -> None:
    # ASGITransport 는 lifespan 을 돌리지 않는다.
    # DB 없이도 200이어야 한다(/healthz 는 외부 의존을 보지 않는다).
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


async def test_unknown_route_is_problem_json() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        res = await client.get("/api/v1/nope")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["status"] == 404
