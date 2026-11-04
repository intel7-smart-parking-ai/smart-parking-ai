"""Standalone FastAPI app. Run with uvicorn server_fastapi:app --port 8080."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from chatbot.http_router import router

app = FastAPI(title="주차장 안내 챗봇 (EPIC 06 테스트 모델)")
app.include_router(router)
app.mount("/", StaticFiles(directory=str(Path(__file__).parent / "static"), html=True), name="static")
