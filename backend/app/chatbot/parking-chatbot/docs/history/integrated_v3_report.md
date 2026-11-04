# 통합요청 v3 구현 결과 — v2.3

2026-11-02. 사용자 첨부 `EPIC06 GPT 통합요청 v3.md`의 순서 0 → R1 → R3 → A → B를 적용했다. 기존 최신 결과물 v2.2에서 이어서 작업했으며 프로젝트 변경은 parking-chatbot 안에만 했다. Windows 실행·PR·실제 모델 측정·팀 API 연결 등 제외된 단계는 진행하지 않았다.

## 0·R1·R3: 이미 반영된 기능 대조와 유지

현재 v2.2에는 v1.2 설정값 정리와 R1·R3가 이미 있다. 부록 0의 catalog 검증 블록·briefing 설비 표시 줄·test_settings_facility.py·settings_guide.md를 원문과 비교해 모두 일치함을 확인했다. `lots.json` 설비 값은 계속 모두 null이며 바꾸지 않았다.

R1의 LLM 관리자 호출 제안, 칩·규칙 즉시 접수, R3의 `llm_eval_questions.csv` 경로를 그대로 유지했다. 기준 코드에서 전체 307개와 기존 질문 237/237을 실행해 확인했다. 이미 존재하는 변경을 다시 덮어쓰거나 중복 추가하지 않았다. 검증 기록: `validation_integrated_v3/prerequisites.json`, `stage0_R1_R3_python.txt`, `stage0_R1_R3_questions.txt`.

## A: 규칙 오분류 수정

수정 전 엔진에 직접 입력했을 때 “직원분 안 오셔도 될 것 같아요”는 실제 관리자 문의 1건을 만들었고, 나머지 3문장도 지시서의 오분류와 일치했다.

| 문장 | 수정 전 | 수정 후 |
|---|---|---|
| 직원분 안 오셔도 될 것 같아요 | call_admin, 접수 1건 | cancel_action, cancel_target=admin, 새 접수 0건 |
| 돈은 이따 낼게요 | pay_fee | cancel_action, cancel_target=payment |
| 돈이 두 번 빠져나간 것 같아 | get_fee | payment_issue |
| 휠체어 마크 있는 칸 비었어? | 전체 빈자리 | get_empty_spots, zone=disabled |

`parser.py`의 기존 보호 규칙에 최소 추가했다.

- 관리자 대상은 담당자·관리자·직원·상담원과 님·분 존칭을 포함한다. 부정에 “안 오셔도”, “안 와도”, “오실 필요 없”을 추가했다.
- 결제 부정에 계산 표현과 이따(가)·나중에·다음에 내겠다는 표현을 추가했다. `cancel_target=payment`가 보존되게 했다. “지금 결제할게요”, “이따가 나갈 건데 지금 결제할게요”는 결제 요청으로 유지한다.
- 결제 장애에 이중·중복 결제와 돈·요금·주차비·금액이 두 번/2번 빠져나간다는 표현을 추가했다.
- `lots.json` disabled 별칭에 “휠체어”만 추가했다. 구역 배치·설비·면수·차량 설정은 그대로다.

리허설 CSV에 우선 위 4문장을 넣었다. 새 엔진 검사는 관리자 부정의 호출 0건, 악성 LLM 선택까지 도달하지 않음, 결제 미루기 후 이전 확인 무효화·미결제 유지, 명시적 관리자 호출 유지, 장애인 구역 카드까지 확인한다. A 단계 전체 317개, 기존 7세트+리허설 4문장 241/241이 통과한 뒤 B를 진행했다.

## B: 평가 세트 확장과 문장 이동

지시서의 추가 후보 16문장을 A 적용 전·후 규칙으로 모두 실행했다.

- 원래 LLM 평가 20문장은 문장·정답·인자·순서를 그대로 보존했다.
- 새 후보 중 15문장을 LLM 평가 세트에 추가했다.
- “아직 계산은 안 할래”는 A 적용 후 cancel_action으로 정확히 처리돼 LLM 평가에서 제외하고 리허설 CSV에 추가했다. cancel_target도 payment임을 검사한다.
- 최종 `tests/llm_eval_questions.csv`: **35문장**, 모두 규칙에서 unknown. 중복 없음.
- 최종 `tests/questions_rehearsal.csv`: **5문장**.
- 기존 회귀 질문 7세트: **237문장 그대로**. 리허설 포함 8세트 총 **242문장**.

추가된 LLM 문장:

1. 사람까지 부를 일은 아니에요
2. 누가 와야 하는 건 아니고 그냥 물어본 거예요
3. 근무하시는 분 계세요?
4. 담당하시는 분이랑 얘기할 수 있나요
5. 현장에 계신 분 좀 와 주세요
6. 계산 미리 해 둘 수 있어요?
7. 나가기 전에 정리 좀 해 주세요
8. 페이로도 돼?
9. 주차 ㄱㄴ?
10. 씨 쪽은 널널해?
11. 차단기 앙 열려요
12. 빨간 불 들어온 데 어디야
13. 안내 그만해도 돼
14. 주변에 맛집 있어?
15. 오늘 미세먼지 어때

