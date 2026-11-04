"""테스트 질문 세트 정답률 측정.

    python tests/run_eval.py

정답률 = intent·lot_id·car_id 가 모두 기대값과 같은 질문 수 ÷ 전체 질문 수
프롬프트나 규칙을 바꿀 때마다 돌려서 정답률이 떨어지지 않았는지 확인한다.
"""
import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chatbot.parser import parse  # noqa: E402

files = [Path(x) for x in sys.argv[1:]] or [Path(__file__).parent / "questions.csv"]
rows = []
for file in files:
    with file.open(encoding="utf-8") as stream:
        rows.extend(csv.DictReader(stream))
ok, fails, times, sources = 0, [], [], {}
for r in rows:
    t = time.perf_counter()
    res = parse(r["question"])
    times.append((time.perf_counter() - t) * 1000)
    sources[res.source] = sources.get(res.source, 0) + 1
    got = (res.intent, res.params.get("lot_id", ""), res.params.get("car_id", ""))
    exp = (r["intent"], r["lot_id"], r["car_id"])
    if "zone" in r:
        got += (res.params.get("zone", ""),)
        exp += (r["zone"],)
    if got == exp:
        ok += 1
    else:
        fails.append((r["question"], exp, got))

print(f"정답률: {ok}/{len(rows)} = {ok / len(rows):.1%}")
print(f"평균 처리 시간: {sum(times) / len(times):.2f} ms  (처리 방식별 건수: {sources})")
for q, exp, got in fails:
    print(f"  실패: {q!r}  기대 {exp}  결과 {got}")
sys.exit(0 if not fails else 1)
