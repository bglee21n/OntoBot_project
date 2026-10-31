"""녹화 응답(카세트) — PR 마다 실제 모델 없이 파이프라인 회귀를 돌린다(구현 계획 Phase 7).

- ``record``: 실제 Ollama 로 보내고, 요청 → 응답 본문을 카세트에 적는다.
- ``replay``: 카세트에서만 답한다. 없는 요청은 **실패**다(HTTP 404 → ``llm_unavailable``) —
  지어내지 않는다. 프롬프트·스키마·데이터 블록이 바뀌면 키가 달라져 놓친 요청(``misses``)이
  생긴다. 그때는 실제 모델로 다시 녹화하고 게이트를 다시 통과해야 한다(녹화는 그 시점 모델의 답을
  고정할 뿐 품질 근거가 아니다).

키는 요청 본문(모델·메시지·형식·옵션)의 정규 JSON 해시다. Ollama 어댑터의 ``transport`` 훅을 쓰므로
Ollama 공급자만 녹화한다(평가 설정은 로컬 공급자다). 임베딩(``/api/embed``, Phase 16d)은 본문이
다르므로 같은 키로 겹치지 않고, 항목에 ``embeddings`` 를 남긴다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

import httpx

from ontobot.llm.adapters import ollama

Mode = Literal["live", "record", "replay"]


def key(body: dict[str, Any]) -> str:
    canon = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class Cassette:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.entries: dict[str, dict[str, Any]] = {}
        if path.is_file():
            self.entries = json.loads(path.read_text(encoding="utf-8"))
        self.misses = 0
        self.used: set[str] = set()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 이번 실행이 쓴 항목만 남긴다 — 예전 프롬프트의 응답이 쌓이지 않게.
        kept = {k: self.entries[k] for k in sorted(self.used) if k in self.entries}
        self.path.write_text(
            json.dumps(kept, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )


class _Recording(httpx.AsyncBaseTransport):
    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # 어댑터는 호출마다 클라이언트를 닫으며 전송층도 닫는다 — 실제 전송층은 요청마다 만든다.
        # 타임아웃은 요청의 extensions 로 함께 온다.
        async with httpx.AsyncHTTPTransport() as inner:
            res = await inner.handle_async_request(request)
            raw = await res.aread()
        if res.status_code == 200:
            k = key(body)
            data = json.loads(raw)
            entry: dict[str, Any] = {"model": body.get("model")}
            if "embeddings" in data:
                entry["embeddings"] = data["embeddings"]
            else:
                entry["message"] = {"content": data["message"]["content"]}
            self.cassette.entries[k] = entry
            self.cassette.used.add(k)
        # 본문은 이미 풀었다 — content-encoding 등 원래 헤더를 옮기면 다시 풀려다 깨진다.
        return httpx.Response(
            res.status_code, content=raw, headers={"content-type": "application/json"}
        )


class _Replaying(httpx.AsyncBaseTransport):
    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        k = key(json.loads(request.content))
        hit = self.cassette.entries.get(k)
        if hit is None:
            self.cassette.misses += 1
            return httpx.Response(404, json={"error": "cassette miss", "key": k})
        self.cassette.used.add(k)
        if "embeddings" in hit:
            return httpx.Response(200, json={"embeddings": hit["embeddings"]})
        return httpx.Response(200, json={"message": hit["message"]})


@contextmanager
def use(mode: Mode, path: Path | None) -> Iterator[Cassette | None]:
    """평가 동안 Ollama 어댑터의 전송층을 바꾼다. 나갈 때 되돌린다(녹화면 저장)."""
    if mode == "live":
        yield None
        return
    if path is None:
        raise ValueError("record·replay 는 카세트 경로가 필요합니다")
    cassette = Cassette(path)
    if mode == "record":
        cassette.entries = {}
    before = ollama.transport
    ollama.transport = _Recording(cassette) if mode == "record" else _Replaying(cassette)
    try:
        yield cassette
    finally:
        ollama.transport = before
        if mode == "record":
            cassette.save()
