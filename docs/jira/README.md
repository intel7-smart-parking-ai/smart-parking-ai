# Jira 신규 Task (11/04)

- `JIRA_TASKS_1104.md`: EPIC별 Task 설명 (붙여넣기용)
- `jira_import_1104.csv`: Jira **CSV 가져오기**용 (20건, UTF-8)

## CSV 가져오기 방법
1. Jira → 프로젝트 KAN → 설정(⚙) → **시스템 → 외부 시스템 가져오기 → CSV** (또는 이슈 화면 우측 상단 ··· → 이슈 가져오기)
2. 파일 선택, **인코딩 UTF-8**, 구분자 쉼표
3. 필드 연결
   - Summary → 요약, Issue Type → 이슈 유형, Description → 설명, Priority → 우선순위, Due Date → 기한(형식 `yyyy-MM-dd`), Labels → 레이블
   - **Assignee Name (참고)**: Jira 사용자 이름과 다르므로 **연결하지 말고** 가져온 뒤 직접 지정 (대표 1명 + 공동은 설명/필드)
   - **Status**: 프로젝트 설정에 따라 가져오기에서 지정이 안 될 수 있음 → 안 되면 가져온 뒤 Done/In Review로 이동
   - EPIC 연결: Labels(`EPIC-04` 등)로 묶은 뒤 각 EPIC에 상위 항목 지정
4. 미리보기로 20건 확인 후 가져오기
