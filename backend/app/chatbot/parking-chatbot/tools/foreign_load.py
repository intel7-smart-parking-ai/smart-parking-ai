"""외국어 질문을 동시 N명으로 보내 답이 그 언어로 번역됐는지 센다(실모델, 모의 데이터).

    python tools/foreign_load.py . <키 파일> [동시 4] [결과.json]      # 다른 체크아웃과 비교하려면 첫 인자에 그 폴더
키 값은 환경 변수로만 넘기고 출력하지 않는다.
"""
import json
import os
import sys
import threading
import time
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]).resolve()
from dotenv import dotenv_values  # noqa: E402

key = dotenv_values(sys.argv[2]).get("OPENROUTER_API_KEY")
workers = int(sys.argv[3]) if len(sys.argv) > 3 else 4
out = sys.argv[4] if len(sys.argv) > 4 else None
os.environ.update(OPENROUTER_API_KEY=key or "", LLM_PROVIDER="openrouter", LLM_ENGINE="langchain", DATA_SOURCE="mock",
                  MONITOR_BACKEND="off", LLM_MAX_PER_SESSION_MIN="60")
os.environ.setdefault("LLM_MODEL", "google/gemma-4-26b-a4b-it")
sys.path.insert(0, str(root))
os.chdir(root)
from fastapi.testclient import TestClient  # noqa: E402
from chatbot import response_locale as locale  # noqa: E402
from server_fastapi import app  # noqa: E402

QUESTIONS = {
    "en": ["Are there any free spaces at the airport?", "How much is parking at Shinsegae?", "Where is car 23?",
           "Find car 1724", "Can car 1724 leave now?", "Which lot is the cheapest?", "Is there an EV charging space?",
           "What are the opening hours of Busan Station parking?", "How much for 3 hours at the airport?",
           "Is the airport crowded now?"],
    "zh-CN": ["机场有空车位吗？", "新世界停车费多少？", "23号车在哪里？", "查找车辆1724", "哪个停车场最便宜？", "有电动车充电位吗？",
              "釜山站停车场营业时间？", "机场停3小时多少钱？", "残疾人车位有空吗？", "开发院还有车位吗？"],
    "ja": ["空港に空きはありますか？", "新世界の駐車料金はいくらですか？", "23番の車はどこですか？", "車1724を探して",
           "一番安い駐車場はどこですか？", "EV充電スペースはありますか？", "釜山駅駐車場の営業時間は？", "空港で3時間いくらですか？",
           "障害者用スペースは空いていますか？", "開発院に空きはありますか？"],
}
reasons = Counter()
original = locale._translate


def traced(strings, language):
    try:
        return original(strings, language)
    except Exception as exc:
        reasons[f"{type(exc).__name__}: {str(exc)[:40]}"] += 1
        raise


locale._translate = traced
jobs = [(lang, q) for lang, qs in QUESTIONS.items() for q in qs]
rows = [None] * len(jobs)
client = TestClient(app)
cursor = iter(range(len(jobs)))
lock = threading.Lock()


def work():
    while True:
        with lock:
            i = next(cursor, None)
        if i is None:
            return
        lang, q = jobs[i]
        started = time.monotonic()
        try:
            r = client.post("/api/v1/chat", json={"session_id": f"f{i}-{int(time.time())}", "text": q, "language": lang})
            body = r.json()
            rows[i] = {"lang": lang, "q": q, "http": r.status_code, "status": body.get("translation_status"),
                       "sec": round(time.monotonic() - started, 1), "a": (body.get("text") or "")[:160],
                       "route": (body.get("meta") or {}).get("route")}
        except Exception as exc:
            rows[i] = {"lang": lang, "q": q, "http": 0, "status": "error:" + type(exc).__name__,
                       "sec": round(time.monotonic() - started, 1), "a": "", "route": None}


threads = [threading.Thread(target=work) for _ in range(workers)]
started = time.monotonic()
for t in threads:
    t.start()
for t in threads:
    t.join()
print(f"동시 {workers}명 · {len(rows)}문항 · 전체 {time.monotonic() - started:.0f}초")
for lang in QUESTIONS:
    part = [r for r in rows if r["lang"] == lang]
    print(f"  {lang}: {dict(Counter(r['status'] for r in part))} · 평균 {sum(r['sec'] for r in part) / len(part):.1f}초 최대 {max(r['sec'] for r in part)}초")
done = sum(r["status"] == "translated" for r in rows)
print(f"번역된 답 {done}/{len(rows)} · 한국어로 나간 답 {len(rows) - done} · 번역 실패 사유 {dict(reasons)}")
for r in rows:
    if r["status"] != "translated" or "understand" in r["a"].lower() or "이해하지" in r["a"]:
        print(f"  [{r['status']}] {r['lang']} {r['q'][:34]!r} {r['sec']}초 → {r['a'][:90]!r}")
if out:
    Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
