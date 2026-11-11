"""새로 넣은 부분의 오류 점검(모델 없음): 이상한 입력, 동시 호출, HTTP 경로.

    python tools/robust_check.py .
예외가 하나라도 나면 '오류'로 센다.
"""
import os
import sys
import threading
import traceback
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]).resolve()
os.environ.update(LLM_PROVIDER="none", MONITOR_BACKEND="off", DATA_SOURCE="mock")
sys.path.insert(0, str(root))
os.chdir(root)
from unittest.mock import patch  # noqa: E402

from chatbot import config, engine as e, response_locale as locale  # noqa: E402
from chatbot.parser import extract_virtual, normalize_colloquial  # noqa: E402
from chatbot.tools.read import ReadTools  # noqa: E402

errors = []


def check(label, fn):
    try:
        return fn()
    except Exception:
        errors.append((label, traceback.format_exc(limit=3)))


WEIRD = ["", " ", "0000", "9999", "1724", "17가1724", "17가 1724", "ㅂㅈㄹ", "?" * 50, "1724 " * 40, "😀🚗 1724 어딧어", "１７２４ 어디 있어？",
         "Find car", "Find car 17가1724 now!!", "我的车在哪里？", "車1724はどこ", "1724\n출차해도 돼?", "__NUMBER0__ 어디 있어?",
         "1724 출차해도 돼? 그리고 9999도", "내차 어딧어", "빈짜리", "결재", "<script>alert(1)</script> 1724", "1724; DROP TABLE cars",
         "0475 어디 있어?", "12345 어디 있어?", "172 어디 있어?", "1724.5원이야?", "2026년 11월 12일 3시에 공항 붐벼?", "a" * 200]

# 1) 이상한 입력: 파서·엔진
for text in WEIRD:
    check(f"extract_virtual {text[:15]!r}", lambda: extract_virtual(text))
    check(f"normalize {text[:15]!r}", lambda: normalize_colloquial(text))
for i, text in enumerate(WEIRD):
    e.reset_all()
    r = check(f"engine {text[:15]!r}", lambda: e.handle(f"w{i}", text=text))
    if r is not None and not isinstance(r.get("text"), str):
        errors.append((f"engine {text[:15]!r}", "응답에 text 없음"))
print(f"1) 이상한 입력 {len(WEIRD)}개 → 엔진 예외 {sum(1 for label, _ in errors if label.startswith('engine'))}")

# 2) 조회 도구: 이상한 번호
tools = ReadTools(e.data, e.current_alert, virtual_numbers=("1724", "17가1724", None, "", 1724))
for value in (None, "", 1724, "1724", "17가1724", "9999", "17가 1724", "x" * 300, "1724' OR 1=1", ["1724"], {"a": 1}):
    r = check(f"tool {str(value)[:15]!r}", lambda: tools.virtual_vehicle_info(value))
    if r is not None and not isinstance(r.get("ok"), bool):
        errors.append((f"tool {value!r}", "ok 없음"))
print("2) 조회 도구 이상한 번호 11개 점검")

# 3) 번역: 이상한 모델 출력
masked, replacements = locale._mask("23번 차량은 김해공항 주차장 A-03에 있어요. 요금은 3,000원이에요.", "en")
bad_outputs = [None, 123, "", "   ", masked, "__NUMBER0__", "__NUMBER0__ __NAME1__ __SPACE2__ __AMOUNT3__ __AMOUNT3__",
               "__NUMBER99__ " + "x" * 5000, "%%NUMBER0%% __NAME1__ __SPACE2__ __AMOUNT3__", "＿＿NUMBER0＿＿ __NAME1__ __SPACE2__ __AMOUNT3__",
               "Vehicle __PLATE0__ at __NAME1__ __SPACE2__, fee __AMOUNT3__ and 7 more"]
rejected = 0
for out in bad_outputs:
    try:
        locale._restore(out, masked, replacements)
    except ValueError:
        rejected += 1
    except Exception:
        errors.append((f"restore {str(out)[:20]!r}", traceback.format_exc(limit=2)))
