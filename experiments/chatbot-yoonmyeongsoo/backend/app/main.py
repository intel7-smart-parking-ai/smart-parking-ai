import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from .chatbot.router import router as chat_router

app = FastAPI(
    title="PARK:ON Smart Parking Chatbot API",
    description="스마트 주차장 대화형 안내 챗봇 서비스 (개발원, 부산역, 김해공항, 신세계백화점)",
    version="2.16.2"
)

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API 라우터 등록
app.include_router(chat_router)

# 프론트엔드 정적 파일 서빙
frontend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "frontend"))
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "parkon-chatbot"}

