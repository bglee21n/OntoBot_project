from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from ontobot import config, health
from ontobot.llm import gateway
from ontobot.llm.adapters import ollama
from ontobot.llm.config import LlmConfig
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


# ---------------------------------------------------------------- /readyz (TRD 15절)


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get(path)


def _stub(monkeypatch: pytest.MonkeyPatch, *, pg: object, llm: object) -> None:
    async def postgres() -> health.Component:
        if isinstance(pg, Exception):
            raise pg
        return health.Component("ok")

    async def llm_check() -> health.Component:
        return llm  # type: ignore[return-value]

    monkeypatch.setattr(health, "_postgres", postgres)
    monkeypatch.setattr(health, "_llm", llm_check)


async def test_readyz_is_unavailable_when_postgres_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, pg=OSError("refused"), llm=health.Component("ok"))
    res = await _get("/readyz")
    assert res.status_code == 503
    body = res.json()
    assert body["status"] == "unavailable"
    assert body["postgres"] == {"status": "down", "detail": "OSError"}
    # ASGITransport 는 lifespan 을 돌리지 않는다 — 드라이버가 없으면 설정 없음으로 본다.
    assert body["neo4j"]["status"] == "not_configured"


async def test_readyz_is_degraded_not_unavailable_when_only_graph_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Neo4j 가 없어도 단일 타입 질의는 답한다(TRD 5.5) — 503 으로 내리지 않는다.
    _stub(monkeypatch, pg=None, llm=health.Component("ok"))
    res = await _get("/readyz")
    assert res.status_code == 200 and res.json()["status"] == "degraded"


async def test_readyz_is_unavailable_when_llm_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, pg=None, llm=health.Component("down", "local: ConnectError"))
    res = await _get("/readyz")
    assert res.status_code == 503 and res.json()["llm"]["detail"] == "local: ConnectError"


async def test_readyz_check_that_hangs_is_reported_as_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    async def hang() -> health.Component:
        await asyncio.sleep(10)
        return health.Component("ok")

    monkeypatch.setattr(health, "CHECK_TIMEOUT_S", 0.05)
    assert await health._bounded(hang()) == health.Component("down", "timeout")


def _llm_config(extra_provider: bool = False) -> LlmConfig:
    providers: dict[str, object] = {"local": {"adapter": "ollama", "base_url": "http://o:1"}}
    roles: dict[str, object] = {
        "planner": {"provider": "local", "model": "gemma4:e4b"},
        "answerer": {"provider": "local", "model": "tiny"},
    }
    if extra_provider:
        providers["ext"] = {
            "adapter": "openai_compat",
            "base_url": "https://llm.example",
            "api_key_env": "ONTOBOT_TEST_ABSENT_KEY",
            "external": True,
        }
        roles["extractor"] = {"provider": "ext", "model": "x"}
    return LlmConfig.model_validate({"providers": providers, "roles": roles})


async def test_check_providers_lists_models_ollama_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"models": [{"name": "tiny:latest"}]})

    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(handler))
    monkeypatch.delenv("ONTOBOT_TEST_ABSENT_KEY", raising=False)
    out = await gateway.check_providers(_llm_config(extra_provider=True), timeout_s=1)
    # 목록만 본다 — 모델을 불러오는 /api/chat·/api/generate 를 부르지 않는다.
    assert [r.url.path for r in seen] == ["/api/tags"]
    assert out["local"] == gateway.ProviderReadiness(
        ok=False, probed=True, detail="missing_models: gemma4:e4b"
    )
    # 외부 공급자에는 묻지 않는다(폐쇄망·과금) — 키 설정만 본다.
    assert out["ext"] == gateway.ProviderReadiness(ok=False, probed=False, detail="api_key_missing")


async def test_check_providers_reports_unreachable_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(ollama, "transport", httpx.MockTransport(handler))
    out = await gateway.check_providers(_llm_config(), timeout_s=1)
    assert out["local"] == gateway.ProviderReadiness(ok=False, probed=True, detail="ConnectError")


# ---------------------------------------------------------------- 프론트 서빙(설치본)
@pytest.fixture
def frontend_dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>", encoding="utf-8")
    (dist / "assets" / "app-1a2b.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("nope", encoding="utf-8")
    monkeypatch.setenv("ONTOBOT_FRONTEND_DIST", str(dist))
    config.get_settings.cache_clear()
    try:
        yield dist
    finally:
        monkeypatch.delenv("ONTOBOT_FRONTEND_DIST")
        config.get_settings.cache_clear()


@pytest.mark.usefixtures("frontend_dist")
async def test_frontend_is_served_with_client_side_routes_falling_back_to_index() -> None:
    root = await _get("/")
    assert root.status_code == 200 and root.text == "<html>app</html>"
    assert root.headers["cache-control"] == "no-cache"
    deep = await _get("/chat/3")
    assert deep.status_code == 200 and deep.text == "<html>app</html>"
    asset = await _get("/assets/app-1a2b.js")
    assert asset.status_code == 200 and asset.text == "console.log(1)"
    assert (await _get("/favicon.svg")).text == "<svg/>"


@pytest.mark.usefixtures("frontend_dist")
async def test_frontend_fallback_does_not_hide_api_404_or_escape_dist() -> None:
    api = await _get("/api/v1/nope")
    assert api.status_code == 404
    assert api.headers["content-type"].startswith("application/problem+json")
    assert (await _get("/healthz")).json() == {"status": "ok"}
    escaped = await _get("/..%2Fsecret.txt")
    assert "nope" not in escaped.text


def test_missing_frontend_build_fails_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONTOBOT_FRONTEND_DIST", str(tmp_path / "missing"))
    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="프론트 빌드 산출물"):
            create_app()
    finally:
        monkeypatch.delenv("ONTOBOT_FRONTEND_DIST")
        config.get_settings.cache_clear()
