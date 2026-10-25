# OntoBot 사용자 매뉴얼

사용자 매뉴얼은 장별 원본(`docs/manual/user/*.md`)과 실제 화면 사진(`docs/manual/user/images/`)으로 되어 있고,
Word·PDF 로 묶어 배포합니다. 설치·서버 관리는 [시스템 운영자 매뉴얼](operator-manual.md)을 보세요.

## 차례

| 장 | 원본 | 대상 |
| --- | --- | --- |
| 들어가며 | [00-intro.md](user/00-intro.md) | 모두 |
| 1~3장 시작하기 | [01](user/01-understanding.md) · [02](user/02-login.md) · [03](user/03-layout.md) | 모두 |
| 4~8장 질문하고 답 읽기 | [04](user/04-first-question.md) · [05](user/05-reading-answers.md) · [06](user/06-question-types.md) · [07](user/07-followup.md) · [08](user/08-when-wrong.md) | 모두 |
| 9~13장 데이터 바꾸기(Action) | [09](user/09-action-concept.md) · [10](user/10-action-chat.md) · [11](user/11-action-screen.md) · [12](user/12-approve.md) · [13](user/13-after-action.md) | 모두·승인자 |
| 14~15장 인터뷰 워크숍 | [14](user/14-workshop.md) · [15](user/15-workshop-review.md) | 현업 전문가·모델러 |
| 16~19장 모델러 | [16](user/16-ontology.md) · [17](user/17-action-types.md) · [18](user/18-sources.md) · [19](user/19-conflicts-graph.md) | 모델러 |
| 20장 평가 | [20](user/20-eval.md) | 챗봇 빌더 |
| 21장 관리 | [21](user/21-admin.md) | 관리자 |
| 22장 업무 시나리오 · 23장 연습 문제 | [22](user/22-scenarios.md) · [23](user/23-exercises.md) | 모두 |
| 24~29장 찾아보기 | [24](user/24-answer-gallery.md) · [25](user/25-question-bank.md) · [26](user/26-faq.md) · [27](user/27-troubleshooting.md) · [28](user/28-ui-dictionary.md) · [29](user/29-roles.md) | 필요할 때 |
| 30~32장 깊이 알기 | [30](user/30-screen-reference.md) · [31](user/31-trace-reading.md) · [32](user/32-naming-experiment.md) | 관리자·모델러 |
| 33~37장 교육·업무 도구 | [33](user/33-workbook.md) · [34](user/34-quick-cards.md) · [35](user/35-forms.md) · [36](user/36-glossary.md) · [37](user/37-index.md) | 모두 |

## 고치고 다시 만들기

화면이나 운영 절차를 바꾸면 해당 장을 고치고, 화면이 바뀌었으면 사진도 다시 찍습니다. 도구는 `docs/manual/tools/`
에 있습니다.

| 순서 | 명령 | 하는 일 |
| --- | --- | --- |
| 1 | `uv run --project backend python docs/manual/tools/manual_env.py prepare` | 촬영용 깨끗한 DB(`ontobot_manual`)와 원천 DB 준비(compose.test 컨테이너) |
| 2 | (API·워커·웹을 1의 환경변수로 실행 — `manual_env.py env`) | 촬영 대상 앱 |
| 3 | `uv run --with playwright python docs/manual/tools/capture_user.py [장 …]` | 실제 화면 촬영(설치된 Edge 사용). 장은 앞 장의 상태에 기대므로 처음부터 순서대로 |
| 4 | `python docs/manual/tools/question_bank.py` → `question_notes.py` | 25장 질문 예시 모음(실제 API 결과)과 해설 |
| 5 | `python docs/manual/tools/gallery_md.py` · `index_md.py` | 24장 답 화면 모음, 37장 찾아보기 |
| 6 | `uv run --with python-docx --with markdown --with playwright --with pillow python docs/manual/tools/build_manual.py` | `dist/manual/` 에 Word(.docx)·PDF |

계정 비밀번호는 환경변수 `ONTOBOT_MANUAL_PASSWORD` 로만 넘깁니다(저장소에 적지 않음). AI 가 쓴 답 문장은 다시 찍으면
조금 달라질 수 있으므로, 다시 찍은 뒤에는 본문이 인용한 조회 조건·숫자가 사진과 맞는지 확인하세요.
