# 챗봇 변경 안내

팀 레포의 루트 `CLAUDE.md`를 따른다. 실행 방식, 데이터 흐름, 잠금과 대화 상태는 [CODEMAP](docs/CODEMAP.md)을 본다.

고정 접점은 `backend.app.chatbot.router.router`, `/api/v1/chat`의 전체 HTTP 계약, 칩 ID·카드 type·flow, `static/`의 화면, 단독 서버 실행법, 설정 파일 값과 팀 API 요청 body·헤더다.

- 수정 범위는 `backend/app/chatbot/` 내부로 제한한다.
- 사용자 기능과 기존 문구는 작업지시서 범위를 따른다.
- LLM은 도구 분류만 담당한다. 관리자 호출은 LLM 경로에서 확인을 받는다.
- Lv2에서는 대피 안내가 우선이다. 결제는 차량·견적·사용자 확인을 거친다.
- 비밀 키와 `.env`, 생성 로그와 캐시를 제출물에서 제외한다.

```sh
python tests/run_guarded.py
python tests/run_browser_checks.py
```
