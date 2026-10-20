"""인터뷰 워크숍(TRD 12절) — 주제 상태기계·근거 확인·요소 검증·합치기·검토 규칙·프롬프트 예시."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from pydantic import BaseModel

from ontobot.db.models.workshop import DraftElement, InterviewSession
from ontobot.ontology.models import (
    LinkSource,
    LinkTypeDef,
    ObjectTypeDef,
    OntologyDocument,
    PropertyDef,
)
from ontobot.workshop import elements, evidence, extractor, interviewer, publish, review, topics

# ---------------------------------------------------------------- 주제 상태기계


def _answer(state: dict[str, Any], verdict: Any = "sufficient", kinds: set[str] | None = None):
    return topics.decide(state, verdict=verdict, present_kinds=kinds or set(), follow_up="되물음?")


def test_topics_follow_the_trd_order_and_end_with_queries() -> None:
    assert [t.key for t in topics.TOPICS] == [
        "role",
        "workflow",
        "decisions",
        "data",
        "exceptions",
        "queries",
    ]
    state = topics.initial_state()
    assert state["current"] == "role"
    assert state["topics"]["role"]["status"] == "asking"


def test_sufficient_answer_moves_to_next_topic_with_template_question() -> None:
    state, d = _answer(topics.initial_state())
    assert (d.kind, d.topic) == ("next", "workflow")
    assert d.question == topics.topic("workflow").opening
    assert state["topics"]["role"]["status"] == "done"
    assert state["topics"]["workflow"]["status"] == "asking"


def test_vague_answer_uses_interviewer_follow_up() -> None:
    state, d = _answer(topics.initial_state(), verdict="vague")
    assert (d.kind, d.question, d.topic) == ("follow_up", "되물음?", "role")
    assert state["topics"]["role"]["follow_ups"] == 1


def test_missing_required_kind_asks_template_even_if_answer_looked_sufficient() -> None:
    state, _ = _answer(topics.initial_state())  # → workflow (object_type 필요)
    state, d = _answer(state)
    assert d.kind == "follow_up"
    assert d.question == topics.topic("workflow").follow_up
    assert d.missing == {"object_type"}
    state, d = _answer(state, kinds={"object_type"})
    assert (d.kind, d.topic) == ("next", "decisions")


def test_failed_assessment_judges_by_required_kinds_only() -> None:
    _, d = _answer(topics.initial_state(), verdict=None)
    assert d.kind == "next"


def test_follow_ups_are_capped_then_topic_is_incomplete() -> None:
    state = topics.initial_state()
    for _ in range(topics.MAX_FOLLOW_UPS):
        state, d = _answer(state, verdict="vague")
        assert d.kind == "follow_up"
    state, d = _answer(state, verdict="vague")
    assert d.kind == "next"
    assert state["topics"]["role"]["status"] == "incomplete"


def test_last_topic_finishes_and_further_answers_stay_finished() -> None:
    state = topics.initial_state()
    every = {"object_type", "property", "golden_query"}
    for _ in topics.TOPICS:
        state, d = _answer(state, kinds=every)
    assert d.kind == "finish" and d.question == topics.CLOSING
    assert state["current"] is None
    assert all(v["status"] == "done" for v in state["topics"].values())
    _, again = _answer(state)
    assert again.kind == "finish"


def test_decide_does_not_mutate_input_state() -> None:
    state = topics.initial_state()
    before = json.dumps(state, sort_keys=True)
    _answer(state, verdict="vague")
    assert json.dumps(state, sort_keys=True) == before


# ---------------------------------------------------------------- 근거 확인


@pytest.mark.parametrize(
    ("quote", "utterance", "ok"),
    [
        ("시설마다 위험등급을 봅니다", "저는 시설마다 위험등급을 봅니다.", True),
        ("시설마다  위험등급을\n봅니다", "시설마다 위험등급을 봅니다", True),  # 공백 정규화
        ("시설마다 위험 등급을 봅니다", "시설마다 위험등급을 봅니다", False),  # 띄어쓰기 다름
        ("시설별 위험등급을 확인", "시설마다 위험등급을 봅니다", False),  # 바꿔 쓴 말
        ("시", "시설", False),  # 한 글자 인용은 근거가 아니다
        ("", "시설", False),
    ],
)
def test_evidence_is_exact_inclusion_after_nfc_and_whitespace(
    quote: str, utterance: str, ok: bool
) -> None:
    assert evidence.verify(quote, utterance) is ok


def test_evidence_normalizes_nfc() -> None:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "위험등급")
    assert evidence.verify(decomposed, "위험등급을 봅니다")


# ---------------------------------------------------------------- 요소 payload


def test_validate_fills_defaults_and_rejects_bad_identifiers() -> None:
    ok = elements.validate("object_type", {"api_name": "Facility", "display_name": "시설"})
    assert ok["pk_property"] == "id" and ok["title_property"] == "name"
    with pytest.raises(elements.PayloadInvalid):
        elements.validate("object_type", {"api_name": "facility", "display_name": "시설"})
    with pytest.raises(elements.PayloadInvalid):
        elements.validate("object_type", {"api_name": "Fac`ility", "display_name": "시설"})
    with pytest.raises(elements.PayloadInvalid):
        elements.validate(
            "property",
            {
                "object_type": "Facility",
                "api_name": "grade",
                "display_name": "등급",
                "data_type": "enum",
            },
        )  # enum 은 enum_values 필요
    with pytest.raises(elements.PayloadInvalid):
        elements.validate("golden_query", {"question": "x", "unknown": 1})


def test_element_key_is_exact_name() -> None:
    assert elements.key("object_type", {"api_name": "Facility"}) == "Facility"
    assert elements.key("object_type", {"api_name": "facility"}) != "Facility"
    assert (
        elements.key("property", {"object_type": "Facility", "api_name": "risk_grade"})
        == "Facility.risk_grade"
    )
    assert elements.key("golden_query", {"question": " 몇 개야?  "}) == "몇 개야?"


def test_candidates_map_to_payload_field_names() -> None:
    out = extractor.Extraction.model_validate(
        {
            "object_types": [
                {"quote": "q", "api_name": "Facility", "display_name": "시설", "description": ""}
            ],
            "questions": [{"quote": "q2", "question": "몇 개야?", "expected_answer": ""}],
        }
    )
    found = extractor.candidates(out)
    assert [c.kind for c in found] == ["object_type", "golden_query"]
    assert found[0].payload == {"api_name": "Facility", "display_name": "시설", "description": None}
    assert found[1].payload == {"question": "몇 개야?", "expected_answer": None, "answerable": True}
    for c in found:  # 추출 payload 의 키는 확정 모델이 아는 키여야 한다
        assert set(c.payload) <= set(elements.PAYLOADS[c.kind].model_fields)


# ---------------------------------------------------------------- 프롬프트 예시


def _keys_in_order(obj: dict[str, Any]) -> list[str]:
    return list(obj)


def _example_json(prompt: str) -> dict[str, Any]:
    start = prompt.index('\n{"', prompt.index("예시")) + 1
    return json.loads(re.sub(r"\n", "", prompt[start:].strip()))


@pytest.mark.parametrize(
    ("prompt", "schema", "item_schemas"),
    [
        (
            extractor.SYSTEM,
            extractor.Extraction,
            {
                "object_types": extractor.ObjectTypeCand,
                "properties": extractor.PropertyCand,
                "link_types": extractor.LinkTypeCand,
                "actions": extractor.ActionCand,
                "questions": extractor.QuestionCand,
            },
        ),
        (interviewer.SYSTEM, interviewer.Assessment, {}),
    ],
    ids=["extractor", "interviewer"],
)
def test_prompt_example_key_order_matches_schema(
    prompt: str, schema: type[BaseModel], item_schemas: dict[str, type[BaseModel]]
) -> None:
    # 구조화 출력은 스키마 순서대로 생성된다 — 예시 순서가 다르면 앞 필드를 비운다(ADR-0004).
    example = _example_json(prompt)
    assert _keys_in_order(example) == list(schema.model_fields)
    for field, item_schema in item_schemas.items():
        for item in example[field]:
            assert _keys_in_order(item) == list(item_schema.model_fields)


# ---------------------------------------------------------------- 합치기(게시)


def _base() -> OntologyDocument:
    return OntologyDocument(
        object_types=[
            ObjectTypeDef(
                api_name="Facility",
                display_name="시설",
                pk_property="facility_id",
                title_property="name",
                properties=[
                    PropertyDef(
                        api_name="facility_id",
                        display_name="코드",
                        data_type="string",
                        nullable=False,
                    ),
                    PropertyDef(api_name="name", display_name="이름", data_type="string"),
                    PropertyDef(api_name="risk_grade", display_name="위험등급", data_type="int"),
                ],
            ),
        ],
        link_types=[],
    )


def _prop(type_: str, name: str, dt: str = "string") -> dict[str, Any]:
    return {"object_type": type_, "api_name": name, "display_name": name, "data_type": dt}


def test_merge_adds_new_type_property_and_link_on_top_of_base() -> None:
    m = publish.merge(
        _base(),
        [
            (1, "object_type", {"api_name": "Inspection", "display_name": "점검"}),
            (2, "property", _prop("Inspection", "inspected_on", "date")),
            (3, "property", _prop("Facility", "last_checked", "date")),
            (
                4,
                "link_type",
                {
                    "api_name": "inspects",
                    "display_name": "점검 대상",
                    "from_type": "Inspection",
                    "to_type": "Facility",
                },
            ),
            (5, "action_candidate", {"name": "점검 요청"}),
        ],
    )
    assert m.issues == []
    assert m.added == {
        "object_type": ["Inspection"],
        "property": ["Inspection.inspected_on", "Facility.last_checked"],
        "link_type": ["inspects"],
    }
    insp = m.document.object_type("Inspection")
    assert insp is not None and insp.pk_property == "id"
    assert m.document.link_types[0].source == LinkSource(
        kind="join_mapping", config={"origin": "workshop"}
    )
    assert _base().object_type("Facility").property("last_checked") is None  # type: ignore[union-attr]


def test_merge_reuses_exact_same_names_and_blocks_conflicts() -> None:
    m = publish.merge(
        _base(),
        [
            (1, "object_type", {"api_name": "Facility", "display_name": "설비"}),
            (2, "property", _prop("Facility", "risk_grade", "int")),
            (3, "property", _prop("Facility", "name", "int")),
            (4, "property", _prop("Nowhere", "x")),
            (5, "object_type", {"api_name": "bad name", "display_name": "x"}),
        ],
    )
    assert m.reused == ["Facility", "Facility.risk_grade"]
    paths = sorted(i.path for i in m.issues)
    assert paths == ["draft_element.3", "draft_element.4", "draft_element.5"]
    # 같은 이름이라도 표시 이름을 덮어쓰지 않는다
    assert m.document.object_type("Facility").display_name == "시설"  # type: ignore[union-attr]


def test_merge_link_with_fk_property_and_conflicting_link() -> None:
    base = _base()
    base.object_types.append(
        ObjectTypeDef(api_name="Org", display_name="조직", pk_property="id", title_property="id")
    )
    base.link_types.append(
        LinkTypeDef(
            api_name="owned_by",
            display_name="소유",
            from_type="Facility",
            to_type="Org",
            cardinality="one",
            source=LinkSource(kind="fk_property", property="name"),
        )
    )
    link = {"api_name": "owned_by", "display_name": "소유", "from_type": "Org", "to_type": "Org"}
    m = publish.merge(base, [(1, "link_type", link)])
    assert [i.path for i in m.issues] == ["draft_element.1"]
    fk = {**link, "api_name": "managed_by", "from_type": "Facility", "fk_property": "name"}
    m = publish.merge(base, [(2, "link_type", fk)])
    assert m.issues == []
    assert m.document.link_types[-1].source == LinkSource(kind="fk_property", property="name")


# ---------------------------------------------------------------- 검토 규칙


class _Db:
    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)


def _el(kind: str, payload: dict[str, Any], *, ok: bool) -> DraftElement:
    return DraftElement(id=7, session_id=1, kind=kind, payload=payload, evidence=[], evidence_ok=ok)


def _session(status: str = "open") -> InterviewSession:
    return InterviewSession(id=1, interviewee_id=1, status=status, topic_state={})


def _review(el: DraftElement, decision: Any, payload: Any = None, status: str = "open") -> None:
    review.review(
        _Db(),  # type: ignore[arg-type]
        _session(status),
        el,
        decision=decision,
        payload=payload,
        actor_id=9,
    )


OBJ = {"api_name": "Facility", "display_name": "시설"}


def test_unverified_evidence_cannot_be_confirmed_without_edit() -> None:
    el = _el("object_type", OBJ, ok=False)
    with pytest.raises(review.EvidenceRequired):
        _review(el, "confirmed")
    assert el.status is None or el.status == "proposed"
    _review(el, "confirmed", {**OBJ, "description": "사람이 고쳤다"})
    assert el.status == "edited" and el.reviewed_by == 9


def test_confirm_validates_payload_strictly() -> None:
    el = _el("object_type", {"api_name": "facility", "display_name": "시설"}, ok=True)
    with pytest.raises(elements.PayloadInvalid):
        _review(el, "confirmed")
    _review(el, "rejected")
    assert el.status == "rejected"  # 거절은 형식과 무관하다


def test_golden_query_needs_expected_answer_unless_unanswerable() -> None:
    q = {"question": "위험등급 4 이상 시설 몇 개야?", "expected_answer": None, "answerable": True}
    el = _el("golden_query", q, ok=True)
    with pytest.raises(review.ExpectedAnswerRequired):
        _review(el, "confirmed")
    _review(el, "confirmed", {**q, "expected_answer": "7개"})
    assert el.status == "edited"
    el2 = _el("golden_query", {**q, "answerable": False}, ok=True)
    _review(el2, "confirmed")
    assert el2.status == "confirmed"


def test_published_session_elements_are_locked() -> None:
    with pytest.raises(review.ElementLocked):
        _review(_el("object_type", OBJ, ok=True), "rejected", status="published")


# ---------------------------------------------------------------- 골든 질의 거르기


def _gq(question: str) -> extractor.Candidate:
    return extractor.Candidate("golden_query", {"question": question}, "q")


def test_golden_queries_only_from_queries_topic_and_never_the_agent_question() -> None:
    queries, data = topics.topic("queries"), topics.topic("data")
    asked = "무엇을 보고 판단하거나 결정하시나요? 판단한 뒤에 하는 조치도 알려 주세요."
    assert extractor.keep(_gq("위험등급 3 이상 시설 몇 개야?"), queries, queries.opening)
    assert not extractor.keep(_gq("위험등급 3 이상 시설 몇 개야?"), data, data.opening)
    # 면담자 질문을 그대로·잘라 베낀 후보(Phase 6 실측)
    assert not extractor.keep(_gq("무엇을 보고 판단하거나 결정하시나요?"), queries, asked)
    assert not extractor.keep(_gq(""), queries, queries.opening)
    obj = extractor.Candidate("object_type", {"api_name": "Facility"}, "q")
    assert extractor.keep(obj, data, data.opening)
