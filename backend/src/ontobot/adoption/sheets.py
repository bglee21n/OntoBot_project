"""도입 현장 양식 선언 — 사용자 매뉴얼 36장 양식 16종의 정본(TRD 17.17, 결정 A2). 순수 모듈.

양식 = 묶음(``Section``)들. 묶음은 칸 묶음(``fields``) 또는 표(``table``)다. 표의 열은 매뉴얼이 적어 둔
값을 그대로 보이는 고정 열과 사람이 채우는 입력 열로 나뉜다.

- 행이 정해진 표(``rows``)는 매뉴얼의 행을 그대로 갖는다. 입력 열 칸에 매뉴얼이 적어 둔 글은 자리 표시다.
  payload 는 행 순서대로의 목록이다.
- 행이 정해지지 않은 표는 사람이 행을 더한다(``MAX_ROWS``). ``blank_rows`` 는 인쇄할 빈 행 수(매뉴얼과 같다).

화면은 이 선언을 그리는 범용 편집기 하나를 쓰고, 서버는 같은 선언에서 만든 Pydantic 모델로 검증한다.
표 머리글·고정 행이 매뉴얼 36장과 같은지는 ``tests/unit/test_adoption.py`` 가 맞춘다 — 칸을 바꾸면
매뉴얼도 함께 고친다. 양식 값은 LLM 에 보내지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, create_model

ColumnKind = Literal["text", "long", "choice", "check"]

MAX_ROWS = 100
TEXT_MAX = 500
LONG_MAX = 4000

YESNO = ("예", "아니오")
LMH = ("낮음", "보통", "높음")
HML = ("상", "중", "하")
SCORE = ("1", "2", "3", "4", "5")
TFU = ("맞음", "틀림", "모름")
HAVE = ("있음", "없음")

STAGES: dict[int, str] = {
    1: "착수 준비",
    2: "문제 정의",
    3: "현장 이해",
    4: "설계",
    5: "파일럿과 자산화",
}


@dataclass(frozen=True)
class Column:
    key: str  # 고정 열은 비어 있다(값이 payload 에 없다)
    label: str
    kind: ColumnKind = "text"
    options: tuple[str, ...] = ()
    pii: bool = False  # 사람 정보(결정 A4) — 감사·로그에 값을 내지 않는다

    @property
    def fixed(self) -> bool:
        return not self.key


@dataclass(frozen=True)
class Section:
    key: str
    title: str | None
    layout: Literal["fields", "table"]
    columns: tuple[Column, ...]
    rows: tuple[tuple[str, ...], ...] = ()  # 표: 행마다 열 수만큼(입력 열은 자리 표시)
    blank_rows: int = 0
    note: str | None = None

    @property
    def inputs(self) -> tuple[Column, ...]:
        return tuple(c for c in self.columns if not c.fixed)


@dataclass(frozen=True)
class Sheet:
    kind: str
    number: str  # 매뉴얼 절 번호 "36.1"
    title: str
    stage: int  # 1~5, 0 은 한눈에 보는 순서표(36.16)
    intro: str
    sections: tuple[Section, ...]

    def section(self, key: str) -> Section | None:
        return next((s for s in self.sections if s.key == key), None)


# ---------------------------------------------------------------- 선언을 짧게 적는 도우미


def fx(label: str) -> Column:
    return Column(key="", label=label)


def col(
    key: str,
    label: str,
    kind: ColumnKind = "text",
    options: tuple[str, ...] = (),
    *,
    pii: bool = False,
) -> Column:
    return Column(key=key, label=label, kind=kind, options=options, pii=pii)


def fields(key: str, title: str | None, *columns: Column, note: str | None = None) -> Section:
    return Section(key=key, title=title, layout="fields", columns=columns, note=note)


def fixed_table(
    key: str,
    title: str | None,
    columns: tuple[Column, ...],
    rows: list[tuple[str, ...]],
    *,
    note: str | None = None,
) -> Section:
    return Section(
        key=key, title=title, layout="table", columns=columns, rows=tuple(rows), note=note
    )


def free_table(
    key: str, title: str | None, *columns: Column, blank: int = 1, note: str | None = None
) -> Section:
    return Section(
        key=key, title=title, layout="table", columns=columns, blank_rows=blank, note=note
    )


def _rows(*values: str, width: int) -> list[tuple[str, ...]]:
    """고정 열 하나 + 빈 입력 열(width-1 개)인 행들."""
    return [(v, *([""] * (width - 1))) for v in values]


# ---------------------------------------------------------------- 1단계 착수 준비

PRECHECK = Sheet(
    kind="precheck",
    number="36.1",
    title="도입 사전 점검표",
    stage=1,
    intro='현장에 들어가기 전에 확인합니다. "아니오"가 있으면 착수 날짜보다 그 항목을 먼저 해결하세요.',
    sections=(
        fixed_table(
            "checks",
            None,
            (
                fx("묶음"),
                fx("확인"),
                col("answer", "예/아니오", "choice", YESNO),
                col("memo", "메모"),
            ),
            [
                (g, q, "", "")
                for g, q in (
                    ("목표·범위", "이번에 다룰 업무가 하나로 정해졌는가"),
                    ("목표·범위", "하지 않을 일(범위 밖)이 적혀 있는가"),
                    ("목표·범위", "성공을 무엇으로 잴지 고객과 합의했는가(36.14)"),
                    ("데이터", "원천 DB 의 종류·위치와 읽기 전용 계정을 받을 수 있는가"),
                    ("데이터", "대상의 고유 번호(pk)가 될 컬럼이 있는가"),
                    ("데이터", "코드값(예: A1, 03)의 뜻을 아는 사람이 있는가"),
                    ("사람", "인터뷰할 현업 전문가 1~3명과 일정이 잡혔는가"),
                    ("사람", "데이터 변경(Action)을 승인할 사람이 정해졌는가"),
                    ("일정·역할", "도입 담당자·모델러·관리자가 누구인지 정했는가"),
                    ("일정·역할", "주간 보고 주기와 받는 사람이 정해졌는가"),
                    ("보안", "개인정보(이름·연락처 등)가 든 컬럼과 볼 수 있는 사람을 정했는가"),
                    (
                        "보안",
                        "서버가 폐쇄망인지, 외부 LLM 을 쓸 수 있는지 확인했는가(운영자 매뉴얼 4장)",
                    ),
                )
            ],
        ),
        fixed_table(
            "risks",
            "도입에서 자주 나는 위험",
            (
                fx("위험"),
                fx("생기면"),
                col("likelihood", "가능성(낮음/보통/높음)", "choice", LMH),
                fx("미리 할 일"),
            ),
            [
                (
                    "원천 DB 에 접근하지 못함",
                    "데이터 연결(18장)이 멈춤",
                    "",
                    "읽기 전용 계정·방화벽 요청을 착수 전에",
                ),
                (
                    "코드값의 뜻을 모름",
                    "목록 값을 정의하지 못해 질문이 틀림",
                    "",
                    "코드표 문서를 먼저 받기",
                ),
                (
                    "승인자가 없음",
                    "Action 요청이 승인 대기에 쌓임",
                    "",
                    "승인자와 대리 승인자 지정",
                ),
                (
                    "개인정보 범위가 정해지지 않음",
                    "게시 뒤 속성을 다시 고쳐야 함",
                    "",
                    "36.6 에서 데이터 그룹 초안과 함께 정하기",
                ),
                (
                    "범위가 계속 늘어남",
                    "일정이 밀리고 평가 기준이 흔들림",
                    "",
                    "범위 밖 목록을 서명받기",
                ),
            ],
        ),
        fixed_table(
            "approval",
            "착수 승인",
            (
                fx(""),
                col("customer", "고객 책임자", pii=True),
                col("lead", "도입 담당자", pii=True),
            ),
            _rows("이름", "서명", "날짜", width=3),
        ),
    ),
)

FIT = Sheet(
    kind="fit",
    number="36.2",
    title="AI 적용 가능성 진단",
    stage=1,
    intro=(
        "대상 업무가 OntoBot 에 맞는지 봅니다. OntoBot 이 잘하는 일은 세 가지입니다. 정리된 데이터에 대한 질문에 근거를 "
        "붙여 답하기, 등록한 문서에서 찾아 답하기, 사람이 승인하는 데이터 변경입니다."
    ),
    sections=(
        fields(
            "overview",
            None,
            col("task", "업무명"),
            col("department", "부서"),
            col("users", "쓰는 사람"),
            col("pain", "지금 불편한 점", "long"),
            col("expected", "기대 효과", "long"),
        ),
        fixed_table(
            "criteria",
            None,
            (
                fx("기준"),
                col("score", "점수(1~5)", "choice", SCORE),
                col("evidence", "근거(사실·숫자)"),
            ),
            _rows(
                "같은 종류의 질문이 자주 나오는가",
                "답에 필요한 데이터가 DB 에 있고 읽을 수 있는가",
                "판단 기준이 숫자·목록 값으로 정해져 있는가",
                "답이 빨리 필요한가(지금은 얼마나 걸리나)",
                "예외 처리가 많지 않은가(많으면 점수 낮게)",
                "보안·규제 제약이 감당할 만한가",
                width=3,
            ),
        ),
        fixed_table(
            "forms",
            "쓸 형태",
            (fx("형태"), fx("OntoBot 기능"), col("picked", "고름", "check")),
            [
                ("데이터 질문과 답", "채팅(4~8장)", ""),
                ("문서에서 찾아 답하기", "문서 넣기(18.8)·문서로 답한 경우(5장)", ""),
                ("정해 둔 계산(비율·점수)", "Function(16.13)", ""),
                ("데이터 변경 요청과 승인", "Action(9~13장)", ""),
            ],
            note=(
                "예측, 사람 승인 없이 저절로 실행되는 일, 데이터에 없는 판단은 OntoBot 이 하지 않습니다. 이런 요구가 "
                "중심이면 범위 밖으로 적고 다른 방법을 찾으세요."
            ),
        ),
        fields(
            "decision",
            "판단",
            col("decision", "판단", "choice", ("바로 진행", "조건을 채운 뒤 진행", "보류")),
            col("condition", "조건"),
            col("reason", "이유"),
        ),
    ),
)

SYSTEMS = Sheet(
    kind="systems",
    number="36.3",
    title="기존 시스템 진단",
    stage=1,
    intro="고객이 지금 쓰는 시스템을 봅니다. 데이터를 어디서 가져올지, OntoBot 이 무엇을 대신할지 정하는 데 씁니다.",
    sections=(
        fixed_table(
            "areas",
            None,
            (
                fx("영역"),
                col("now", "지금 모습"),
                col("problem", "문제점"),
                col("impact", "영향(낮음/보통/높음)", "choice", LMH),
                fx("OntoBot 이 대신할 수 있나"),
            ),
            [
                (a, "", "", "", b)
                for a, b in (
                    ("원천 DB(테이블·pk·코드값)", "데이터 연결·매핑(18장)"),
                    ("조회 화면·보고서", "채팅 질문"),
                    ("데이터 입력·수정", "Action"),
                    ("결재·승인 경로", "Action 승인"),
                    ("알림(메일·문자)", "Action 알림(운영자 매뉴얼 15장)"),
                    ("권한(누가 무엇을 보나)", "데이터 그룹(21.3)"),
                    ("업무 문서(규정·지침)", "문서 넣기(18.8)"),
                )
            ],
        ),
        fields(
            "summary",
            None,
            col("pains", "사용자가 가장 불편해하는 점 세 가지", "long"),
            col("first", "먼저 바꿀 영역 세 가지", "long"),
        ),
    ),
)

# ---------------------------------------------------------------- 2단계 문제 정의

KICKOFF = Sheet(
    kind="kickoff",
    number="36.4",
    title="첫 미팅 질문지",
    stage=2,
    intro=(
        "첫 미팅에서는 해결책을 제안하기보다 문제를 고객의 말로 다시 정리합니다. 워크숍 주제 열은 그 답이 인터뷰 "
        "워크숍의 어느 주제(14.3) 재료가 되는지입니다."
    ),
    sections=(
        fixed_table(
            "questions",
            None,
            (fx("묶음"), fx("질문"), fx("워크숍 주제"), col("memo", "메모", "long")),
            [
                (g, q, t, "")
                for g, q, t in (
                    ("문제", "오늘 가장 먼저 풀고 싶은 일은 무엇입니까", "역할"),
                    ("문제", "그 일이 언제, 어떤 계기로 중요해졌습니까", "역할"),
                    ("문제", "지금까지 해 본 방법과 효과가 없던 이유는 무엇입니까", "예외 상황"),
                    (
                        "업무 흐름",
                        "그 일은 어떤 순서로 진행됩니까. 누가 무엇을 넘겨받습니까",
                        "업무 흐름",
                    ),
                    ("업무 흐름", "시간이 가장 많이 드는 단계는 어디입니까", "업무 흐름"),
                    ("데이터·시스템", "판단할 때 어떤 화면·보고서를 봅니까", "사용 데이터"),
                    (
                        "데이터·시스템",
                        "그 값은 어느 시스템에 있고, 얼마나 정확합니까",
                        "사용 데이터",
                    ),
                    ("의사결정", "무엇을 보고 결정합니까. 기준 숫자가 있습니까", "의사결정"),
                    ("의사결정", "결정 권한은 누구에게 있고, 누가 승인합니까", "의사결정"),
                    ("이해관계자", "이 일로 가장 영향을 받는 사람은 누구입니까", "—"),
                    ("성과", "지금은 이 일을 무엇으로 잽니까. 목표는 얼마입니까", "—"),
                    ("성과", "잘 되면 무엇이 달라져 있어야 합니까", "찾아보는 질문"),
                )
            ],
        ),
        fixed_table(
            "assumptions",
            "확인할 가정",
            (fx("가정"), col("answer", "맞음/틀림/모름", "choice", TFU)),
            _rows(
                "우리가 들은 문제가 고객이 생각하는 문제와 같다",
                "필요한 데이터에 실제로 접근할 수 있다",
                "성공 지표를 잴 수 있다",
                "결정권자가 이 일을 지지한다",
                width=2,
            ),
        ),
        free_table(
            "todos",
            "다음 할 일",
            col("task", "할 일"),
            col("owner", "담당"),
            col("due", "기한"),
            col("next_meeting", "다음 미팅"),
        ),
    ),
)

REQUEST = Sheet(
    kind="request",
    number="36.5",
    title="요청 정리 — 말한 것과 풀 문제",
    stage=2,
    intro="고객이 말한 요구를 그대로 만들지 말고, 그 뒤에 있는 문제를 찾습니다.",
    sections=(
        fields("said", "고객이 말한 그대로", col("verbatim", "받아 적기", "long")),
        free_table(
            "analysis",
            None,
            col("surface", "표면 요구(말한 것)"),
            col("observed", "관찰한 현상"),
            col("hypothesis", "원인 가설"),
            col("problem", "진짜 문제 후보"),
            col("data", "확인할 데이터"),
            blank=3,
        ),
        fixed_table(
            "whys",
            "왜를 다섯 번",
            (fx("왜?"), col("answer", "답")),
            _rows("1", "2", "3", "4", "5", "근본 원인", width=2),
        ),
        fields(
            "problem",
            "풀 문제 한 문장",
            col("statement", "우리가 풀 문제는 … 이다", "long"),
            note=(
                '문장의 주어를 사람이나 부서가 아닌 업무 구조로 쓰세요. 예: "점검 결과가 시설 대장과 따로 관리되어 '
                '고위험 시설을 찾는 데 하루가 걸린다."'
            ),
        ),
        free_table(
            "out_of_scope", None, col("item", "범위 밖(이번에 하지 않을 것)"), col("reason", "이유")
        ),
    ),
)

STAKEHOLDERS = Sheet(
    kind="stakeholders",
    number="36.6",
    title="이해관계자 지도",
    stage=2,
    intro=(
        "사람마다 OntoBot 에서 어떤 역할과 데이터 그룹을 가질지 정합니다. 이 표가 그대로 계정·그룹 설계의 초안이 "
        "됩니다(21장, 29장). 영향력·관심도는 상/중/하로 적습니다."
    ),
    sections=(
        fixed_table(
            "roles",
            None,
            (fx("분류"), fx("하는 일"), fx("대개 맡는 OntoBot 역할")),
            [
                ("결정권자", "예산·우선순위·승인", "승인자, 필요하면 admin"),
                ("현업 사용자", "일상 업무, 질문하는 사람", "user"),
                ("현업 전문가", "업무 규칙을 아는 사람", "domain_expert(인터뷰)"),
                ("데이터·IT 담당", "원천 DB 관리", "원천 계정 제공, modeler 협업"),
                ("설계 담당", "설계도·Action 정의", "modeler"),
                ("품질 담당", "골든셋·평가", "bot_builder"),
                ("운영자", "설치·백업·업그레이드", "admin(운영자 매뉴얼)"),
            ],
        ),
        free_table(
            "people",
            None,
            col("name", "이름·직책", pii=True),
            col("concern", "관심사·걱정"),
            col("influence", "영향력", "choice", HML),
            col("interest", "관심도", "choice", HML),
            col("role", "OntoBot 역할"),
            col("group", "데이터 그룹"),
            col("types", "읽기 / 고치기 타입"),
            blank=3,
            note=(
                "둘 다 높음: 함께 결정합니다(주간 회의). 영향력만 높음: 결과를 정기적으로 알립니다. 관심만 높음: 자주 "
                "묻고 듣습니다(인터뷰 대상 후보). 둘 다 낮음: 변화가 생길 때만 알립니다."
            ),
        ),
        free_table(
            "comms",
            None,
            col("plan", "소통 계획"),
            col("audience", "대상"),
            col("cadence", "주기"),
            col("method", "방법"),
            col("message", "전할 내용"),
            col("owner", "담당"),
        ),
        fields(
            "support",
            None,
            col("supporters", "적극 지지자", "long", pii=True),
            col("opponents", "반대할 수 있는 사람과 우려", "long", pii=True),
        ),
    ),
)

# ---------------------------------------------------------------- 3단계 현장 이해

OBSERVATION = Sheet(
    kind="observation",
    number="36.7",
    title="현장 관찰 기록지",
    stage=3,
    intro=(
        '일하는 모습을 직접 보고 사실만 적습니다. 해석은 "가설" 열에 따로 씁니다. 마지막 열은 그 장면에서 사람이 '
        "실제로 찾던 정보를 질문 형태로 적는 곳입니다. 여기 적은 질문이 골든 질문 후보가 됩니다(20장)."
    ),
    sections=(
        fields(
            "meta",
            None,
            col("when", "날짜·시간"),
            col("place", "장소"),
            col("observer", "관찰자", pii=True),
            col("task", "대상 업무"),
        ),
        free_table(
            "log",
            None,
            col("time", "시각"),
            col("seen", "본 것"),
            col("who", "누가(역할)"),
            col("tools", "쓰는 화면·문서"),
            col("symptom", "문제 징후"),
            col("hypothesis", "가설"),
            col("question", "답할 수 있는 질문 후보"),
            blank=3,
        ),
        fixed_table(
            "patterns",
            None,
            (fx("찾은 패턴"), col("content", "내용", "long")),
            _rows(
                "되풀이되는 일",
                "막히는 지점",
                "정보가 끊기는 곳(다른 시스템·종이로 옮겨 적기)",
                width=2,
            ),
        ),
        free_table(
            "findings",
            "오늘의 발견 세 가지",
            col("finding", "발견"),
            col("impact", "영향(낮음/보통/높음)", "choice", LMH),
            col("evidence", "근거(관찰 몇 번째 줄)"),
            blank=3,
        ),
    ),
)

ASIS = Sheet(
    kind="asis",
    number="36.8",
    title="AS-IS 업무 흐름표",
    stage=3,
    intro=(
        "지금 업무를 단계별로 적습니다. 마지막 열에서 그 단계를 설계도의 말로 읽어 봅니다. 다루는 대상은 객체 타입, "
        "보는 항목은 속성, 넘겨받는 관계는 링크, 값을 바꾸는 일은 Action 후보가 됩니다(16·17장)."
    ),
    sections=(
        fields(
            "meta",
            None,
            col("task", "업무명"),
            col("scope", "범위(시작~끝)"),
            col("author", "작성자", pii=True),
            col("date", "날짜"),
        ),
        free_table(
            "steps",
            None,
            col("step", "단계"),
            col("input", "입력"),
            col("work", "하는 일"),
            col("role", "담당(역할)"),
            col("system", "쓰는 시스템"),
            col("output", "결과물"),
            col("duration", "걸린 시간"),
            col("problem", "문제점"),
            col("candidate", "설계도 후보"),
            blank=3,
            note=(
                "예시(샘플 도메인) 단계 2: 입력은 점검 결과표, 하는 일은 위험등급 다시 매기기, 담당은 점검팀, 쓰는 "
                "시스템은 시설 대장 엑셀, 걸린 시간은 2시간, 문제점은 같은 값을 두 번 입력하는 것. 설계도 후보는 객체 "
                '타입 Facility, 속성 risk_grade(정수), Action "위험등급 변경".'
            ),
        ),
        fixed_table(
            "manual",
            None,
            (
                fx("수작업 점검"),
                col("present", "있음/없음", "choice", HAVE),
                fx("Action 후보로 볼까"),
            ),
            [
                ("같은 값을 여러 시스템에 입력", "", "예"),
                ("종이·메일로 결재", "", "예(승인 있는 Action)"),
                ("손으로 집계해 보고서 작성", "", "Function·질문"),
                ("여러 화면을 옮겨 다니며 확인", "", "질문(링크 탐색)"),
            ],
        ),
        fields(
            "notes",
            None,
            col("blocked", "막히는 구간", "long"),
            col("exceptions", "예외 처리", "long"),
        ),
    ),
)

TACIT = Sheet(
    kind="tacit",
    number="36.9",
    title="암묵지 인터뷰 기록지",
    stage=3,
    intro="현업 전문가의 머릿속 판단 기준을 꺼내는 인터뷰를 적습니다. 질문은 워크숍 주제 순서로 놓았습니다.",
    sections=(
        fields(
            "meta",
            None,
            col("name", "이름", pii=True),
            col("role", "역할"),
            col("career", "경력"),
            col("date", "날짜"),
            col("mode", "방식", "choice", ("대면", "화상", "전화")),
        ),
        fixed_table(
            "questions",
            None,
            (
                fx("주제"),
                fx("질문"),
                col("summary", "답 요지", "long"),
                col("quote", "그대로 받아 적은 말", "long"),
            ),
            [
                (t, q, "", "")
                for t, q in (
                    ("의사결정", "결정할 때 가장 먼저 보는 값과 기준 숫자는 무엇입니까"),
                    ("예외 상황", "평소와 다르게 처리하는 경우는 언제입니까"),
                    ("예외 상황", "자주 생기는 문제(오류·지연)는 무엇입니까"),
                    ("의사결정", "문서에 없지만 알고 있는 요령이 있습니까"),
                    ("예외 상황", "크게 잘못된 적이 있다면 원인은 무엇이었습니까"),
                    ("의사결정", "여러 건이 겹치면 무엇부터 처리합니까"),
                    ("업무 흐름", "가장 불편하거나 바꾸고 싶은 단계는 어디입니까"),
                    ("찾아보는 질문", "앞으로 조심해서 지켜봐야 할 신호는 무엇입니까"),
                )
            ],
            note=(
                "OntoBot 워크숍은 후보마다 근거 문장이 발언에 그대로 있는지 확인합니다(15장). 중요한 말은 요약하지 말고 "
                "그대로 받아 적어 두세요. 같은 말을 워크숍에서 다시 하면 근거가 맞습니다."
            ),
        ),
        fixed_table(
            "hints",
            "설계도로 옮길 힌트",
            (fx("종류"), col("found", "찾은 것", "long")),
            _rows(
                "객체 타입(무엇에 대한 이야기인가)",
                "상태(목록 값 속성: 정상·점검중·폐쇄 등)",
                "링크(무엇과 무엇이 이어지나)",
                "규칙(Action 사전 조건·Function 계산)",
                "성공 지표 후보",
                width=2,
            ),
        ),
        free_table(
            "followups",
            None,
            col("item", "더 확인할 것"),
            col("question", "관련 질문"),
            col("priority", "우선순위(상/중/하)", "choice", HML),
            col("method", "확인 방법"),
            col("owner", "담당"),
            col("done", "완료", "check"),
        ),
    ),
)

# ---------------------------------------------------------------- 4단계 설계

CONCEPTS = Sheet(
    kind="concepts",
    number="36.10",
    title="질문 → 개념 추출표",
    stage=4,
    intro=(
        "인터뷰·회의 답에서 개념을 뽑아 설계도 요소로 바꿉니다. 열은 워크숍이 뽑는 요소(15장)와 같습니다. 한 줄에 "
        "질문 하나를 적고, 답에서 개념이 여러 개 나오면 중요한 것부터 적습니다."
    ),
    sections=(
        free_table(
            "rows",
            None,
            col("no", "번호"),
            col("question", "질문 요지"),
            col("answer", "답 한 문장"),
            col("object_type", "객체 타입"),
            col("property", "속성(자료형)"),
            col("link", "링크"),
            col("action", "Action 후보"),
            col("golden", "골든 질문"),
            blank=3,
        ),
        fixed_table(
            "checks",
            "뽑은 개념 점검",
            (fx("확인"), col("answer", "예/아니오", "choice", YESNO)),
            _rows(
                '"좋다·많다·빠르다" 같은 말을 숫자나 목록 값으로 바꿨는가',
                "같은 대상에 이름이 둘 이상 붙지 않았는가(워크숍 병합은 이름이 글자 그대로 같을 때만 합친다)",
                "영문 API 이름 규칙을 지켰는가(32장)",
                "객체 타입마다 고유 번호(pk)가 될 식별자가 있는가",
                "원천 DB 의 어느 컬럼에서 오는지 알 수 있는가",
                width=2,
            ),
        ),
        free_table(
            "priorities",
            "먼저 설계할 개념 다섯 개",
            col("rank", "순위"),
            col("concept", "개념"),
            col("reason", "이유"),
            col("related", "연결되는 것"),
            col("kpi", "성공 지표 후보"),
            blank=5,
        ),
    ),
)

GLOSSARY = Sheet(
    kind="glossary",
    number="36.11",
    title="도메인 용어집",
    stage=4,
    intro=(
        '현장에서 쓰는 말을 정리합니다. 이 표의 "표시 이름"과 "설명"은 설계도에 그대로 들어갑니다(16장). 챗봇은 '
        "질문을 조회 조건으로 바꿀 때 설계도의 이름과 설명을 읽습니다."
    ),
    sections=(
        free_table(
            "terms",
            None,
            col("term", "용어"),
            col("display_name", "표시 이름"),
            col("api_name", "API 이름"),
            col("description", "설명(한두 문장)", "long"),
            col("aliases", "같은 뜻의 다른 말"),
            col("enum_values", "목록 값"),
            col("source_column", "원천 컬럼"),
            col("importance", "중요도(상/중/하)", "choice", HML),
            blank=2,
            note=(
                "예시(샘플 도메인) 용어: 위험등급 / 표시 이름: 위험등급 / API 이름: risk_grade / 설명: 점검 결과로 "
                "매기는 1~5 정수, 5가 가장 위험 / 같은 뜻의 다른 말: 위험도, 등급"
            ),
        ),
        free_table(
            "unstable",
            "뜻이 흔들리는 말 정리",
            col("word", "흔들리는 말"),
            col("meanings", "지금 쓰이는 뜻들"),
            col("confusion", "생긴 혼선"),
            col("definition", "합의한 정의(목록 값)"),
            col("agreed_by", "합의한 사람", pii=True),
            blank=2,
            note='예: "처리 완료" — 조치 끝 / 보고까지 끝 — 건수가 부서마다 다름 — done = 결과 보고까지 마친 것',
        ),
        fixed_table(
            "notation",
            "표기 규칙",
            (fx("규칙"), col("decided", "정한 내용")),
            _rows(
                "줄임말(처음엔 풀어 쓰고 괄호에 줄임말)",
                "영문 표기",
                '쓰지 않을 말("대충", "빠르게")',
                "용어집 고치는 주기·담당",
                width=2,
            ),
        ),
    ),
)

ELEMENTS = Sheet(
    kind="elements",
    number="36.12",
    title="온톨로지 7요소 설계표",
    stage=4,
    intro="현장 지식을 일곱 요소로 나누어 설계합니다. 일곱 요소는 모두 OntoBot 의 기능 하나씩에 대응합니다.",
    sections=(
        fixed_table(
            "elements",
            None,
            (
                fx("요소"),
                fx("OntoBot 에서는"),
                col("items", "주요 항목", "long"),
                fx("예시(샘플 도메인)"),
                fx("이 책"),
            ),
            [
                ("객체", "객체 타입(pk·제목)", "", "시설 Facility", "16.7"),
                ("속성", "속성(자료형·필수·PII)", "", "위험등급 risk_grade 정수", "16.6"),
                ("관계", "링크 타입(FK 속성으로 연결)", "", "시설 → 소유 조직", "16.8"),
                ("상태", "목록 값(enum) 속성", "", "시설 유형: 변전소·철탑·전주", "16.6"),
                (
                    "액션",
                    "Action(파라미터·사전 조건·바뀔 값·알림)",
                    "",
                    "위험등급 변경(승인 필요)",
                    "17장",
                ),
                ("권한", "데이터 그룹·속성 숨김·행 정책", "", "점검팀은 시설만 읽기", "21.3"),
                (
                    "KPI",
                    "Function(정해 둔 계산)·성공 지표 목표",
                    "",
                    "고위험 시설 비율",
                    "16.13, 36.14",
                ),
            ],
        ),
        fields(
            "memos",
            None,
            col("core", "핵심 객체 타입 다섯 개", "long"),
            col("relations", "관계 메모", "long"),
            col("states", "상태 메모", "long"),
            col("rules", "규칙 메모", "long"),
        ),
        fixed_table(
            "review",
            "설계 검토",
            (fx("확인"), col("answer", "예/아니오", "choice", YESNO)),
            _rows(
                "현장 용어가 표시 이름·설명에 그대로 들어갔는가(36.11)",
                '현업 전문가가 보고 "우리 일"이라고 알아보는가',
                "모든 속성이 원천 DB 컬럼과 매핑되는가(18장)",
                "게시 전 검사와 게시 영향에서 막히는 것이 없는가(16.3, 16.11)",
                "성공 지표를 이 설계로 계산할 수 있는가",
                width=2,
            ),
        ),
        fields("summary", None, col("summary", "설계 한 줄 요약", "long")),
    ),
)

ACTION_FLOW = Sheet(
    kind="action_flow",
    number="36.13",
    title="Action 업무 흐름 설계표",
    stage=4,
    intro="데이터를 바꾸는 업무를 OntoBot 의 Action 흐름에 맞춰 설계합니다.",
    sections=(
        fields(
            "meta",
            None,
            col("task", "업무명"),
            col("trigger", "시작하는 일(이벤트)"),
            col("done", "끝난 모습"),
            col("users", "쓰는 사람"),
            col("source", "원천 시스템"),
            note=(
                "OntoBot 은 사람 승인 없이 데이터를 바꾸지 않습니다. 채팅에서 나온 변경 제안도 미리보기까지만 만들고, "
                "실행은 승인자가 승인할 때 일어납니다(9~12장)."
            ),
        ),
        fixed_table(
            "roles",
            None,
            (fx("하는 일"), fx("OntoBot 에서는"), col("content", "이 업무에서 맡을 내용", "long")),
            [
                ("알아차리기", "동기화로 들어온 값, 편집 충돌 표시(19장)", ""),
                ("분석하기", "채팅 질문, Function 계산", ""),
                ("제안하기", "채팅의 Action 미리보기(10장)", ""),
                ("실행하기", "승인자 승인 = 실행(12장)", ""),
            ],
        ),
        fixed_table(
            "steps",
            None,
            (
                fx("단계"),
                fx("입력 데이터"),
                col("rule", "처리(규칙)"),
                col("owner", "담당(사람·역할)"),
                col("integration", "연동"),
                col("exception", "예외 처리"),
            ),
            [
                ("1 요청", "대상 객체·파라미터", "", "요청자", "", ""),
                ("2 판단", "사전 조건", "", "시스템", "", "조건 불충족이면 요청 막힘"),
                ("3 미리보기", "바뀔 값", "", "시스템", "", "알림 채널이 꺼져 있으면 막힘"),
                ("4 승인", "미리보기", "", "승인자", "", "거부 사유 기록"),
                (
                    "5 실행·알림",
                    "바뀐 값",
                    "",
                    "시스템",
                    "메일·SMS",
                    "실행 직전 재검증 실패면 실패로 기록",
                ),
                ("6 기록", "실행 기록·감사 로그", "", "시스템", "", "롤백(13장)"),
            ],
        ),
        free_table(
            "failures",
            None,
            col("scenario", "실패·예외 시나리오"),
            col("cause", "원인"),
            col("impact", "영향"),
            col("response", "대응"),
            col("retry", "다시 시도·우회"),
        ),
        fixed_table(
            "kpis",
            None,
            (fx("성과 지표"), col("target", "목표")),
            [
                ("요청부터 실행까지 걸린 시간", "분/건"),
                ("Action 실행 성공률", "%"),
                ("승인 거부율", "%"),
                ("롤백한 건수", "건/월"),
            ],
        ),
    ),
)

# ---------------------------------------------------------------- 5단계 파일럿과 자산화

PILOT = Sheet(
    kind="pilot",
    number="36.14",
    title="거버넌스·성공 지표·파일럿 판단표",
    stage=5,
    intro=(
        "파일럿을 안전하게 운영하고, 끝에 계속할지 판단하는 표입니다. 시스템 지표의 목표는 관리 화면의 성공 지표 "
        "목표에 넣고(운영자 매뉴얼 38.9), 업무 지표는 측정 방법을 함께 적습니다."
    ),
    sections=(
        fields(
            "meta",
            None,
            col("name", "파일럿 이름"),
            col("task", "대상 업무"),
            col("period", "기간"),
            col("departments", "참여 부서"),
            col("owner", "책임자", pii=True),
        ),
        fixed_table(
            "governance",
            "거버넌스",
            (fx("항목"), fx("OntoBot 장치"), col("decided", "정한 내용", "long")),
            [
                ("권한", "역할(화면·기능)과 데이터 그룹(데이터)", ""),
                ("개인정보", "PII 속성 표시, 기록·AI 전송 가림", ""),
                ("승인 체계", "Action 승인자", ""),
                ("기록·감사", "감사 로그(고칠 수 없음), 트레이스", ""),
                ("장애·예외", "장애 대응 절차(운영자 매뉴얼 27장)", ""),
            ],
        ),
        fixed_table(
            "metrics",
            "성공 지표",
            (
                fx("지표"),
                fx("종류"),
                fx("정의"),
                fx("재는 방법"),
                col("current", "지금 값"),
                col("target", "목표"),
            ),
            [
                ("답 정답률", "시스템", "골든셋 평가에서 맞힌 비율", "평가 실행(20장)", "", ""),
                (
                    "근거 등급 ① 비율",
                    "시스템",
                    "모든 문장에 근거가 붙은 답의 비율",
                    "평가 결과",
                    "",
                    "",
                ),
                ("응답 시간", "시스템", "질문부터 답까지", "평가 결과·트레이스", "", ""),
                (
                    "Action 실행 성공률",
                    "시스템",
                    "실행 성공 / 실행 시도",
                    "관리 화면 운영 지표",
                    "",
                    "",
                ),
                (
                    "조회에 드는 시간",
                    "업무",
                    "정보를 찾는 데 걸리는 시간",
                    "도입 전후 관찰(36.7)",
                    "",
                    "",
                ),
                (
                    "활용도",
                    "업무",
                    "주간 질문한 사람 수 / 대상 인원",
                    "트레이스 목록(21.5)",
                    "",
                    "",
                ),
            ],
        ),
        fixed_table(
            "phases",
            "파일럿 단계",
            (fx("단계"), fx("할 일"), fx("결과물")),
            [
                ("준비", "범위·데이터·권한·지표 확정", "36.1·36.6·36.12"),
                ("시험", "골든셋 평가, 게이트 통과 확인", "평가 결과(20장)"),
                ("운영", "정해진 사용자만 쓰기, 피드백 모으기", "피드백·답 오류 신고(35.1)"),
                ("중간 점검", "지표 다시 재기, 틀린 문항 고치기", "수정한 설계도 버전"),
                ("종료 평가", "최종 지표, 계속할지 판단", "이 표"),
            ],
        ),
        fixed_table(
            "risks",
            None,
            (
                fx("위험"),
                col("likelihood", "가능성", "choice", LMH),
                col("impact", "영향", "choice", LMH),
                col("response", "대응"),
            ),
            _rows(
                "데이터 품질이 낮음",
                "답이 자주 틀림",
                "개인정보 노출",
                "서버 장애",
                "사용자가 쓰지 않음",
                width=4,
            ),
        ),
        fields(
            "decision",
            "판단",
            col("decision", "판단", "choice", ("계속(전사로 넓힘)", "보완 뒤 계속", "보류")),
            col("plan", "보완 계획", "long"),
            col("findings", "핵심 발견", "long"),
            col("decisions", "주요 결정", "long"),
            col("must_fix", "꼭 보완할 것", "long"),
            col("next", "다음 일정"),
            note=(
                "계속: 평가 게이트를 통과했고 불변식 위반이 없으며 업무 지표 목표에 닿았다. 보완 뒤 계속: 게이트는 "
                "통과했지만 일부 지표가 모자란다. 보류: 게이트 실패, 또는 불변식 위반(권한·지어낸 근거)이 있다. "
                "불변식 위반은 평균 점수로 상쇄할 수 없습니다(20.6)."
            ),
        ),
    ),
)

ASSETS = Sheet(
    kind="assets",
    number="36.15",
    title="도입 결과 자산화 기록",
    stage=5,
    intro="도입이 끝나면 다음 도입이 처음부터 시작하지 않도록 결과를 남깁니다.",
    sections=(
        fixed_table(
            "outputs",
            None,
            (fx("결과물"), fx("어디서 나오나"), col("kept", "남김", "check")),
            [
                ("설계도(온톨로지)", "내보내기(16.10)", ""),
                ("합성 예시 데이터", "실제 값을 바꾼 표본 CSV", ""),
                ("골든셋", "평가 화면(20장)", ""),
                ("Action·Function 정의", "설계도 안", ""),
                ("등록 문서 목록", "문서 화면(18.8)", ""),
                ("이 장의 양식 사본", "36.1~36.14", ""),
            ],
            note=(
                "실제 고객 데이터는 남기지 않습니다. 밖으로 가져갈 수 있는 것은 설계도·합성 데이터·양식뿐입니다. "
                "개인정보가 섞이지 않았는지 반드시 확인하세요."
            ),
        ),
        fixed_table(
            "record",
            None,
            (fx("항목"), col("content", "내용", "long")),
            _rows(
                "자산 이름 / 도메인 / 버전",
                "풀었던 문제(36.5 한 문장)",
                "핵심 질문(골든 질문 대표 5개)",
                "설계도 요약(객체 타입·링크 수)",
                "결과 지표(36.14)",
                "배운 점",
                "다른 곳에 쓸 수 있는 범위",
                "보안 등급·공개 범위",
                width=2,
            ),
        ),
        fixed_table(
            "quality",
            None,
            (fx("품질 확인"), col("answer", "예/아니오", "choice", YESNO)),
            _rows(
                "출처가 분명한가(어느 도입, 누가 만들었나)",
                "다른 사람이 같은 결과를 다시 만들 수 있는가(합성 데이터로 골든 기대값을 다시 계산)",
                "성과 숫자에 근거가 있는가",
                "보안 등급과 공개 범위가 맞는가",
                width=2,
            ),
        ),
        fields(
            "registration",
            "등록 결정",
            col("decision", "등록 결정", "choice", ("등록", "보류", "반려")),
            col("approver", "승인자", pii=True),
            col("date", "날짜"),
            col("next_review", "다음 검토일"),
        ),
    ),
)

ROADMAP = Sheet(
    kind="roadmap",
    number="36.16",
    title="도입 순서 한눈에",
    stage=0,
    intro="인터뷰 워크숍은 3단계와 4단계 사이에, 평가(20장)는 5단계에서 합니다. 기간·누가는 이 도입에 맞게 고칩니다.",
    sections=(
        fixed_table(
            "stages",
            None,
            (
                fx("단계"),
                col("period", "기간(예시)"),
                col("who", "누가"),
                fx("쓸 양식"),
                fx("OntoBot 에서 할 일"),
            ),
            [
                (
                    "1 착수 준비",
                    "1주",
                    "도입 담당·고객 책임자",
                    "36.1~36.3",
                    "설치 준비(운영자 매뉴얼 4장)",
                ),
                ("2 문제 정의", "1주", "도입 담당·결정권자", "36.4~36.6", "계정·데이터 그룹 초안"),
                (
                    "3 현장 이해",
                    "1~2주",
                    "도입 담당·현업 전문가",
                    "36.7~36.9",
                    "인터뷰 워크숍(14·15장)",
                ),
                (
                    "4 설계",
                    "1~2주",
                    "모델러",
                    "36.10~36.13",
                    "데이터 연결·설계도 게시·Action 정의(16~18장)",
                ),
                ("5 파일럿", "2~4주", "모두", "36.14", "골든셋 평가(20장)·성공 지표 목표"),
                ("자산화", "끝난 뒤", "도입 담당", "36.15", "설계도 내보내기"),
            ],
        ),
    ),
)

SHEETS: tuple[Sheet, ...] = (
    PRECHECK,
    FIT,
    SYSTEMS,
    KICKOFF,
    REQUEST,
    STAKEHOLDERS,
    OBSERVATION,
    ASIS,
    TACIT,
    CONCEPTS,
    GLOSSARY,
    ELEMENTS,
    ACTION_FLOW,
    PILOT,
    ASSETS,
    ROADMAP,
)
BY_KIND: dict[str, Sheet] = {s.kind: s for s in SHEETS}


def get(kind: str) -> Sheet | None:
    return BY_KIND.get(kind)


# ---------------------------------------------------------------- 검증(같은 선언에서 만든 모델)


class SheetInvalid(Exception):
    def __init__(self, issues: list[dict[str, str]]) -> None:
        super().__init__("양식 값이 올바르지 않습니다")
        self.issues = issues


def _value_type(c: Column) -> Any:
    if c.kind == "check":
        return bool | None
    if c.kind == "choice":
        choice: Any = Literal.__getitem__(c.options)
        return choice | None
    return (
        Annotated[str, StringConstraints(max_length=LONG_MAX if c.kind == "long" else TEXT_MAX)]
        | None
    )


_STRICT = ConfigDict(extra="forbid")


@cache
def model(kind: str) -> type[BaseModel]:
    """양식 payload 모델. 입력 열이 없는 묶음(참고 표)은 payload 에 없다."""
    sheet = BY_KIND[kind]
    members: dict[str, Any] = {}
    for s in sheet.sections:
        if not s.inputs:
            continue
        cell = create_model(  # type: ignore[call-overload]
            f"{kind}_{s.key}",
            __config__=_STRICT,
            **{c.key: (_value_type(c), None) for c in s.inputs},
        )
        if s.layout == "fields":
            members[s.key] = (cell | None, None)
        else:
            limit = len(s.rows) if s.rows else MAX_ROWS
            members[s.key] = (list[cell], Field(default_factory=list, max_length=limit))  # type: ignore[valid-type]
    sheet_model: type[BaseModel] = create_model(f"sheet_{kind}", __config__=_STRICT, **members)
    return sheet_model


def _blank_to_none(value: Any) -> Any:
    """빈 글자는 값 없음 — 화면이 지운 칸을 ""로 보내도 같은 payload 가 된다."""
    if isinstance(value, str):
        return value if value.strip() else None
    if isinstance(value, dict):
        return {k: _blank_to_none(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_blank_to_none(v) for v in value]
    return value


def validate(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """선언에 맞는지 보고 정리한 payload 를 돌려준다. 문제는 모두 모아 ``SheetInvalid`` 로."""
    sheet = BY_KIND[kind]
    try:
        parsed = model(kind).model_validate(_blank_to_none(payload))
    except ValidationError as e:
        raise SheetInvalid(
            [
                {"path": ".".join(str(p) for p in err["loc"]), "message": err["msg"]}
                for err in e.errors()
            ]
        ) from e
    out: dict[str, Any] = parsed.model_dump(mode="json", exclude_defaults=True)
    for s in sheet.sections:
        rows = out.get(s.key)
        if s.layout != "table" or not isinstance(rows, list):
            continue
        # 행을 더하는 표의 빈 행은 버린다. 행이 정해진 표는 순서가 뜻이라 끝의 빈 행만 버린다.
        if s.rows:
            while rows and not rows[-1]:
                rows.pop()
        else:
            rows = [r for r in rows if r]
        if rows:
            out[s.key] = rows
        else:
            out.pop(s.key)
    return out
