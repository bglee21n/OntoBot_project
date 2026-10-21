"""대시보드 — "지금 배포할 수 있는가"를 신호등으로.

판정은 기계적으로 알 수 있는 것만 한다(CLAUDE.md "깨면 조용히 틀리는 규칙" 중):
- 검사 결과가 **지금 소스**로 돌린 것인가(작업 시작 때의 커밋 = 지금 HEAD, 그때·지금 모두 커밋하지
  않은 변경 없음)
- API 파일을 바꿨는데 OpenAPI(프론트 타입)를 재생성하지 않았나
- 프롬프트·스키마 파일이 마지막 카세트 녹화 이후 바뀌었나(→ eval-record 필요)
- 커밋하지 않은 변경(→ 번들이 -dirty)
"""

from __future__ import annotations

from typing import Any, Literal

from opsjobs import Job, JobStore

from ontobot_devconsole import git
from ontobot_devconsole.settings import Settings

Light = Literal["ok", "warn", "fail", "unknown"]
CHECKS = ("lint", "test-unit", "test-contract", "test-int", "eval-replay")
# 바뀌면 실제 모델 게이트를 다시 녹화해야 하는 파일(CLAUDE.md — 프롬프트·스키마·모델·추출).
PROMPT_FILES = [
    "backend/src/ontobot/query/planner.py",
    "backend/src/ontobot/query/plan.py",
    "backend/src/ontobot/ontology/summary.py",
    "backend/src/ontobot/chat/answerer.py",
    "backend/src/ontobot/query/records.py",
    "backend/src/ontobot/workshop/extractor.py",
    "backend/src/ontobot/workshop/interviewer.py",
    "config/llm.yaml",
    "domain-packs/sample/ontology.yaml",
    "domain-packs/sample/golden.yaml",
    "domain-packs/sample/seed",
]
CASSETTE = "domain-packs/sample/llm_cassette.json"
API_FILES = ("backend/src/ontobot/api/", "backend/src/ontobot/chat/stream.py")
OPENAPI_FILE = "frontend/src/api/openapi.json"


def _latest(store: JobStore, kinds: tuple[str, ...]) -> Job | None:
    found = [j for k in kinds if (j := store.last(k)) is not None]
    return max(found, key=lambda j: j.id) if found else None


def check_light(job: Job | None, head: str | None, dirty_now: bool) -> dict[str, Any]:
    if job is None:
        return {"light": "unknown", "detail": "돌린 적 없음", "job": None}
    fresh = job.meta.get("commit") == head and not job.meta.get("dirty") and not dirty_now
    light: Light
    if job.status == "running":
        light = "unknown"
    elif job.status != "succeeded":
        light = "fail"
    elif fresh:
        light = "ok"
    else:
        light = "warn"
    detail = {
        "running": "진행 중",
        "succeeded": "통과" if fresh else "통과 — 그 뒤 소스가 바뀜(다시 돌릴 것)",
    }.get(job.status, "실패")
    return {
        "light": light,
        "detail": detail,
        "job": {
            "id": job.id,
            "status": job.status,
            "finished_at": job.finished_at,
            "result": job.result,
        },
    }


def build(settings: Settings, store: JobStore) -> dict[str, Any]:
    root = settings.root
    st = git.status(root)
    changed_paths = {f.path for f in st.files}
    lights: dict[str, Any] = {}
    for name in CHECKS:
        kinds = (
            (f"task:{name}", "check-all", "release")
            if name != "eval-replay"
            else (
                "task:eval-replay",
                "release",
            )
        )
        lights[name] = check_light(_latest(store, kinds), st.head, st.dirty)

    warnings: list[dict[str, Any]] = []
    if st.dirty:
        warnings.append(
            {
                "kind": "dirty",
                "text": f"커밋하지 않은 변경 {len(st.files)}개"
                " — 이대로 만든 번들은 -dirty(서버 거부)",
            }
        )
    api_changed = sorted(p for p in changed_paths if p.startswith(API_FILES))
    if api_changed and OPENAPI_FILE not in changed_paths:
        warnings.append(
            {
                "kind": "openapi",
                "text": "API 파일을 바꿨는데 OpenAPI 를 재생성하지 않았습니다 → 'OpenAPI 재생성'",
                "files": api_changed[:10],
            }
        )
    ref = git.last_commit_touching(root, CASSETTE)
    if ref is not None:
        stale = git.changed_since(root, ref, PROMPT_FILES)
        if stale and CASSETTE not in changed_paths:
            warnings.append(
                {
                    "kind": "cassette",
                    "text": "프롬프트·스키마·모델·샘플 팩이 마지막 카세트 녹화 뒤 바뀌었습니다"
                    " → '실제 모델 평가 + 녹화' 후 카세트 커밋",
                    "files": stale[:10],
                }
            )
    key = settings.signing_key()
    return {
        "git": st.as_dict(),
        "checks": lights,
        "warnings": warnings,
        "signing": {
            "key_configured": key is not None,
            "key_exists": bool(key and key.is_file()),
            "pub_exists": bool(settings.signing_pub() and settings.signing_pub().is_file()),  # type: ignore[union-attr]
        },
    }
