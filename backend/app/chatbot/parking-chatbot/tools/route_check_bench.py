"""[실험] 가로채기 확인의 판단 정확도(실모델). 지시문 예시와 겹치지 않는 문장으로 잰다.

    python tools/route_check_bench.py --env-file <키 파일> [--model anthropic/claude-haiku-5.5] [--out 결과.json]

- '뒤집어야 함'(False): 규칙이 그 처리로 잘못 가로챌 수 있는 문장(11/10 실제 오분류 포함)
- '유지해야 함'(True): 규칙이 맞게 가로챈 문장
보는 숫자: 잘못 읽은 것을 잡은 비율, 맞는 것을 잘못 뒤집은 수, 응답 시간(1.5초를 넘으면 실제로는 규칙대로 감)
"""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = [
    # (처리, 문장, 그 처리가 맞는가)
    ("set_destination", "개발원 주차장 주차요금 안내해줘", False),
    ("set_destination", "김해공항 국내선 청사로 갈려고 하는데 가장 가까운 주차면이 어디야", False),
    ("set_destination", "부산역 주차장으로 가려면 요금 얼마야?", False),
    ("set_destination", "공항 주차장 빈자리 안내해줘", False),
    ("set_destination", "신세계 주차장으로 가면 자리 있을까?", False),
    ("set_destination", "개발원 주차장 이용 방법 안내해줘", False),
    ("set_destination", "개발원 주차장으로 안내해줘", True),
    ("set_destination", "공항 주차장으로 갈게", True),
    ("set_destination", "신세계 주차장으로 가줘", True),
    ("set_destination", "부산역 주차장으로 이동할게", True),
    ("set_destination", "D로 안내해 줘", True),
    ("set_destination", "김해공항 주차장을 목적지로 해줘", True),
    ("set_destination", "B로 갈게", True),
    ("set_destination", "공항 주차장으로 길 안내 시작해줘", True),
    ("find_my_car", "부모님 모시고 백화점 갔다가 저녁에 기차 타야 하는데 아버지가 장애인 차량이야. 어디 세우는 게 나아?", False),
    ("find_my_car", "장애인 차량은 어디 주차해?", False),
    ("find_my_car", "큰 차량인데 어디가 편해?", False),
    ("find_my_car", "전기 차량 충전하려면 어디로 가야 해?", False),
    ("find_my_car", "차량 높이가 높은데 어디 들어갈 수 있어?", False),
    ("find_my_car", "내 차 어디 있어?", True),
    ("find_my_car", "제 차 위치 알려줘", True),
    ("find_my_car", "차량 어디 있는지 찾아줘", True),
    ("find_my_car", "내 차 어디 세워뒀더라?", True),
    ("find_my_car", "차 어디 주차했는지 모르겠어", True),
    ("find_my_car", "내 차 요금 얼마 나왔어?", True),
    ("find_my_car", "우리 차 몇 번 칸에 있지?", True),
    ("congestion_forecast", "지금 공항 붐벼?", False),
    ("congestion_forecast", "덜 붐비는 곳 추천해줘", False),
    ("congestion_forecast", "내일 3시에 공항 요금 얼마야?", False),
    ("congestion_forecast", "주말에 주차 요금 더 비싸?", False),
    ("congestion_forecast", "내일 오후 3시 공항 붐벼?", True),
    ("congestion_forecast", "김해공항 안 붐비는 시간대는?", True),
    ("congestion_forecast", "토요일 오전에 신세계 혼잡해?", True),
    ("congestion_forecast", "내일 아침에 가면 신세계 자리 있어?", True),
    ("congestion_forecast", "모레 저녁 6시 공항 주차장 한산할까?", True),
    ("congestion_forecast", "이번 주 금요일 낮에 백화점 주차 어렵나?", True),
    ("charging_status", "충전 요금은 얼마야?", False),
    ("charging_status", "충전 칸에 일반 차 세워도 돼?", False),
    ("charging_status", "전기차 충전 자리 남았어?", False),
    ("charging_status", "충전하는 데 얼마나 걸려?", False),
    ("charging_status", "전기차 충전 현황", True),
    ("charging_status", "공항 충전 현황 보여줘", True),
    ("charging_status", "신세계 충전기 몇 대 쓰는 중이야?", True),
    ("charging_status", "충전 다 된 차 있어?", True),
    ("charging_status", "지금 충전 가능한 충전기 있어?", True),
]
# 지시문을 다듬은 뒤 처음 보는 문장(지시문·위 목록에 없음). 점수가 부풀려지지 않았는지 따로 본다.
HOLDOUT = [
    ("set_destination", "신세계 주차장 장애인 칸 안내 좀 해줘", False),
    ("set_destination", "C로 가면 3시간에 얼마 나와?", False),
    ("find_my_car", "SUV 차량인데 어디가 넓어?", False),
    ("congestion_forecast", "오늘 공항 주차장 몇 시까지 해?", False),
    ("charging_status", "충전 구역 주차 요금도 똑같아?", False),
    ("set_destination", "A로 가자", True),
    ("set_destination", "김해공항 주차장으로 길 안내해줘", True),
    ("find_my_car", "제 차량 지금 어느 칸에 있어요?", True),
    ("congestion_forecast", "일요일 점심때 신세계 많이 붐빌까?", True),
    ("charging_status", "공항 충전기 비어 있는 거 있어?", True),
]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file")
    ap.add_argument("--model")
    ap.add_argument("--out")
    args = ap.parse_args(argv)
    if args.env_file:
        from dotenv import dotenv_values
        key = dotenv_values(args.env_file).get("OPENROUTER_API_KEY")
        if key:
            os.environ.setdefault("OPENROUTER_API_KEY", key)
    os.environ.update(LLM_PROVIDER="openrouter", LLM_ENGINE="langchain", LLM_ROUTE_CHECK="1", MONITOR_BACKEND="off")
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    from chatbot import config, route_check
    print(f"모델 {config.LLM_ROUTE_CHECK_MODEL or config.LLM_MODEL} · 제한 {config.LLM_ROUTE_CHECK_TIMEOUT_SEC}초 · 문장 {len(CASES)}개")
    rows = []
    for intent, text, expected in CASES + HOLDOUT:
        started = time.perf_counter()
        try:
            verdict = route_check._ask(text, intent, 6.0)
        except Exception as exc:
            verdict = f"error:{type(exc).__name__}"
        ms = round((time.perf_counter() - started) * 1000)
        rows.append({"intent": intent, "text": text, "expected": expected, "verdict": verdict, "ms": ms,
                     "holdout": (intent, text, expected) in HOLDOUT})
    fresh = [r for r in rows if r["holdout"]]
    print(f"처음 보는 문장 {len(fresh)}개 중 맞게 판단: {sum(r['verdict'] is r['expected'] for r in fresh)}")
    for r in fresh:
        if r["verdict"] is not r["expected"]:
            print(f"   틀림: {r['intent']}: {r['text'][:50]} → {r['verdict']} (기대 {r['expected']})")
    rows_all, rows = rows, [r for r in rows if not r["holdout"]]
    should_veto = [r for r in rows if r["expected"] is False]
    should_keep = [r for r in rows if r["expected"] is True]
    caught = [r for r in should_veto if r["verdict"] is False]
    overturned = [r for r in should_keep if r["verdict"] is False]
    unsure = [r for r in rows if not isinstance(r["verdict"], bool)]
    times = [r["ms"] for r in rows]
    late = [r for r in rows if r["ms"] > config.LLM_ROUTE_CHECK_TIMEOUT_SEC * 1000]
    print(f"잘못 읽은 문장을 잡음: {len(caught)}/{len(should_veto)}")
    print(f"맞는 문장을 잘못 뒤집음: {len(overturned)}/{len(should_keep)}")
    print(f"판단 못 함(형식·오류): {len(unsure)} · 응답 시간 중앙값 {statistics.median(times):.0f}ms, 90% {sorted(times)[int(len(times) * .9)]}ms, 최대 {max(times)}ms · 제한 초과 {len(late)}개")
    # 빠른 길(모델 호출 없음): 그 처리의 말로만 된 문장은 확인을 생략한다. 잘못 읽은 문장을 생략하면 안 된다.
    fast = [r for r in rows_all if route_check.sure(r["text"], r["intent"])]
    print(f"빠른 길로 확인 생략: 맞는 문장 {sum(r['expected'] for r in fast)}/{sum(r['expected'] for r in rows_all)}"
          f" · 잘못 읽은 문장 {sum(not r['expected'] for r in fast)}/{sum(not r['expected'] for r in rows_all)}(0이어야 함)")
    for label, items in (("놓친 문장(뒤집어야 했는데 유지)", [r for r in should_veto if r["verdict"] is not False]),
                         ("잘못 뒤집은 문장", overturned)):
        if items:
            print(f"[{label}]")
            for r in items:
                print(f"   {r['intent']}: {r['text'][:50]} → {r['verdict']}")
    if args.out:
        Path(args.out).write_text(json.dumps({"model": config.LLM_ROUTE_CHECK_MODEL or config.LLM_MODEL, "rows": rows_all},
                                             ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
