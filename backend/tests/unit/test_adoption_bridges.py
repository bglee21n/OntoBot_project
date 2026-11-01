"""Phase 21 양식 → 제품 보내기 — 미리보기 변환(순수). 같은 입력을 두 번 보내면 적용할 것이 없다."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from ontobot.adoption import bridges
from ontobot.adoption.bridges import UserState
from ontobot.ontology.models import OntologyDocument

DOC = OntologyDocument.model_validate(
    {
        "object_types": [
            {
                "api_name": "Facility",
                "display_name": "시설",
                "pk_property": "facility_id",
                "title_property": "name",
                "properties": [
                    {"api_name": "facility_id", "display_name": "시설 번호", "data_type": "string"},
                    {"api_name": "name", "display_name": "이름", "data_type": "string"},
                    {"api_name": "risk_grade", "display_name": "위험 등급", "data_type": "int"},
                    {
                        "api_name": "facility_type",
                        "display_name": "유형",
                        "data_type": "enum",
                        "enum_values": ["변전소", "철탑"],
                    },
                ],
            },
            {
                "api_name": "Organization",
                "display_name": "조직",
                "pk_property": "org_id",
                "title_property": "name",
                "properties": [
                    {"api_name": "org_id", "display_name": "조직 번호", "data_type": "string"},
                    {"api_name": "name", "display_name": "이름", "data_type": "string"},
                ],
            },
        ]
    }
)


def _terms(*rows: dict[str, str]) -> dict[str, dict[str, object]]:
    return {"glossary": {"terms": list(rows)}}


def _apply_to_doc(doc: OntologyDocument, changes: list[bridges.Change]) -> OntologyDocument:
    """테스트용 적용 — 바뀐 정의로 문서를 다시 만든다(실제 적용은 adoption.send)."""
    data = doc.model_dump()
    for t in data["object_types"]:
        t.update(bridges.element_patch(changes, t["api_name"]))
        for p in t["properties"]:
            p.update(bridges.element_patch(changes, f"{t['api_name']}.{p['api_name']}"))
    return OntologyDocument.model_validate(data)


def test_glossary_matches_by_exact_api_name_only() -> None:
    changes = bridges.glossary_to_draft(
        _terms(
            {
                "api_name": "risk_grade",
                "display_name": "위험등급",
                "description": "1~5, 5가 가장 위험",
            },
            {"api_name": "Facility", "description": "점검 대상 설비"},
            {"api_name": "Facility.facility_type", "enum_values": "변전소·철탑·전주"},
            {"api_name": "name", "display_name": "명칭"},  # 두 타입에 있다
            {"api_name": "riskgrade", "display_name": "x"},  # 비슷해도 맞추지 않는다
            {"api_name": "위험등급"},
        ),
        DOC,
    )
    ok = [(c.target, c.field, c.after) for c in bridges.applicable(changes)]
    assert ok == [
        ("Facility.risk_grade", "display_name", "위험등급"),
        ("Facility.risk_grade", "description", "1~5, 5가 가장 위험"),
        ("Facility", "description", "점검 대상 설비"),
        ("Facility.facility_type", "enum_values", "변전소, 철탑, 전주"),
    ]
    blocked = {c.target: c.blocked for c in changes if c.blocked}
    assert "Facility, Organization" in blocked["name"]
    assert "설계도에 없는" in blocked["riskgrade"]
    assert "규칙" in blocked["위험등급"]


def test_glossary_never_removes_enum_values_or_changes_types() -> None:
    changes = bridges.glossary_to_draft(
        _terms(
            {"api_name": "facility_type", "enum_values": "변전소"},
            {"api_name": "risk_grade", "enum_values": "1, 2, 3"},
        ),
        DOC,
    )
    assert bridges.applicable(changes) == []
    assert "철탑" in (changes[0].blocked or "")
    assert "int" in (changes[1].blocked or "")


def test_glossary_twice_has_nothing_to_apply() -> None:
    payload = _terms(
        {"api_name": "risk_grade", "display_name": "위험등급"},
        {"api_name": "facility_type", "enum_values": "변전소, 철탑, 전주"},
    )
    first = bridges.glossary_to_draft(payload, DOC)
    after = _apply_to_doc(DOC, first)
    assert bridges.applicable(bridges.glossary_to_draft(payload, after)) == []
    assert after.object_type("Facility").property("facility_type").enum_values == [  # type: ignore[union-attr]
        "변전소",
        "철탑",
        "전주",
    ]


def test_glossary_blocks_definition_the_document_rejects() -> None:
    changes = bridges.glossary_to_draft(
        _terms({"api_name": "risk_grade", "display_name": "가" * 200}), DOC
    )
    assert bridges.applicable(changes) == []
    assert changes[0].blocked


def test_questions_dedupe_by_normalized_exact_match() -> None:
    payloads = {
        "observation": {"log": [{"question": "고위험  시설은 몇 곳?"}, {"seen": "질문 없음"}]},
        "concepts": {"rows": [{"golden": "고위험 시설은 몇 곳?"}, {"golden": "강남 철탑 목록"}]},
        "tacit": {"followups": [{"question": "변전소 평균 위험등급은?"}]},
    }
    changes = bridges.questions_to_golden(payloads, existing=["변전소 평균 위험등급은?"])
    assert [c.after for c in bridges.applicable(changes)] == [
        "고위험 시설은 몇 곳?",
        "강남 철탑 목록",
    ]
    assert [c.source for c in changes] == [
        "36.7 관찰 기록 1행",
        "36.10 개념 추출표 1행",
        "36.10 개념 추출표 2행",
        "36.9 더 확인할 것 1행",
    ]
    again = bridges.questions_to_golden(
        payloads, existing=["고위험 시설은 몇 곳?", "강남 철탑 목록", "변전소 평균 위험등급은?"]
    )
    assert bridges.applicable(again) == []


def test_parse_target_units() -> None:
    assert bridges.parse_target("90%", "ratio") == Decimal("0.9")
    assert bridges.parse_target("0.95", "ratio") == Decimal("0.95")
    assert bridges.parse_target("25초", "seconds") == Decimal(25)
    assert isinstance(bridges.parse_target("95", "ratio"), str)
    assert isinstance(bridges.parse_target("10%", "seconds"), str)
    assert isinstance(bridges.parse_target("빠르게", "seconds"), str)


def test_kpis_only_known_system_rows() -> None:
    payloads = {
        "pilot": {
            "metrics": [
                {"target": "92%"},  # 답 정답률
                {"target": "80%"},  # 근거 등급 ① — 목표 지표 없음
                {"target": "25초"},  # 응답 시간(지금과 같다)
                {"target": "아주 높게"},  # Action 성공률 — 읽지 못함
                {},
                {"target": "50%"},  # 활용도 — 업무 지표
            ]
        }
    }
    current = {"accuracy": Decimal("0.9"), "p95_seconds": Decimal(25), "action_success_rate": None}
    changes = bridges.kpis_to_targets(payloads, current)
    assert [(c.target, c.before, c.after) for c in bridges.applicable(changes)] == [
        ("accuracy", "0.9", "0.92")
    ]
    assert {c.target: c.blocked for c in changes if c.blocked is not None} == {
        "row:2": bridges.WHY["no_target"],
        "p95_seconds": bridges.SAME,
        "action_success_rate": "숫자로 읽을 수 없습니다(예: 90%, 0.9, 25초)",
        "row:6": bridges.WHY["no_target"],
    }


ROLES = frozenset({"user", "modeler", "bot_builder", "domain_expert", "admin"})


def test_accounts_create_only_missing_and_add_roles_never_remove() -> None:
    payloads = {
        "stakeholders": {
            "people": [
                {
                    "name": "홍길동 과장",
                    "username": "hong",
                    "role": "user",
                    "group": "inspection_team",
                },
                {"username": "kim", "role": "modeler", "group": "점검팀"},  # 표시 이름으로 맞춤
                {"username": "lee", "role": "user, approver"},
                {"username": "svc", "role": "user"},
                {"username": "park", "group": "검사팀"},
                {"username": "choi"},
                {"name": "아이디 없음", "role": "user"},
            ]
        }
    }
    users = {
        "kim": UserState(frozenset({"user", "admin"}), frozenset()),
        "park": UserState(frozenset({"user"}), frozenset()),
    }
    changes = bridges.stakeholders_to_accounts(
        payloads,
        users=users,
        groups={"inspect": "점검팀"},
        known_roles=ROLES,
        service_usernames=frozenset({"svc"}),
    )
    ok = [(c.op, c.target, c.after) for c in bridges.applicable(changes)]
    assert ok == [
        ("create", "group:inspection_team", "inspection_team"),
        ("create", "user:hong", "홍길동 과장"),
        ("add_role", "user:hong", "user"),
        ("add_member", "group:inspection_team", "hong"),
        ("add_role", "user:kim", "modeler"),
        ("add_member", "group:inspect", "kim"),
    ]
    blocked = {c.target: c.blocked for c in changes if c.blocked}
    assert "approver" in blocked["user:lee"]
    assert blocked["user:svc"] == bridges.WHY["service"]
    assert blocked["group:검사팀"] == bridges.WHY["bad_group"]
    assert blocked["user:choi"] == bridges.WHY["no_role"]


def test_accounts_twice_has_nothing_to_apply() -> None:
    payloads = {
        "stakeholders": {
            "people": [{"username": "hong", "role": "user, modeler", "group": "team_a"}]
        }
    }
    after = {"hong": UserState(frozenset({"user", "modeler"}), frozenset({"team_a"}))}
    changes = bridges.stakeholders_to_accounts(
        payloads, users=after, groups={"team_a": "A"}, known_roles=ROLES
    )
    assert bridges.applicable(changes) == []


def test_fingerprint_changes_with_content() -> None:
    a = bridges.questions_to_golden({"concepts": {"rows": [{"golden": "질문"}]}}, existing=[])
    assert bridges.fingerprint(a) == bridges.fingerprint(list(a))
    assert bridges.fingerprint(a) != bridges.fingerprint([replace(a[0], after="다른 질문")])
