"""Read-only integration check. Run from the chatbot folder with the team server running."""
import os
from pathlib import Path
import sys
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["DATA_SOURCE"] = "api"
os.environ["LLM_PROVIDER"] = "none"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from chatbot import config, engine as e
from chatbot.config import catalog
from chatbot.adapters import ApiAdapter
from chatbot.parser import normalize_car_id

COMPARE_QUESTION = "A 주차장 빈자리 몇 개야?"
# PM 규격 통일표의 확인 질문(그대로 유지)
PM_QUESTIONS = ["주차장 빈자리 알려줘", "7번 차 어디 있어?", "99번 차 어디 있어?",
                "27번 차 요금 얼마야?", "지금 경보 있어?"]
NOT_FOUND_CAR = "98"


def server_cars(api):
    """서버 /spaces의 car_id를 모은다. (규칙에 맞는 번호, 규칙 밖 번호)"""
    valid, invalid = [], []
    for lot in catalog.ids():
        status, body, _ = api._request("GET", config.API_PATHS["spaces"].format(lot_id=lot))
        if status != 200 or not isinstance(body, dict):
            continue
        for item in body.get("items", []):
            car = item.get("car_id")
            if car is None:
                continue
            (valid if normalize_car_id(str(car)) else invalid).append((lot, item.get("space_id"), str(car)))
    return valid, invalid


def main():
    api = e.data.inner
    if not isinstance(api, ApiAdapter):
        raise SystemExit("DATA_SOURCE=api가 필요합니다.")
    calls = []
    headers_seen = []
    original = api._request_uncached

    def recorded(method, path, body=None):
        result = original(method, path, body)
        calls.append((method, path, result[0]))
        headers_seen.append(any(k.lower()=="x-parkflow-data-source" and v=="mock" for k,v in result[2].items()))
        return result

    valid, invalid = server_cars(api)
    questions = [COMPARE_QUESTION] + PM_QUESTIONS
    if valid:
        first = valid[0][2]
        questions += [f"{first}번 차 어디 있어?", f"{first}번 차 요금 얼마야?"]
    questions += [f"{NOT_FOUND_CAR}번 차 어디 있어?"]
    rows = []
    with patch.object(api, "_request_uncached", side_effect=recorded):
        for question in questions:
            start = len(calls)
            sid = uuid.uuid4().hex
            response = e.handle(sid, text=question)
            if response["flow"] == "confirm_vehicle":
                response = e.handle(sid, chip_id="confirm_yes", confirmation_token=response["confirmation_token"])
            end = len(calls)
            question_calls = end-start
            comparison = "—"
            if question == COMPARE_QUESTION:
                status, snapshot, _ = api._request("GET", config.API_PATHS["lots"])
                server = next((x for x in snapshot.get("items", []) if x.get("lot_id") == "A"), None)
                card = response.get("card") or {}
                shown = card.get("data", {}).get("lots", {}).get("A")
                if status == 200 and server and shown:
                    actual = (shown["empty"], shown.get("uncertain", 0), shown["total"])
                    expected = (server["empty_spaces"], server.get("uncertain_spaces", 0), server["total_spaces"])
                    comparison = "일치" if actual == expected else f"불일치: 답변={actual}, 서버={expected}"
                else:
                    comparison = "비교 불가"
            paths = "; ".join(f"{method} {path} [{status or '연결 실패'}]" for method, path, status in calls[start:end])
            rows.append([question, response["text"], paths or "없음 (입력 규칙 검증 또는 30초 캐시)", question_calls, comparison])
    print("| 질문 | 답변 | 호출 경로·HTTP 상태 | HTTP 횟수(비교 조회 제외) | 서버 숫자 비교 |")
    print("| --- | --- | --- | ---: | --- |")
    for row in rows:
        print("| " + " | ".join(str(x).replace("|", "/").replace("\n", " / ") for x in row) + " |")
    print(f"\n7번처럼 한 자리 번호는 조회하지 않고 '두 자리로 말씀해 주세요'로 답합니다. {NOT_FOUND_CAR}번은 서버에 없는 번호의 404 처리 확인용입니다.")
    if valid:
        print("서버 차량(규칙 10~99): " + ", ".join(f"{catalog.name(l)} {sp} {c}번" for l, sp, c in valid[:6]))
    else:
        print("서버 /spaces에서 규칙에 맞는 차량을 찾지 못해 위치·요금 정상 경로는 확인하지 못했습니다.")
    if invalid:
        print("규칙 밖 서버 차량 번호(번호 미인식으로 처리됨, 목업 갱신 필요): " + ", ".join(f"{l} {sp} {c}" for l, sp, c in invalid[:10]))
    print("목업 응답 헤더: " + ("관측됨" if any(headers_seen) else "관측되지 않음 (연결 실패 시 판정 불가)"))
    return 1 if any(status == 0 or status >= 500 for _, _, status in calls) else 0


if __name__ == "__main__":
    raise SystemExit(main())
