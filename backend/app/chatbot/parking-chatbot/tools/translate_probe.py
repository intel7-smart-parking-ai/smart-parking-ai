"""답 번역이 어디서 깨지는지 본다: 가린 입력, 모델의 원래 출력, 검사 결과(실모델).

    python tools/translate_probe.py . <키 파일> [반복 2]      # 키 값은 환경 변수로만 넘기고 출력하지 않는다
"""
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]).resolve()
from dotenv import dotenv_values  # noqa: E402

os.environ.update(OPENROUTER_API_KEY=dotenv_values(sys.argv[2]).get("OPENROUTER_API_KEY") or "", LLM_PROVIDER="openrouter",
                  MONITOR_BACKEND="off", DATA_SOURCE="mock")
os.environ.setdefault("LLM_MODEL", "google/gemma-4-26b-a4b-it")
repeat = int(sys.argv[3]) if len(sys.argv) > 3 else 2
sys.path.insert(0, str(root))
os.chdir(root)
from chatbot import config, prompts, response_locale as locale  # noqa: E402
from chatbot.transport import load_json  # noqa: E402

SENTENCES = [
    "23번 차량은 현재 개발원주차장 A-03 구역에 있어요. 오전 10시 13분에 입차하여 13분째 주차 중이에요.",
    "김해공항 주차장의 요금 체계는 처음 30분 동안 3,000원이며 이후 10분마다 800원, 하루에 최대 40,000원까지 부과돼요.",
    "가장 저렴한 주차장은 개발원주차장이에요.\n\n· 개발원주차장: 처음 30분 1,000원\n· 부산역주차장: 처음 30분 2,000원\n· 김해공항 주차장: 처음 30분 3,000원",
    "17가1724 차량은 김해공항 주차장 A-07에 있어요.\n오전 9시 59분에 들어와서 29분째 주차 중이에요.\n지금까지 요금은 3,000원이에요.",
    "44번 차량은 현재 부산역주차장 A-01 구역에 주차 중이에요.\n오전 9시 17분에 입차하여 1시간 11분 동안 주차하셨고, 현재 요금은 5,500원이에요.",
]
outcome = Counter()
for language in ("en", "zh-CN", "ja"):
    for sentence in SENTENCES:
        masked, replacements = locale._mask(sentence, language)
        for _ in range(repeat):
            body = {"model": config.LLM_MODEL, "temperature": 0, "max_tokens": 2000, "messages": [
                {"role": "system", "content": prompts.render("translate_answer", language=locale.LANGUAGES[language])},
                {"role": "user", "content": json.dumps({"strings": [masked]}, ensure_ascii=False)}]}
            request = urllib.request.Request(
                config.OPENROUTER_BASE_URL + "/chat/completions", data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.api_key("openrouter")})
            try:
                content = load_json(request, 8.0)["choices"][0]["message"]["content"] or ""
                raw = json.loads(content[content.find("{"):content.rfind("}") + 1])["translations"][0]
            except Exception as exc:
                outcome[(language, "호출·형식 실패")] += 1
                print(f"[{language}] 호출·형식 실패 {type(exc).__name__}")
                continue
            try:
                locale._restore(raw, masked, replacements)
                outcome[(language, "통과")] += 1
            except ValueError as exc:
                outcome[(language, str(exc))] += 1
                want, got = Counter(locale._TOKEN.findall(masked)), Counter(locale._TOKEN.findall(raw))
                print(f"\n[{language}] {exc}\n  가린 입력: {masked!r}\n  모델 출력: {raw!r}\n  빠진 자리표시: {dict(want - got)} · 더 쓴 자리표시: {dict(got - want)}")
print("\n요약:", {f"{k[0]} {k[1]}": v for k, v in sorted(outcome.items())})
