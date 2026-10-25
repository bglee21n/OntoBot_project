"""부록 「질문 예시 모음」 — 질문들을 실제 API 로 돌려 조회 조건·결과·답을 모아 Markdown 으로 쓴다.

    ONTOBOT_MANUAL_PASSWORD=... python docs/manual/tools/question_bank.py   # 표준 라이브러리만 쓴다

매뉴얼 환경(manual_env.py)의 API(:8000)에 ``jung.builder`` 로 묻는다. 결과는
``question_bank.json``(원자료)과 ``../user/25-question-bank.md``(본문)로 남긴다. 답은 AI 가 쓰므로
다시 돌리면 문장이 조금 달라질 수 있다(숫자·조회 조건은 데이터가 같으면 같다).
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = os.environ.get("ONTOBOT_MANUAL_API", "http://127.0.0.1:8000/api/v1")
HERE = Path(__file__).resolve().parent
OUT_JSON = HERE / "question_bank.json"
OUT_MD = HERE.parent / "user" / "25-question-bank.md"

# (분류, 설명, [질문 또는 [이어서 묻는 질문들]])
BANK: list[tuple[str, str, list[str | list[str]]]] = [
    ("개수 세기", "조건에 맞는 대상이 몇 개인지 셉니다.", [
        "시설은 모두 몇 개야?", "변전소는 몇 개야?", "철탑은 몇 개 있어?", "전주 개수 알려줘",
        "위험등급이 3 이상인 시설은 몇 개야?", "위험등급이 4 이상인 시설 수는?", "위험등급이 1인 시설은 몇 곳이야?",
        "위험등급이 5인 변전소는 몇 개야?", "위험등급이 5인 전주는 몇 개야?", "위험등급이 2 이하인 철탑은 몇 개?",
        "설비 용량이 1000kW 넘는 시설은 몇 개야?", "조직은 몇 개야?", "지역은 몇 개 등록돼 있어?",
    ]),
    ("목록 보기", "조건에 맞는 대상을 나열합니다. 기본으로 최대 20건까지 보여 줍니다.", [
        "변전소 목록 보여줘", "철탑 목록 보여줘", "전주 목록 알려줘", "위험등급이 5인 시설 목록 알려줘",
        "위험등급이 4인 철탑 보여줘", "설비 용량이 2000kW 이상인 시설 보여줘", "시설 목록 전부 보여줘",
        "조직 목록 보여줘", "지역 목록 알려줘",
    ]),
    ("한 대상의 정보", "식별자(pk)나 이름으로 대상 하나를 찾아 정보를 봅니다.", [
        "F-0007 시설의 설비 용량은?", "F-0001 정보 알려줘", "강남 변전소 13호 정보 알려줘",
        "부산 철탑 02호의 위험등급과 설치일 알려줘", "수원 전주 12호는 어느 조직 소유야?",
        "대구 변전소 19호 설치일은 언제야?", "강남지사 담당자 연락처 알려줘", "지역 코드 41은 어디야?",
        "강남변전소1호 위험등급 알려줘",
    ]),
    ("순위·상위 몇 개", "어떤 값이 큰(작은) 순서로 몇 개를 봅니다.", [
        "위험등급이 가장 높은 시설 5개 알려줘", "설비 용량이 큰 순서로 변전소 3개 보여줘",
        "설비 용량이 가장 작은 시설 3개는?", "가장 최근에 설치된 시설 5개 보여줘", "가장 오래된 철탑 3개 알려줘",
    ]),
    ("묶어서 세기", "어떤 값별로 몇 개씩 있는지 봅니다.", [
        "시설 유형별 개수 알려줘", "위험등급별 시설 수 알려줘", "지역 코드별 시설 수 알려줘",
        "소유 조직별 시설 수는?", "시설 유형별 평균 위험등급은?", "시설 유형별 설비 용량 합계 알려줘",
    ]),
    ("평균·합계·최대·최소", "숫자 속성의 평균·합계 등을 계산합니다.", [
        "변전소의 평균 설비 용량은?", "철탑 설비 용량 합계는?", "전체 시설의 평균 위험등급은?",
        "용량이 1000kW 넘는 변전소의 평균 위험등급은?", "위험등급 3 이상인 변전소의 평균 설비 용량은?",
        "설비 용량이 가장 큰 값은 얼마야?", "전주의 최소 설비 용량은?",
    ]),
    ("날짜 조건", "설치일 같은 날짜로 거릅니다.", [
        "2010년 이전에 설치된 철탑은?", "2020년 이후에 설치된 시설 목록 보여줘", "2015년에 설치된 시설 알려줘",
        "2010년부터 2015년 사이에 설치된 변전소는?", "설치된 지 10년 넘은 전주는 몇 개야?",
    ]),
    ("관계 따라가기", "시설과 이어진 조직·지역의 조건으로 거릅니다.", [
        "강남지사가 소유한 시설 목록 보여줘", "부산지사 소유 시설은 몇 개야?", "서울에 있는 변전소는?",
        "경기에 있는 시설 목록", "대구 지역의 위험등급 4 이상 시설은?",
        "부산지사가 소유한 시설 중 위험등급이 4 이상인 것은?", "경남에 있는 시설 알려줘",
        "수원지사 소유 철탑의 평균 설비 용량은?",
    ]),
    ("조건 여러 개", "조건을 함께 씁니다. 조회 조건에 모두 들어갔는지 꼭 확인하세요.", [
        "경기 지역 철탑 중 위험등급이 4 이상인 것 보여줘", "서울에 있는 위험등급 2 이하 시설",
        "2015년 이후 설치된 위험등급 4 이상 철탑", "강남지사 소유 변전소 중 용량 2000kW 이상인 것",
    ]),
    ("이어서 묻기", "앞 답의 목록을 '그 중'으로 이어서 좁힙니다. 한 묶음은 같은 대화에서 차례로 물었습니다.", [
        ["부산지사가 소유한 시설 목록 보여줘", "그 중 위험등급이 4 이상인 것만", "그 시설들의 설비 용량 합계는?"],
        ["변전소 목록 보여줘", "그 중 서울에 있는 것", "그 중 위험등급이 가장 높은 것은?"],
        ["위험등급 5인 시설 보여줘", "그 중 철탑만", "그 시설들 설치일 알려줘"],
    ]),
    ("답할 수 없는 질문", "데이터에 없는 정보를 물으면 지어내지 않고 실패로 알립니다.", [
        "시설 점검 담당자 이름 알려줘", "다음 달 점검 일정 알려줘", "변전소 고장 원인이 뭐야?",
        "시설별 전기 요금 알려줘", "가장 위험한 시설은 왜 위험해?",
    ]),
    ("결과 0건", "조회는 성공했지만 조건에 맞는 데이터가 없습니다.", [
        "경남에 있는 시설 알려줘", "위험등급이 9인 시설 보여줘", "2030년 이후에 설치된 시설은?", "제주 변전소 99호 정보 알려줘",
    ]),
    ("데이터 질문이 아닌 말", "인사·잡담은 데이터 질문이 아니라고 안내합니다.", [
        "안녕하세요", "오늘 날씨 어때?", "고마워", "너는 누구야?",
    ]),
    ("같은 뜻 다른 말", "같은 질문을 말투만 바꿔 물었습니다. 조회 조건이 같은지 비교해 보세요.", [
        "변전소 몇 개 있어?", "변전소 개수", "변전소가 총 몇 곳인지 알려주세요", "등록된 변전소 수를 세어줘",
        "변전소는 전부 몇 개입니까?", "위험등급 3 이상 시설 수", "위험등급이 3보다 크거나 같은 시설은 몇 개?",
        "위험등급 3 이상인 시설이 몇 군데야?", "위험한 시설(위험등급 3 이상) 개수 알려줘",
        "부산지사 시설 목록", "부산지사에서 관리하는 시설 보여줘", "부산지사 소속 시설을 보여주세요",
        "철탑 중에 위험등급 5인 거", "위험등급 5짜리 철탑 알려줘", "철탑이면서 위험등급이 5인 시설 목록",
    ]),
    ("조직·지역 질문", "시설이 아닌 조직·지역 데이터와, 조직·지역을 기준으로 한 질문입니다.", [
        "조직 목록을 담당자와 함께 보여줘", "ORG-03 조직 이름 알려줘", "대구지사 담당자는 누구야?",
        "담당자 연락처가 010-0000-0004인 조직은?", "지역 코드가 26인 지역 이름은?", "경남 지역 코드 알려줘",
        "지역별 시설 수 알려줘", "조직별 평균 위험등급 알려줘", "시설이 가장 많은 지역은?",
    ]),
    ("점검 기록 질문", "22장에서 연결한 점검 기록(Inspection) 데이터에 대한 질문입니다.", [
        "점검 기록은 몇 건이야?", "점검 기록 목록 보여줘", "점검 결과가 정상인 점검 기록은 몇 건?",
        "2024년에 한 점검 기록 보여줘", "가장 최근 점검 기록 3개 알려줘", "I-005 점검 기록 알려줘",
        "F-0026 시설의 점검 기록 보여줘", "점검 대상 시설이 대구 변전소 07호인 점검 기록은?",
        "점검 결과별 점검 기록 수", "점검일이 2025-03-21인 점검 기록은?",
    ]),
    ("오타·띄어쓰기", "이름·낱말을 조금 다르게 썼을 때입니다. 이름은 글자 그대로 같아야 찾습니다.", [
        "강남 변전소 1호 위험등급 알려줘", "강남변전소 01호 위험등급 알려줘", "부산철탑02호 정보", "f-0007 설비 용량",
        "F0007 설비 용량은?", "변전쏘 몇 개야?", "위험 등급이 4 이상인 시설 수", "강남 지사 소유 시설 몇 개야?",
    ]),
    ("길게 묻기", "배경 설명을 섞어 길게 물었을 때입니다. 핵심 조건이 조회 조건에 들어갔는지 보세요.", [
        "이번 주 점검 계획을 세우려고 하는데, 위험등급이 4 이상인 변전소가 몇 개인지 알려줄래?",
        "보고서를 써야 해서 그런데요, 부산지사가 소유한 철탑 목록 좀 부탁드립니다.",
        "혹시 2010년 이전에 설치된 오래된 전주들이 어떤 게 있는지 궁금합니다.",
        "팀장님이 물어보셔서 그러는데 설비 용량이 제일 큰 변전소 3개만 뽑아줘.",
        "위험등급이 5인 시설 중에서 서울에 있는 것만 알고 싶어요. 가능할까요?",
    ]),
    ("경계 조건", "이상·초과·이하·미만과 날짜 경계를 어떻게 이해하는지 봅니다.", [
        "위험등급 3 초과인 시설 몇 개?", "위험등급 3 미만인 시설 몇 개?", "위험등급이 2에서 4 사이인 시설은 몇 개야?",
        "설비 용량이 1200kW인 시설 보여줘", "설비 용량이 1200kW 이상 2400kW 이하인 변전소",
        "2017년 1월 1일 이후 설치된 철탑", "2017년 1월 1일 포함 이후에 설치된 철탑", "2016년 말까지 설치된 전주는 몇 개?",
    ]),
    ("데이터 바꾸기 요청(Action)", "바꾸는 요청은 미리보기만 만듭니다. 실제 변경은 승인 뒤입니다.", [
        "F-0007 시설 위험등급을 5로 바꿔줘", "대구 철탑 11호 긴급 점검 요청해줘", "강남 전주 21호 일반 점검 요청해줘",
        "F-0003 위험등급 바꿔줘", "제주 변전소 99호 위험등급을 2로 바꿔줘", "F-0010 설비 용량을 1500으로 정정해줘. 사유는 현장 실측",
    ]),
]


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def post(op: urllib.request.OpenerDirector, path: str, body: dict, stream: bool = False):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"},
        method="POST",
    )
    return op.open(req, timeout=600)


def ask(op: urllib.request.OpenerDirector, question: str, conversation: int | None) -> dict:
    t0 = time.time()
    res = post(op, "/chat", {"question": question, "conversation_id": conversation}, stream=True)
    out: dict = {"question": question}
    event = None
    for raw in res:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: ") and event:
            data = json.loads(line[6:])
            if event == "conditions":
                out["conditions"] = data.get("text")
            elif event == "answer":
                out["answer"] = data.get("text")
            elif event == "grade":
                out["status"] = data.get("status")
                out["grade"] = data.get("grade")
            elif event == "citations":
                out["citations"] = len(data)
            elif event == "action_preview":
                out["status"] = "action_preview"
                out["answer"] = data.get("text")
                out["action"] = {k: data.get(k) for k in ("display_name", "target_pk", "target_title", "issues", "run_id")}
            elif event == "error":
                out["status"] = "failed"
                out["failure"] = data.get("reason")
                out["answer"] = data.get("message")
            elif event == "done":
                out["conversation_id"] = data.get("conversation_id")
    out["seconds"] = round(time.time() - t0, 1)
    return out


GRADE = {1: "①", 2: "②", 3: "③"}
FAIL = {
    "plan_invalid": "질문 해석 실패", "forbidden": "권한 없음", "graph_unavailable": "관계 저장소 연결 불가",
    "timeout": "시간 초과", "execution_error": "실행 오류", "too_broad": "결과 과다", "llm_unavailable": "답변 모델 연결 불가",
}


def result_label(r: dict) -> str:
    st = r.get("status")
    if st == "ok":
        return f"정상 {GRADE.get(r.get('grade'), '')}"
    if st == "empty":
        return "결과 0건"
    if st == "not_query":
        return "질의 아님"
    if st == "action_preview":
        a = r.get("action") or {}
        return "Action 미리보기" if a.get("run_id") else "Action 확인할 것"
    if st == "failed":
        return f"조회 실패({FAIL.get(r.get('failure'), r.get('failure'))})"
    return st or "—"


def cell(text: str | None, limit: int = 160) -> str:
    if not text:
        return "—"
    t = " ".join(text.replace("|", "/").replace("**", "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def write_md(results: list[dict]) -> None:
    lines = [
        "# 25장 질문 예시 모음",
        "",
        "이 장은 예제 데이터에 **실제로 물어본 질문과 그 결과**를 모은 것입니다. 질문을 어떻게 써야 할지 막힐 때,",
        "비슷한 질문을 찾아 그대로 또는 조금 바꿔 물어보세요. 표의 칸은 다음 뜻입니다.",
        "",
        "| 칸 | 뜻 |",
        "| --- | --- |",
        "| 질문 | 채팅 칸에 입력한 글 그대로 |",
        "| 조회 조건 | 챗봇이 질문을 이해한 내용(프로그램이 실제 조회한 조건). 내 뜻과 같은지 확인하는 칸 |",
        "| 결과 | 정상 ①②③ / 결과 0건 / 조회 실패(이유) / 질의 아님 / Action 미리보기 |",
        "| 답 | 화면에 나온 답(길면 앞부분만). AI 가 쓰므로 다시 물으면 문장이 조금 다를 수 있습니다 |",
        "",
        "> **주의** 결과가 \"정상 ①\"이어도 조회 조건이 내 질문과 다르면 답도 내 뜻과 다릅니다. 표에서도 그런 줄이",
        "> 몇 개 있습니다(각 분류 끝의 \"살펴볼 점\"에 적었습니다). 이것이 조회 조건을 먼저 확인해야 하는 이유입니다.",
        "",
    ]
    by_cat: dict[str, list[dict]] = {}
    for r in results:
        by_cat.setdefault(r["category"], []).append(r)
    n = 0
    for no, (cat, desc, _) in enumerate(BANK, 1):
        rows = by_cat.get(cat, [])
        lines += [f"## 25.{no} {cat}", "", desc, ""]
        lines += ["| 번호 | 질문 | 조회 조건 | 결과 | 답 |", "| --- | --- | --- | --- | --- |"]
        for r in rows:
            n += 1
            q = ("↳ " if r.get("followup") else "") + r["question"]
            lines.append(
                f"| {n} | {cell(q, 80)} | {cell(r.get('conditions'), 120)} | {result_label(r)} | {cell(r.get('answer'))} |"
            )
        lines += ["", "<!-- notes:" + cat + " -->", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    pw = os.environ.get("ONTOBOT_MANUAL_PASSWORD")
    if not pw:
        sys.exit("ONTOBOT_MANUAL_PASSWORD 를 주세요")
    op = opener()
    post(op, "/auth/local/login", {"username": os.environ.get("ONTOBOT_MANUAL_USER", "jung.builder"), "password": pw}).read()
    results: list[dict] = []
    for cat, _, items in BANK:
        for item in items:
            chain = item if isinstance(item, list) else [item]
            conv = None
            for k, q in enumerate(chain):
                r = ask(op, q, conv)
                conv = r.get("conversation_id") if isinstance(item, list) else None
                r["category"] = cat
                r["followup"] = k > 0
                results.append(r)
                print(f"[{cat}] {q} → {result_label(r)} ({r['seconds']}s)", flush=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    write_md(results)
    print(f"{len(results)}문항 → {OUT_MD}")


if __name__ == "__main__":
    main()
