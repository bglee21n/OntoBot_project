"""감사 로그 기록(TRD 6.6, 9.3). payload 는 언제나 마스킹해서 남긴다.

호출자의 트랜잭션 안에서 행을 추가한다 — 감사 대상 변경과 감사 기록이 함께 커밋되거나 함께
롤백되어야 한다(변경은 됐는데 기록이 없는 상태를 만들지 않는다).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ontobot.db.models.security import AuditLog
from ontobot.security.pii import mask_payload

# 감사 이벤트 이름(TRD 9.3). 문자열을 호출부마다 적으면 오타가 조용히 새 이벤트가 된다.
LOGIN = "auth.login"
LOGIN_FAILED = "auth.login_failed"
LOGOUT = "auth.logout"
ROLE_CHANGED = "security.role_changed"
PERMISSION_CHANGED = "security.permission_changed"
GROUP_CHANGED = "security.group_changed"
GROUP_MEMBERS_CHANGED = "security.group_members_changed"
USER_CREATED = "security.user_created"
USER_STATUS_CHANGED = "security.user_status_changed"
USER_CONTACTS_CHANGED = "security.user_contacts_changed"
AUDIT_VIEWED = "security.audit_viewed"
TRACE_VIEWED = "security.trace_viewed"
RETENTION_PURGED = "ops.retention_purged"
TARGETS_CHANGED = "ops.targets_changed"
DRAFT_CREATED = "ontology.draft_created"
DRAFT_IMPORTED = "ontology.draft_imported"
DRAFT_EDITED = "ontology.draft_edited"
VERSION_PUBLISHED = "ontology.version_published"
SOURCE_CHANGED = "sync.source_changed"
MAPPING_CHANGED = "sync.mapping_changed"
SYNC_TRIGGERED = "sync.manual_run"
GRAPH_REBUILD = "graph.rebuild"
WORKSHOP_STARTED = "workshop.session_started"
WORKSHOP_ELEMENT_REVIEWED = "workshop.element_reviewed"
WORKSHOP_SUBMITTED = "workshop.submitted"
WORKSHOP_PUBLISHED = "workshop.published"
GOLDEN_CHANGED = "eval.golden_changed"
EVAL_RUN_STARTED = "eval.run_started"
OBJECT_EDIT_CLEARED = "objects.edit_cleared"
EDIT_CONFLICT_ACKNOWLEDGED = "objects.conflict_acknowledged"
ACTION_PREVIEWED = "action.previewed"
ACTION_EXECUTED = "action.executed"
ACTION_REJECTED = "action.rejected"
ACTION_FAILED = "action.failed"
ACTION_ROLLED_BACK = "action.rolled_back"
# 배포 콘솔(ADR-0014)이 작업이 끝난 뒤 `security.cli audit-deploy` 로 남긴다.
DEPLOY_EVENTS = frozenset(
    {
        "deploy.bundle_imported",
        "deploy.upgrade",
        "deploy.rollback",
        "deploy.backup",
        "deploy.deployer_update",
        "deploy.login",
        "deploy.login_failed",
    }
)


def record(
    db: AsyncSession,
    event: str,
    *,
    actor_id: int | None,
    target: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            event=event,
            actor_id=actor_id,
            target=target,
            payload=mask_payload(payload or {}),
        )
    )
