"""사용자에게 보이는 안내 문구의 정본(TRD 15절). 상태·실패 사유마다 한 곳에서 정한다.

``failed`` 문구는 ``empty`` 문구와 **모양부터 달라야** 한다 — "자료가 없다"는 그럴듯한 문장이
조회 장애를 가리면 사용자는 그것을 사실로 믿는다(PRD 5.0).
"""

from __future__ import annotations

NOT_QUERY = (
    '저는 등록된 데이터를 조회해 답합니다. 예를 들어 "위험등급이 3 이상인 시설은 몇 개야?"처럼 '
    "물어보세요."
)

FAILED = {
    "plan_invalid": "질문을 조회 조건으로 바꾸지 못했습니다. 대상과 조건을 더 구체적으로 다시 물어봐 주세요. "
    '(예: "변전소 중 위험등급 4 이상인 시설 목록")',
    "forbidden": "조회 권한이 없는 정보입니다.",
    "execution_error": "조회에 실패했습니다. 잠시 뒤 다시 시도해 주세요.",
    "timeout": "조회 시간이 초과되어 실패했습니다. 조건을 좁혀 다시 시도해 주세요.",
    "graph_unavailable": "관계 정보를 조회할 수 없습니다. 잠시 뒤 다시 시도해 주세요.",
    "too_broad": "조건에 맞는 대상이 너무 많습니다. 조건을 좁혀 다시 물어봐 주세요.",
    "llm_unavailable": "답변 모델에 연결하지 못해 실패했습니다. 잠시 뒤 다시 시도해 주세요.",
    "no_schema": "게시된 온톨로지가 없어 조회할 수 없습니다. 관리자에게 문의하세요.",
    "internal_error": "답변을 만드는 중 오류가 발생했습니다. 잠시 뒤 다시 시도해 주세요.",
}


def empty(conditions: str) -> str:
    return f"조회 조건({conditions})에 맞는 객체가 없습니다."


def ungrounded(conditions: str) -> str:
    """등급 ③ — 답변 본문을 버리고 이것을 보낸다."""
    return (
        "조회 결과에서 근거를 확인할 수 있는 답을 만들지 못했습니다. "
        f"조회 조건({conditions})의 결과를 아래 근거 목록에서 직접 확인해 주세요."
    )


def action_proposed(action: str) -> str:
    """``action_preview`` — 미리보기(실행 기록)를 만들었다. 실행은 승인 뒤다."""
    return (
        f"'{action}' 요청의 미리보기를 만들었습니다. 바뀔 값과 사전 조건을 확인하세요 — "
        "승인되어야 실행됩니다."
    )


def action_incomplete(action: str) -> str:
    """``action_preview`` — 대상이나 값이 대조되지 않아 미리보기를 만들지 않았다."""
    return (
        f"'{action}' 요청을 그대로 만들 수 없습니다. 아래 확인 사항을 보고 대상과 값을 고른 뒤 "
        "미리보기를 요청하세요."
    )
