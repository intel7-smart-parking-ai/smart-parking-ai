"""
AI 스마트 주차장 — 서정범 님 LLM 챗봇 연동용 파이썬 Tool 함수 (EPIC 06 & EPIC 05).

작성자: 윤명수 (요금·모의결제·매출 담당)
연동 대상: 서정범 님 LLM 챗봇 (LangChain / OpenAI Functions / Gemini Function Calling)
"""

from __future__ import annotations
import json
from typing import Any
import urllib.parse
import urllib.request

class ChatbotBillingTools:
  def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
    self.base_url = base_url.rstrip("/")

  def get_vehicle_fee(self, session_id: str) -> dict[str, Any]:
    """차량의 실시간 주차요금과 입차시간, 주차시간을 조회합니다. 응답의 quote_id(유효 5분)로 결제해야 하며, 만료되면 다시 조회합니다."""
    url = f"{self.base_url}/api/v1/sessions/{urllib.parse.quote(session_id)}/fee"
    req = urllib.request.Request(url, headers={"User-Agent": "SmartParking-Chatbot/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
      return json.loads(resp.read().decode("utf-8"))

  def get_monthly_revenue(self, month: str = "2026-11") -> dict[str, Any]:
    """주차장별(A·B·C·D) 및 전체 월간 매출 집계 리포트(총 승인액, 환불 차감액, 순매출)를 조회합니다."""
    url = f"{self.base_url}/api/v1/reports/revenue?month={urllib.parse.quote(month)}"
    req = urllib.request.Request(url, headers={"User-Agent": "SmartParking-Chatbot/1.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
      return json.loads(resp.read().decode("utf-8"))
