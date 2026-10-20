"""Action 정의·규칙·발송 채널(PRD 5.3, TRD 17.1) — DB 없이."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from ontobot.actions import rules
from ontobot.notify import channels
from ontobot.notify.config import NotifyConfig, SmsTableChannel
from ontobot.notify.config import load as load_notify
from ontobot.ontology import importer
from ontobot.ontology.models import ActionTypeDef, OntologyDocument, check_document
from ontobot.security import rbac
from ontobot.security.pii import mask_contact

SAMPLE = Path(__file__).resolve().parents[3] / "domain-packs" / "sample" / "ontology.yaml"


def _sample() -> OntologyDocument:
    return importer.parse(SAMPLE.read_text(encoding="utf-8"), "yaml")


def _action(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "api_name": "adjust_risk",
        "display_name": "위험등급 조정",
        "target_type": "Facility",
        "parameters": [
            {"api_name": "grade", "display_name": "새 등급", "data_type": "int"},
            {
                "api_name": "note",
                "display_name": "사유",
                "data_type": "string",
                "required": False,
            },
        ],
        "preconditions": [{"property": "risk_grade", "op": "not_null"}],
        "changes": [{"property": "risk_grade", "param": "grade"}],
        "notify": [
            {"channel": "app", "to_roles": ["modeler"], "template": "{target} 등급 {grade}"},
            {"channel": "sms", "to_roles": ["admin"], "template": "{target}({target_pk}) {grade}"},
        ],
        "approval": {"roles": ["modeler"]},
        "compensation": "[정정] {target} 조정 취소",
    }
    base.update(over)
    return base


def _doc_with(**over: Any) -> OntologyDocument:
    doc = _sample()
    return doc.model_copy(update={"action_types": [ActionTypeDef.model_validate(_action(**over))]})


# ---------------------------------------------------------------- 정의·게시 전 검사


def test_valid_action_passes_publish_checks() -> None:
    assert check_document(_doc_with()) == []


def test_document_without_actions_keeps_its_content_hash() -> None:
    # Phase 9 이전 게시본의 content_hash 가 그대로 맞아야 한다(권한 지문·버전 비교).
    doc = _sample()
    assert "action_types" not in json.loads(doc.canonical_json())
    assert "action_types" in json.loads(_doc_with().canonical_json())


@pytest.mark.parametrize(
    "over",
    [
        {"changes": [{"property": "risk_grade", "param": "grade", "value": 3}]},
        {"approval": {"roles": []}},
        {"notify": [{"channel": "app", "to_property": "name", "template": "x"}]},
        {"changes": [], "notify": []},
        {"parameters": [{"api_name": "target", "display_name": "x", "data_type": "string"}]},
    ],
)
def test_malformed_action_is_rejected_by_the_model(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ActionTypeDef.model_validate(_action(**over))


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"changes": [{"property": "facility_id", "value": "X"}]}, "pk 속성"),
        ({"changes": [{"property": "name", "value": "X"}]}, "제목 속성"),
        ({"changes": [{"property": "owner_org_id", "value": "O"}]}, "링크(FK)"),
        ({"changes": [{"property": "nope", "value": 1}]}, "속성 'nope' 이 없습니다"),
        ({"changes": [{"property": "risk_grade", "param": "note"}]}, "자료형"),
        ({"changes": [{"property": "facility_type", "value": "댐"}]}, "enum 값이 아닙니다"),
        ({"target_type": "Ghost"}, "대상 타입"),
        (
            {"notify": [{"channel": "sms", "to_property": "region_code", "template": "x"}]},
            "pii_kind 가 phone",
        ),
        (
            {"notify": [{"channel": "app", "to_roles": ["user"], "template": "{who}"}]},
            "자리표시자",
        ),
        ({"compensation": None}, "정정 문구"),
        ({"preconditions": [{"property": "ghost", "op": "is_null"}]}, "ghost"),
    ],
)
def test_publish_checks_catch_unsafe_actions(over: dict[str, Any], needle: str) -> None:
    issues = check_document(_doc_with(**over))
    assert any(needle in i.message for i in issues), issues


def test_enum_parameter_must_fit_the_property() -> None:
    issues = check_document(
        _doc_with(
            parameters=[
                {
                    "api_name": "kind",
                    "display_name": "유형",
                    "data_type": "enum",
                    "enum_values": ["변전소", "댐"],
                }
            ],
            changes=[{"property": "facility_type", "param": "kind"}],
            notify=[],
            compensation=None,
        )
    )
    assert any("enum 값 밖" in i.message for i in issues)


# ---------------------------------------------------------------- 규칙(순수)


def _defs() -> tuple[ActionTypeDef, Any]:
    doc = _doc_with()
    a = doc.action_types[0]
    t = doc.object_type("Facility")
    return a, t


def test_params_are_converted_and_errors_reported_together() -> None:
    a, _ = _defs()
    ok, errors = rules.convert_params(a, {"grade": "4"})
    assert errors == [] and ok == {"grade": 4, "note": None}
    _, errors = rules.convert_params(a, {"grade": "x", "who": 1})
    assert len(errors) == 2 and any("who" in e for e in errors)
    _, errors = rules.convert_params(a, {})
    assert errors == ["'새 등급' 값이 필요합니다"]


def test_conditions_use_typed_comparison() -> None:
    _, t = _defs()
    conds = [
        {"property": "risk_grade", "op": "gte", "value": 3},
        {"property": "capacity_kw", "op": "lt", "value": "10.5"},
        {"property": "facility_type", "op": "in", "value": ["변전소", "철탑"]},
        {"property": "installed_on", "op": "is_null"},
    ]
    from ontobot.ontology.models import ActionCondition

    cs = [ActionCondition.model_validate(c) for c in conds]
    visible = {
        "risk_grade": 4,
        "capacity_kw": "9.75",
        "facility_type": "철탑",
        "installed_on": None,
    }
    assert [r.ok for r in rules.check_conditions(t, cs, visible)] == [True, True, True, True]
    visible = {
        "risk_grade": 2,
        "capacity_kw": "12",
        "facility_type": "전주",
        "installed_on": "2020-01-01",
    }
    assert [r.ok for r in rules.check_conditions(t, cs, visible)] == [False, False, False, False]


def test_render_replaces_known_names_only() -> None:
    assert (
        rules.render("{target} → {grade}", {"target": "변전소 1", "grade": "4"}) == "변전소 1 → 4"
    )
    # 형식 지정·속성 접근은 자리표시자가 아니다 — 그대로 남는다.
    assert rules.render("{grade.__class__} {grade!r} {x}", {"grade": "4"}) == (
        "{grade.__class__} {grade!r} "
    )


def test_planned_changes_store_the_object_json_shape() -> None:
    a, t = _defs()
    changes = rules.planned_changes(a, t, {"grade": 5}, {"risk_grade": 2})
    assert [(c.property, c.before, c.after, c.after_display) for c in changes] == [
        ("risk_grade", 2, 5, "5")
    ]


def test_mask_contact_keeps_only_a_hint() -> None:
    assert mask_contact("kim.gasang@corp.local") == "k***@corp.local"
    assert mask_contact("010-1234-5678") == "***-5678"
    assert mask_contact("12") == "***"


# ---------------------------------------------------------------- 권한 판정


def test_action_permission_rules() -> None:
    assert rbac.can_request_action({"user"}, [])
    assert not rbac.can_request_action({"user"}, ["modeler"])
    # 요청자 본인은 allow_self 일 때만 — 승인 역할이 있어도 마찬가지다.
    assert not rbac.can_decide_action({"modeler"}, ["modeler"], allow_self=False, is_requester=True)
    assert rbac.can_decide_action({"user"}, ["modeler"], allow_self=True, is_requester=True)
    assert rbac.can_decide_action({"modeler"}, ["modeler"], allow_self=False, is_requester=False)
    assert not rbac.can_decide_action({"user"}, ["modeler"], allow_self=False, is_requester=False)
    assert rbac.can_rollback_action({"admin"}, ["modeler"])
    assert not rbac.can_rollback_action({"user"}, ["modeler"])


# ---------------------------------------------------------------- 발송 채널


def test_missing_notify_config_disables_channels(tmp_path: Path) -> None:
    cfg = load_notify(tmp_path / "none.yaml")
    assert not cfg.enabled("email") and not cfg.enabled("sms")


def test_fake_dir_is_relative_to_the_repo(tmp_path: Path) -> None:
    f = tmp_path / "n.yaml"
    f.write_text("channels:\n  sms: {adapter: fake, dir: out}\n", encoding="utf-8")
    cfg = load_notify(f, base=tmp_path)
    assert cfg.is_fake("sms") and cfg.channels.sms.dir == tmp_path / "out"  # type: ignore[union-attr]


async def test_fake_channel_writes_a_line(tmp_path: Path) -> None:
    cfg = NotifyConfig.model_validate(
        {"channels": {"email": {"adapter": "fake", "dir": str(tmp_path)}}}
    )
    await channels.send(cfg, channels.OutMessage("email", "a@b.cd", "제목", "본문"))
    line = json.loads((tmp_path / "email.jsonl").read_text(encoding="utf-8"))
    assert (line["recipient"], line["body"]) == ("a@b.cd", "본문")


async def test_sms_http_failure_is_a_channel_error(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(500 if len(seen) == 1 else 200)

    monkeypatch.setattr(channels, "transport", httpx.MockTransport(handler))
    cfg = NotifyConfig.model_validate(
        {
            "channels": {
                "sms": {"adapter": "sms_http", "url": "http://gw.local/send", "sender": "02"}
            }
        }
    )
    msg = channels.OutMessage("sms", "010-1111-2222", None, "본문")
    with pytest.raises(channels.ChannelError, match="500"):
        await channels.send(cfg, msg)
    await channels.send(cfg, msg)
    assert seen[1] == {"to": "010-1111-2222", "text": "본문", "sender": "02"}


def test_sms_table_identifiers_are_strict() -> None:
    with pytest.raises(ValidationError):
        SmsTableChannel.model_validate(
            {
                "adapter": "sms_table",
                "secret_ref": "env:X",
                "table": "q; DROP TABLE x",
                "to_column": "to",
                "text_column": "t",
            }
        )


async def test_unconfigured_channel_refuses_to_send() -> None:
    with pytest.raises(channels.ChannelError, match="설정되지"):
        await channels.send(NotifyConfig(), channels.OutMessage("sms", "010", None, "x"))