print(f"3) 이상한 번역 출력 {len(bad_outputs)}개 → 거절 {rejected}, 통과 {len(bad_outputs) - rejected - sum(1 for l, _ in errors if l.startswith('restore'))}")
for payload in ({"choices": []}, {}, {"choices": [{"message": {}}]}, {"choices": [{"message": {"content": "not json"}}]},
                {"choices": [{"message": {"content": '{"translations": "x"}'}}]}, {"choices": [{"message": {"content": '{"korean": 5}'}}]}):
    with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "api_key", return_value="test-only"), \
            patch.object(config, "LLM_MODEL", "test/model"), patch.object(locale, "load_json", return_value=payload):
        locale._question_to_korean.cache_clear()
        locale.reset_shapes_for_tests()
        r = check("localize 깨진 응답", lambda: locale.localize_response({"text": "처음 보는 문장이에요 1,234원", "chips": []}, "en"))
        if r is not None and r.get("translation_status") != "unavailable":
            errors.append(("localize 깨진 응답", f"상태 {r.get('translation_status')}"))
        q = check("질문 깨진 응답", lambda: locale.question_to_korean("Find car 1724", "en"))
        if q is not None and q != "Find car 1724":
            errors.append(("질문 깨진 응답", f"원문이 아님: {q!r}"))
print("4) 깨진 모델 응답 6종 → 답 번역은 '원문 안내', 질문 번역은 '원문 그대로'")

# 4) 동시 호출: 문장 틀 기억
locale.reset_shapes_for_tests()
results = Counter()


def fake_call(strings, language, timeout):
    return ["__NAME0__ has __COUNT1__ free (model)." if "__COUNT1__" in s else "T" for s in strings]


def worker(n):
    for i in range(60):
        lot = ("김해공항 주차장", "신세계백화점주차장", "개발원주차장", "부산역주차장")[(n + i) % 4]
        try:
            out = locale._translate((f"{lot}에서 지금 {i + 1}자리 비었어요.",), ("en", "zh-CN", "ja")[i % 3])
            results["ok" if str(i + 1) in out[0] else "wrong"] += 1
        except Exception:
            results["error"] += 1


with patch.object(config, "LLM_PROVIDER", "openrouter"), patch.object(config, "api_key", return_value="test-only"), \
        patch.object(config, "LLM_MODEL", "test/model"), patch.object(locale, "_translate_call", side_effect=fake_call):
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
print(f"5) 16스레드 × 60회 번역 → {dict(results)} · 기억 {locale.shape_stats()}")
if results["wrong"] or results["error"]:
    errors.append(("동시 번역", str(dict(results))))

# 5) HTTP 경로(모델 없음): 외국어 요청이 500 없이 끝나는가, 아는 틀은 모델 없이 번역되는가
try:
    from fastapi.testclient import TestClient
    from server_fastapi import app
    locale.load_seed()
    client = TestClient(app)
    statuses = Counter()
    translated = 0
    cases = [("en", "1724"), ("en", "Find car 1724"), ("ja", "1724 出庫してもいい?"), ("zh-CN", "我的车0475在哪里？"), ("en", ""), ("ja", "空きはありますか"),
             ("en", "Can 1724 leave now?"), ("zh-CN", "1724"), ("en", "x" * 200)]
    for i, (language, text) in enumerate(cases):
        r = client.post("/api/v1/chat", json={"session_id": f"h{i}", "text": text, "language": language})
        statuses[r.status_code] += 1
        if r.status_code == 200 and r.json().get("translation_status") == "translated":
            translated += 1
    r = client.post("/api/v1/chat", json={"session_id": "chip", "chip_id": "empty_all", "language": "en"})
    statuses[r.status_code] += 1
    print(f"6) HTTP {dict(statuses)} · 모델 없이 번역된 답 {translated + (r.json().get('translation_status') == 'translated')}/{len(cases) + 1}")
    print("   예:", r.json().get("text", "")[:110].replace("\n", " / "))
    if any(code >= 500 for code in statuses):
        errors.append(("HTTP", str(dict(statuses))))
except ImportError:
    print("6) fastapi 없음 → HTTP 점검 건너뜀")

print(f"\n오류 {len(errors)}건")
for label, detail in errors[:10]:
    print("  -", label, "|", detail.strip().splitlines()[-1][:160])
