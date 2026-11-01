# GitHub 협업 규칙

- 저장소 1개: `intel7-smart-parking-ai/smart-parking-ai` (Private).
- `main`은 통합 확인된 버전만 반영합니다. 일반 개발은 작업 브랜치에서 수행합니다.
- 권장 브랜치: `feature/KAN-작업번호-기능명`, 초기 구조는 `chore/initial-scaffold`.
- Pull Request에 Jira 링크, 변경 파일, 실행 방법, 테스트 결과를 적습니다.
- 작성자 본인을 제외한 팀원 1명 이상 리뷰. API/DB 계약 변경은 PM 및 해당 소유자 협의.
- AI가 만든 코드도 개발자가 직접 실행하여 검증합니다.
- `.env`, 실제 API 키, 학습 원본 영상, 데이터베이스, 큰 가중치는 일반 Git에 업로드하지 않습니다.
- AI 학습 팀 2명은 데이터 수집·라벨링·학습·평가·개선 전 과정을 공동 진행합니다.
- 기능 소유자는 `docs/통합_개발운영지침서.md` 기준. 공통 모듈 변경 전 Jira/Discord 공유.