규칙만 비교한 결과는 **5/35**다. 지시서의 원래 5/36에서 규칙이 새로 처리한 1문장을 리허설로 옮겼기 때문이다. unknown이 정답인 범위 밖 질문 5개만 맞는 구조이며, 이 세트는 규칙이 모르는 표현만 골라 만든 것이므로 전체 정확도가 아니다. 실제 LLM 하이브리드 정확도는 **미측정**이다. 키 없이 비교 도구가 정상 skip함을 확인했으며 유료 모델을 호출하지 않았다.

## 기존 테스트 변경 이유

기능 기대값과 기존 7세트의 질문·정답은 바꾸지 않았다. 새 데이터 파일 때문에 `tests/test_v2_2.py`의 평가 데이터 검사만 다음과 같이 조정했다.

1. 기존 7세트·237문장 검사는 새 rehearsal 파일을 제외하고 그대로 7·237을 기대한다. 별도 새 검사는 전체 질문 glob이 8세트·242문장인지 확인한다.
2. LLM CSV 행 수 `20 → 35`, 비교 결과 전체 수 `20 → 35`, unknown 정답 수 `3 → 5`를 바꿨다. 이는 사용자 지시로 평가 데이터가 확장돼 필요한 세 개의 수량 기대값 변경이다. 원본과 변경된 assertion 목록을 `scope.json`에 남긴다.

기존 테스트 삭제·skip 추가·기능 기대값 완화는 없다. 신규 검사 `test_integrated_v3.py` 13개와 실제 화면 검사 `browser_integrated_v3.cjs` 5개를 추가했다.

## 실제 실행 결과

환경: Linux / Python 3.12. 새 코드를 Windows·Python 3.11/3.13에서 실행했다고 주장하지 않는다.

| 단계·검사 | 결과 | 기록 |
|---|---|---|
| 0·R1·R3 기준 확인 | Python 307/307, 질문 237/237 | stage0_R1_R3_* |
| A 전용 검사 | 10/10 | A_focused.txt |
| A 전체 | Python 317/317, 질문 241/241 | A_python.txt, A_questions.txt |
| B 최종 전체 (LangChain 설치) | 320/320 | B_python.txt |
| B 최종 전체 (LangChain 미설치·FastAPI 설치) | 318 통과 + 설치 전용 2개 정상 skip, 총 320 | without_langchain.txt |
| 기존 7세트 + rehearsal 5문장 | 242/242 | B_questions.txt |
| LLM 전용 비교, LLM 꺼짐 | 규칙 5/35, 하이브리드 skip | B_llm_compare.json |
| Chromium A 동작 | 5개 통과 | browser_A.txt |
| Chromium R1 유지 | 4개 통과 | browser_R1.txt |
| 구문·변경 범위 확인 | compileall, 원본 대비 비교 | scope.json, changes.diff |

원시 파일은 모두 `docs/validation_integrated_v3/`에 있다. 신규 Python 입출력은 UTF-8을 사용한다. 기존 subprocess의 `PYTHONIOENCODING=utf-8`, `encoding='utf-8'`, `errors='replace'` 처리는 유지했다. 이번 작업의 단계별 검사에서는 실패가 없었다.

## 변경 파일과 재현

제품 코드 변경은 `chatbot/parser.py`, `chatbot/lots.json` 두 파일뿐이다. 기존 엔진·라우터·템플릿·API 경로·어댑터·브리핑·설비 검증·static 화면은 변경하지 않았다.

데이터·검사: `tests/questions_rehearsal.csv`, `tests/llm_eval_questions.csv`, `tests/test_integrated_v3.py`, `tests/test_v2_2.py`, `tests/browser_integrated_v3.cjs`.
문서: README, 본 보고서, 이전 보완 보고서 후속 기록, v2.2 기록의 이력 표시, 부록 3의 작업지침서를 그대로 옮긴 `docs/chatbot_guidelines_v2.md`.

```bash
python -m unittest discover -s tests
python tests/run_eval.py tests/questions.csv tests/questions_extended.csv tests/questions_holdout.csv tests/questions_realistic.csv tests/questions_stability.csv tests/questions_navigation_dev.csv tests/questions_navigation_holdout.csv tests/questions_rehearsal.csv
python tests/run_llm_compare.py
python tests/run_browser_checks.py browser_integrated_v3.cjs
python tests/run_v2_2_browser.py
```

브라우저 검사는 Node·Playwright·Chromium이 필요하다. 실제 LLM 측정은 키·모델을 정한 뒤 `run_llm_compare.py --compare`로 수행할 후속 작업이다.

설정값·카메라·실제 API 연결, Windows 실행, 원격 PR·병합은 이번 범위가 아니다. 결과 ZIP에는 수정된 parking-chatbot 전체를 담으며 `.bundle`, `__pycache__`, `.pyc`, `.git`, `.env`, 설치 패키지와 전송 도구는 제외한다.
