// 화면 문구는 이 파일 한 곳에 모은다(TRD 15절). 서버 쪽 안내 문구의 정본은 backend chat/templates.py 다.
export const ko = {
  appTitle: "OntoBot",
  health: {
    checking: "서버 상태 확인 중…",
    ok: "서버 정상",
    down: "서버에 연결할 수 없습니다",
  },
} as const;
