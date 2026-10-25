"""37장 「찾아보기」 — 낱말마다 그 낱말이 나오는 장을 원본에서 찾아 표로 쓴다.

    python docs/manual/tools/index_md.py

낱말 목록(TERMS)은 사람이 고른다. 장 번호는 파일 이름의 앞 두 자리(00 은 「들어가며」).
부록 표(25 질문 예시, 36 낱말 풀이, 37 자신)는 찾지 않는다 — 거의 모든 낱말이 나와 쓸모가 없다.
"""

from __future__ import annotations

import re
from pathlib import Path

USER = Path(__file__).resolve().parents[1] / "user"
OUT = USER / "37-index.md"
SKIP = {"25", "36", "37"}

TERMS = [
    "감사 로그", "객체 타입", "거부", "게시", "게시 전 검사", "게이트", "결과 0건", "골든셋", "관계 저장소", "권한 저장",
    "그 중", "그래프 투영", "근거 카드", "근거 칩", "기대 답변", "내 요청", "내보내기", "다시 사용", "대사", "데이터 소스",
    "동기화", "등급", "링크 타입", "마지막 동기화", "막힘", "매핑", "모두 읽음", "미리보기", "버전 비교", "보관됨",
    "불변식", "사전 조건", "새 대화", "속성 추가", "승인 대기", "승인하고 실행", "시크릿 참조", "알림 연락처", "알림함",
    "역할별 읽기 권한", "열 불러오기", "요청 거두기", "워커", "원천 값", "이 버전으로 새 draft", "인터뷰", "임포트",
    "장애 주입", "전체 기록", "접속 시험", "정정", "정지", "조회 조건", "조회 실패", "질문 해석 실패", "지금 동기화",
    "증분", "추정", "트레이스", "편집 레이어", "편집 충돌", "편집 해제", "편집됨", "평가 실행", "필수 값", "확인함",
    "롤백", "로그아웃", "로컬 계정 추가", "draft", "enum", "pk", "FK 속성", "PII", "api_name", "SSO", "Action 추가",
]


def chapter_no(p: Path) -> str:
    return p.name[:2]


def main() -> None:
    files = sorted(p for p in USER.glob("*.md") if re.match(r"^\d\d", p.name) and p.name[:2] not in SKIP)
    titles = {
        chapter_no(p): re.sub(r"^\d+장\s*", "", p.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip())
        for p in files
    }
    texts = {chapter_no(p): p.read_text(encoding="utf-8") for p in files}
    rows = []
    for term in sorted(TERMS, key=lambda t: (not re.match(r"[가-힣]", t), t)):
        hits = []
        for no, text in texts.items():
            n = text.count(term)
            if n:
                hits.append((n, no))
        # 많이 나오는 장부터 다섯 곳까지, 장 번호 순으로 보인다.
        top = sorted(no for _, no in sorted(hits, reverse=True)[:5])
        rows.append((term, ", ".join("들어가며" if no == "00" else f"{int(no)}장" for no in top) or "—"))
    lines = [
        "# 37장 찾아보기",
        "",
        "낱말이 많이 나오는 장을 다섯 곳까지 적었습니다(장 번호 순). 뜻은 「36장 낱말 풀이」, 화면 문구는 「28장 화면 문구",
        "사전」을 보세요.",
        "",
        "| 낱말 | 나오는 장 |",
        "| --- | --- |",
        *[f"| {t} | {c} |" for t, c in rows],
        "",
        "## 장 목록",
        "",
        "| 장 | 제목 |",
        "| --- | --- |",
        *[f"| {'들어가며' if no == '00' else f'{int(no)}장'} | {t} |" for no, t in sorted(titles.items())],
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("→", OUT, len(rows), "낱말")


if __name__ == "__main__":
    main()
