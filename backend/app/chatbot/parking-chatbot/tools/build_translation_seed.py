"""자주 나오는 답의 '문장 틀' 번역을 미리 만들어 chatbot/translation_seed.json 에 넣는다(실모델, 모의 데이터).

    python tools/build_translation_seed.py --env-file <키 파일> [--out chatbot/translation_seed.json]

- 버튼 이름과, 규칙이 내는 대표 답(빈자리·요금·내 차·가상 차량·출차·예측·안내 문구)을 모아 언어별로 번역한다.
- 숫자·주차장 이름·칸·번호판은 자리표시로 가린 틀만 저장한다 → 숫자가 달라도 같은 번역을 쓴다.
- 검사(자리표시 보존, 숫자 추가 금지)를 통과한 틀만 저장한다. 저장된 틀은 실행 중 모델을 부르지 않는다.
- 키 값은 환경 변수로만 넘기고 출력하지 않는다. 결과 파일은 사람이 한 번 읽어 보고 커밋한다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

QUESTIONS = [
    "빈자리 알려줘", "공항 자리 있어?", "신세계 자리 있어?", "개발원 자리 있어?", "부산역 자리 있어?",
    "전기차 자리 있어?", "장애인 자리 있어?", "공항 전기차 자리 있어?",
    "요금 안내해줘", "공항 요금 얼마야?", "신세계 요금 얼마야?", "개발원 요금 얼마야?", "부산역 요금 얼마야?",
    "공항 3시간이면 얼마야?", "어디가 제일 싸?",
    "내 차 어디 있어?", "23번 차 어디 있어?", "23번 요금 얼마야?", "44번 차 어디 있어?", "99번 차 어디 있어?",
    "1724 어디 있어?", "1724 요금 얼마야?", "1724 출차해도 돼?", "1724 결제했어?", "9999 어디 있어?", "1724 60분 뒤 얼마야?",
    "출차해도 돼?", "23번 출차해도 돼?", "23번 차 결제해줘",
    "신세계로 가자", "공항 주차장으로 안내해줘", "부산역 주차장으로 안내해줘", "목적지 취소해줘",
    "전기차 충전 현황", "신세계 충전 현황 보여줘", "개발원 충전 현황",
    "내일 오후 3시 공항 붐벼?", "김해공항 3시에 혼잡하나?",
    "관리자 불러줘", "차 번호를 잊어버렸어", "예약 돼?", "할인돼?", "너 누구야?", "ㅁㄴㅇㄹ", "고마워",
    "차에서 연기 나요", "대피 어디로 해?", "경보 있어?",
]


# 사람이 읽고 고친 것(모델 번역의 어색하거나 틀린 표현). 새로 만들 때마다 다시 적용한다.
FIXES = {   # (정규식, 바꿀 말)
    "en": [(r" AM/PM\b", " AM"), (r"(__NUMBER\d+__) (AM|PM) (__NUMBER\d+__),?", r"\1:\3 \2"),
           (r"license plate ending in (__NUMBER\d+__) (__NUMBER\d+__)", r"the last \1 digits \2 of the license plate")],
    "zh-CN": [(r"于 ?AM ?(__NUMBER\d+__)", r"于上午 \1"), (r"于 ?PM ?(__NUMBER\d+__)", r"于下午 \1"), (r"行人优先", "人员安全第一"),
              (r"车顶贴纸的后两位数字", "车顶贴纸上的两位数字")],
    "ja": [(r"\bAM ?(__NUMBER\d+__)", r"午前 \1"), (r"\bPM ?(__NUMBER\d+__)", r"午後 \1"), (r"歩行者優先", "人命優先"),
           (r"車内にいる場合", "施設内にいる場合"), (r"実店舗の駐車場", "実際の駐車場"),
           (r"下2桁 ?(__NUMBER\d+__) ?を", r"下\1桁を"), (r"ステッカーの下2桁", "ステッカーの2桁")],
}
# 실제 서버에서만 붙는 줄(모의 데이터로는 나오지 않는다): 시연용 표시, 카메라 확인 중, 오후 입차
EXTRA_LINES = [
    "※ 시연용 데이터예요",
    "개발원주차장 만차 · 부산역주차장 만차 · 김해공항 주차장 26자리 · 신세계백화점주차장 41자리 (카메라 확인 중: 개발원주차장 12칸, 부산역주차장 16칸)",
    "개발원주차장 5자리 · 부산역주차장 3자리 · 김해공항 주차장 26자리 · 신세계백화점주차장 41자리",
    "개발원주차장 만차 · 부산역주차장 만차 · 김해공항 주차장 26자리 · 신세계백화점주차장 41자리",
    "개발원주차장 5자리 · 부산역주차장 만차 · 김해공항 주차장 만차 · 신세계백화점주차장 41자리",
    "오후 12시 2분에 들어와서 1분째 주차 중이에요.",
    "오후 3시 20분에 들어와서 1시간 5분째 주차 중이에요.",
    "오전 9시 5분에 들어와서 2시간 21분째 주차 중이에요.",
    "결제가 끝난 차량이에요. 출구에서 현장 안내를 따라 출차해 주세요.",
    "이전 결제는 취소된 상태예요.",
    "지금까지 요금은 3,000원이에요. 이미 결제됐어요.",
]


def apply_fixes(seed):
    import re
    changed = 0
    for language, pairs in FIXES.items():
        for masked, translated in list(seed.get(language, {}).items()):
            fixed = translated
            for wrong, right in pairs:
                fixed = re.sub(wrong, right, fixed)
            if fixed != translated:
                seed[language][masked] = fixed
                changed += 1
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file")
    ap.add_argument("--fix-only", action="store_true", help="모델을 부르지 않고 검수 교정(FIXES)만 다시 적용")
    ap.add_argument("--out", default=str(ROOT / "chatbot" / "translation_seed.json"))
    ap.add_argument("--batch", type=int, default=6)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.fix_only:                               # 이미 만든 파일에 검수 교정만 다시 적용(모델 호출 없음)
        seed = json.loads(Path(args.out).read_text(encoding="utf-8"))
        print("교정한 항목", apply_fixes(seed))
        Path(args.out).write_text(json.dumps(seed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return 0
    if not args.env_file:
        print("--env-file 이 필요합니다")
        return 1
    from dotenv import dotenv_values
    key = dotenv_values(args.env_file).get("OPENROUTER_API_KEY")
    if not key:
        print("키 없음")
        return 1
    os.environ.setdefault("OPENROUTER_API_KEY", key)
    os.environ.update(DATA_SOURCE="mock", MONITOR_BACKEND="off", LLM_ENGINE="direct")
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    os.environ["LLM_PROVIDER"] = "none"            # 답은 규칙으로만 만든다(틀이 고정된 문장만 모은다)
    from chatbot import config, engine as e, response_locale as locale

    strings = []

    def collect(response):
        for text in (response.get("text"), *(c.get("label") for c in response.get("chips") or [] if isinstance(c, dict))):
            if isinstance(text, str) and text.strip() and text not in strings:
                strings.append(text)
        for part in ("banner", "safety"):
            detail = response.get(part)
            if isinstance(detail, dict):
                for field in ("text", "title", "guidance", "vehicle_help"):
                    if isinstance(detail.get(field), str) and detail[field] and detail[field] not in strings:
                        strings.append(detail[field])

    e.reset_all()
    collect(e.handle("seed-start", event="start"))
    for i, question in enumerate(QUESTIONS):
        e.reset_all()
        collect(e.handle(f"seed{i}", text=question))
    e.reset_all()
    r = e.handle("seed-pay", text="23번 차 결제해줘")
    for _ in range(3):                              # 확인 → 견적 → 결제 완료
        collect(r)
        if not r.get("confirmation_token"):
            break
        r = e.handle("seed-pay", chip_id="confirm_yes", confirmation_token=r["confirmation_token"])
    collect(r)
    e.reset_all()
    e.data.set_alert(2, "A", 3)                     # Lv2 경보 중 안내
    for i, question in enumerate(("빈자리 알려줘", "23번 출차해도 돼?", "대피 어디로 해?")):
        collect(e.handle(f"seed-alert{i}", text=question))
    e.reset_all()
    strings.extend(line for line in EXTRA_LINES if line not in strings)
    print(f"모은 문장 {len(strings)}개")

    config.LLM_PROVIDER = "openrouter"             # 번역만 모델을 쓴다
    config.LLM_MODEL = os.environ.get("LLM_MODEL") or config.DEFAULT_OPENROUTER_MODEL
    locale.reset_shapes_for_tests()
    with locale._shape_lock:
        locale._seed.clear()                       # 이미 있는 틀에 기대지 않고 새로 만든다
    failed = {}
    for language in locale.LANGUAGES:
        token = locale.translate_timeout.set(14.0)
        try:
            for start in range(0, len(strings), args.batch):
                batch = tuple(strings[start:start + args.batch])
                try:
                    locale._translate(batch, language)
                except Exception as exc:
                    for text in batch:              # 묶음이 실패하면 한 문장씩 다시
                        try:
                            locale._translate((text,), language)
                        except Exception as inner:
                            failed.setdefault(language, []).append((text[:40], type(inner).__name__ + ": " + str(inner)[:40]))
        finally:
            locale.translate_timeout.reset(token)
    seed = {language: {} for language in locale.LANGUAGES}
    with locale._shape_lock:
        for (language, masked), translated in locale._shapes.items():
            seed[language][masked] = translated
    seed = {language: dict(sorted(rows.items())) for language, rows in seed.items()}
    print("교정한 항목", apply_fixes(seed))
    Path(args.out).write_text(json.dumps(seed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print({language: len(rows) for language, rows in seed.items()}, "→", args.out)
    for language, rows in failed.items():
        print(f"[{language}] 실패 {len(rows)}개:", rows[:6])
    return 0


if __name__ == "__main__":
    sys.exit(main())
