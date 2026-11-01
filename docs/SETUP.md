# Windows 개발 환경 초기 설정

## 사전 설치

- Git, Python 3.11 (권장), Node.js (Vite와 호환되는 지원 버전)
- GitHub 계정과 `intel7-smart-parking-ai` 조직 가입

## 서버 실행 (저장소 루트에서 PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --reload
```

http://127.0.0.1:8000/docs 와 http://127.0.0.1:8000/api/v1/health 확인

## 프론트 실행 (다른 PowerShell 창, 저장소 루트에서)

```powershell
cd frontend
npm install
npm run dev
```

브라우저에서 `http://127.0.0.1:5173` 접속. 개발 화면에 서버 연결 상태가 표시됩니다.

## 주의

- 초기 골격은 **시스템 전체 구현이 아니며**, health API와 임시 프론트 연결 확인만 동작합니다.
- `configs/*.example.json`은 복사 후 실측 데이터를 채워 사용합니다.
- AI 모델 학습 코드·카메라 입출력·DB·결제·챗봇은 담당자가 구현합니다.
