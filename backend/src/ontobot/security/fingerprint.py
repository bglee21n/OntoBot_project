"""권한 지문(TRD 9.1, 6.7 끝 문단) — 파생 저장소(의미 캐시 등)의 키에 넣는 값.

역할 집합과 게시 스키마 버전이 같으면 같은 지문이다. 캐시를 들이면 키에 이것을 넣어, 권한이 다른
사용자끼리 답변을 공유하지 않게 한다. MVP 는 캐시가 없으므로 함수와 테스트만 둔다.

역할 이름이 아니라 **역할 집합**이 기준이다 — 같은 권한을 가진 두 사용자는 같은 지문을 갖고,
역할이 하나라도 다르면(권한 변경 포함) 다른 지문이 된다. 타입 권한 표가 바뀌면 지문만으로는 알 수
없으므로, 캐시는 권한 변경 감사 이벤트로 비워야 한다(도입 시 결정).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


def fingerprint(roles: Iterable[str], schema_version_hash: str) -> str:
    body = json.dumps(
        {"roles": sorted(set(roles)), "schema": schema_version_hash},
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
