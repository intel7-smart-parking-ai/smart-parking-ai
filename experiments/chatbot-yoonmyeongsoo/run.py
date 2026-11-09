import uvicorn
import os
import sys

if __name__ == "__main__":
    # app 모듈 경로 추가
    sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
    print("=" * 60)
    print("PARK:ON 스마트 주차장 챗봇 서버를 시작합니다.")
    print("웹 브라우저 접속: http://localhost:8080")
    print("챗봇 API 엔드포인트: http://localhost:8080/api/v1/chat")
    print("=" * 60)
    uvicorn.run("backend.app.main:app", host="0.0.0.0", port=8080, reload=True)

