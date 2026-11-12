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
              (r"车顶贴纸的后两位数字", "车顶贴纸上的两位数字"),
              (r"(__NAME\d+__) 已满(?!。)", r"\1已满")],            # 목록 줄: 조각 번역(HAND)과 같은 모양으로
    "ja": [(r"\bAM ?(__NUMBER\d+__)", r"午前 \1"), (r"\bPM ?(__NUMBER\d+__)", r"午後 \1"), (r"歩行者優先", "人命優先"),
           (r"車内にいる場合", "施設内にいる場合"), (r"実店舗の駐車場", "実際の駐車場"),
           (r"下2桁 ?(__NUMBER\d+__) ?を", r"下\1桁を"), (r"ステッカーの下2桁", "ステッカーの2桁"),
           (r"(__NAME\d+__) 満車(?!で)", r"\1は満車")],
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


# 사람이 직접 쓴 번역(영어, 중국어, 일본어). 모델이 만든 것보다 우선하고, 새로 만들 때마다 다시 넣는다.
# 아래쪽 '조각'은 코드가 만드는 목록 줄을 구분 기호(' · ', ' / ', ', ')로 나눈 것이다(response_locale._known_line).
# 주차장 상태의 조합마다 줄 모양이 달라 줄 통째로는 미리 번역할 수 없어, 조각을 번역해 두고 이어 붙인다.
HAND = {
    # ---- 카메라 확인 중인 칸이 있는 주차장(#120 후속)
    "__NAME0__은 확인된 빈자리가 없어요. (카메라 확인 중 __COUNT1__)": (
        "__NAME0__ has no confirmed vacant spaces. (__COUNT1__ being checked by camera)",
        "__NAME0__目前没有已确认的空车位。（摄像头确认中：__COUNT1__）", "__NAME0__には確認済みの空きがありません。（カメラで確認中：__COUNT1__）"),
    "__NAME0__ 충전 구역은 확인된 빈자리가 없어요. (카메라 확인 중 __COUNT1__)": (
        "The EV charging area of __NAME0__ has no confirmed vacant spaces. (__COUNT1__ being checked by camera)",
        "__NAME0__充电区目前没有已确认的空车位。（摄像头确认中：__COUNT1__）", "__NAME0__の充電エリアには確認済みの空きがありません。（カメラで確認中：__COUNT1__）"),
    "__NAME0__ 장애인 구역은 확인된 빈자리가 없어요. (카메라 확인 중 __COUNT1__)": (
        "The accessible parking area of __NAME0__ has no confirmed vacant spaces. (__COUNT1__ being checked by camera)",
        "__NAME0__无障碍车位区目前没有已确认的空车位。（摄像头确认中：__COUNT1__）", "__NAME0__の身障者用エリアには確認済みの空きがありません。（カメラで確認中：__COUNT1__）"),
    "__NAME0__에 확인된 빈자리가 없어졌어요.": (
        "__NAME0__ no longer has any confirmed vacant spaces.", "__NAME0__已没有已确认的空车位。", "__NAME0__に確認済みの空きがなくなりました。"),
    "확인된 빈자리가 있는 주차장이 없어요.": (
        "No parking lot has confirmed vacant spaces right now.", "目前没有停车场有已确认的空车位。", "現在、確認済みの空きがある駐車場はありません。"),
    # ---- 모델 번역이 어색했던 줄('124 spaces total available parking spaces in the entire parking lot')
    "전체 주차장 빈자리는 총 __COUNT0__예요.": (
        "There are __COUNT0__ available in total across all parking lots.", "所有停车场共有__COUNT0__空闲。", "全駐車場の空きは合計__COUNT0__です。"),
    "전체 주차장 충전 구역 빈자리는 총 __COUNT0__예요.": (
        "There are __COUNT0__ available in total in the EV charging areas across all parking lots.",
        "所有停车场的充电区共有__COUNT0__空闲。", "全駐車場の充電エリアの空きは合計__COUNT0__です。"),
    "전체 주차장 장애인 구역 빈자리는 총 __COUNT0__예요.": (
        "There are __COUNT0__ available in total in the accessible parking areas across all parking lots.",
        "所有停车场的无障碍车位区共有__COUNT0__空闲。", "全駐車場の身障者用エリアの空きは合計__COUNT0__です。"),
    "__NAME0__ 충전 구역에 __COUNT1__ 있어요.": (
        "There are __COUNT1__ available in the __NAME0__ EV charging area.", "__NAME0__充电区有__COUNT1__空闲。", "__NAME0__の充電エリアに__COUNT1__の空きがあります。"),
    "__NAME0__ 장애인 구역에 __COUNT1__ 있어요.": (
        "There are __COUNT1__ available in the __NAME0__ accessible parking area.", "__NAME0__无障碍车位区有__COUNT1__空闲。", "__NAME0__の身障者用エリアに__COUNT1__の空きがあります。"),
    "빈자리와 기본요금 중 더 중요한 기준으로 선택해 주세요.": (
        "Please choose based on which matters more to you: available spaces or the base fee.",
        "请根据空车位和基本费用中您更看重的一项来选择。", "空き状況と基本料金のうち、重視する方でお選びください。"),
    "이용 가능 여부와 요금이 확인된 후보 중 기본요금이 가장 낮은 곳은 __NAME0__이에요.": (
        "Among the options with confirmed availability and fees, __NAME0__ has the lowest base fee.",
        "在已确认可用情况和费用的候选中，基本费用最低的是__NAME0__。", "利用可否と料金を確認できた候補のうち、基本料金が最も安いのは__NAME0__です。"),
    "현재 이용 가능 여부와 요금을 함께 확인한 후보가 없어 추천하기 어려워요.": (
        "There are currently no options with both availability and fees confirmed, so I can't make a recommendation.",
        "目前没有同时确认了可用情况和费用的候选，难以推荐。", "現在、利用可否と料金の両方を確認できた候補がないため、おすすめできません。"),
    # ---- 조각: 전체 요약 줄('A 확인된 빈자리 없음 · B 만차 · C 62자리'), 다른 주차장 줄('A 충전 구역 8자리 / B 충전 구역 6자리')
    "__NAME0__ 만차": ("__NAME0__ Full", "__NAME0__已满", "__NAME0__は満車"),
    "__NAME0__ 확인된 빈자리 없음": ("__NAME0__ No confirmed vacancy", "__NAME0__暂无已确认空位", "__NAME0__は確認済みの空きなし"),
    "__NAME0__ 경보 중": ("__NAME0__ Alert active", "__NAME0__警报中", "__NAME0__は警報中"),
    "__NAME0__ 이용 제한": ("__NAME0__ Restricted", "__NAME0__限制使用", "__NAME0__は利用制限中"),
    "__NAME0__ 확인 불가": ("__NAME0__ Status unavailable", "__NAME0__无法确认", "__NAME0__は確認不可"),
    "__NAME0__ 충전 구역 __COUNT1__": ("__NAME0__ EV charging area __COUNT1__", "__NAME0__充电区__COUNT1__", "__NAME0__の充電エリア__COUNT1__"),
    "__NAME0__ 장애인 구역 __COUNT1__": ("__NAME0__ accessible parking area __COUNT1__", "__NAME0__无障碍车位区__COUNT1__", "__NAME0__の身障者用エリア__COUNT1__"),
    # ---- 조각: 비교 줄('이름 · 빈자리 5칸, 카메라 확인 중 2칸, 기본요금 처음 30분 1,000원')
    "빈자리 __COUNT0__": ("__COUNT0__ available", "__COUNT0__空闲", "空き__COUNT0__"),
    "충전 구역 빈자리 __COUNT0__": ("EV charging area: __COUNT0__ available", "充电区__COUNT0__空闲", "充電エリアの空き__COUNT0__"),
    "장애인 구역 빈자리 __COUNT0__": ("accessible parking area: __COUNT0__ available", "无障碍车位区__COUNT0__空闲", "身障者用エリアの空き__COUNT0__"),
    "빈자리 미확인": ("vacancy unknown", "空位未确认", "空き未確認"),
    "만차": ("full", "已满", "満車"),
    "확인된 빈자리 없음": ("no confirmed vacancy", "无已确认空位", "確認済みの空きなし"),
    "충전 구역 확인된 빈자리 없음": ("EV charging area: no confirmed vacancy", "充电区无已确认空位", "充電エリアに確認済みの空きなし"),
    "장애인 구역 확인된 빈자리 없음": ("accessible parking area: no confirmed vacancy", "无障碍车位区无已确认空位", "身障者用エリアに確認済みの空きなし"),
    "카메라 확인 중 __COUNT0__": ("__COUNT0__ being checked by camera", "摄像头确认中__COUNT0__", "カメラで確認中__COUNT0__"),
    "기본요금 처음 __NUMBER0__분 __AMOUNT1__": (
        "base fee __AMOUNT1__ for the first __NUMBER0__ minutes", "基本费用：前__NUMBER0__分钟__AMOUNT1__", "基本料金：最初の__NUMBER0__分__AMOUNT1__"),
    # ---- 요금표('주차요금 안내해줘' → 주차장마다 한 줄)
    "주차장별 요금표예요.": ("Here are the parking fees for each lot.", "以下是各停车场的收费标准。", "駐車場ごとの料金表です。"),
    "· __NAME0__: 처음 __NUMBER1__분은 __AMOUNT2__, 이후 __NUMBER3__분마다 __AMOUNT4__, 하루 최대 __AMOUNT5__": (
        "· __NAME0__: __AMOUNT2__ for the first __NUMBER1__ minutes, then __AMOUNT4__ every __NUMBER3__ minutes, up to __AMOUNT5__ per day",
        "· __NAME0__：前__NUMBER1__分钟__AMOUNT2__，之后每__NUMBER3__分钟__AMOUNT4__，每日最高__AMOUNT5__",
        "· __NAME0__：最初の__NUMBER1__分は__AMOUNT2__、以降__NUMBER3__分ごとに__AMOUNT4__、1日最大__AMOUNT5__"),
    "내 차 요금은 차량 번호와 함께 물어봐 주세요.": (
        "For your own car's fee, ask with your vehicle number.", "查询您的车辆费用时，请同时告知车辆号码。", "ご自身の車の料金は、車両番号と一緒にお尋ねください。"),
    # ---- 이용 방법(화면의 '이용 방법' 버튼)
    "이용 방법을 알려 드릴게요.": ("Here is how to use this service.", "为您介绍使用方法。", "ご利用方法をご案内します。"),
    "· 빈자리: ‘빈자리 알려줘’라고 하거나 주차장 이름으로 물어보세요.": (
        "· Vacancy: ask \"Any free spaces?\" or ask by parking lot name.",
        "· 空车位：请说“有空车位吗”，或用停车场名称询问。", "· 空き状況：「空きはありますか」と聞くか、駐車場名でお尋ねください。"),
    "· 내 차 위치와 요금: 차량 번호를 알려 주세요. 차 지붕 스티커의 두 자리 숫자예요.": (
        "· Your car's location and fee: tell me your vehicle number. It is the two-digit number on the sticker on the car roof.",
        "· 车辆位置和费用：请告知车辆号码，即车顶贴纸上的两位数字。", "· 車の位置と料金：車両番号をお知らせください。車の屋根のステッカーにある二桁の数字です。"),
    "· 김해공항·신세계 주차장 차량은 번호판 뒤 네 자리로 찾아요.": (
        "· For cars in the Gimhae Airport and Shinsegae parking lots, use the last four digits of the license plate.",
        "· 金海机场和新世界停车场的车辆请用车牌后四位查询。", "· 金海空港・新世界の駐車場の車は、ナンバープレートの下四桁で検索します。"),
    "· 결제: 요금을 확인한 뒤 ‘결제해줘’라고 하면 금액을 다시 확인하고 모의 결제해요.": (
        "· Payment: after checking the fee, say \"Pay\" and I will confirm the amount again and make a mock payment.",
        "· 支付：确认费用后说“帮我支付”，我会再次确认金额并进行模拟支付。", "· お支払い：料金を確認したあと「支払って」と言うと、金額をもう一度確認して模擬決済を行います。"),
    "· 도움이 필요하면 ‘관리자 불러줘’라고 해 주세요.": (
        "· If you need help, say \"Call the manager\".", "· 需要帮助时，请说“叫管理员”。", "· お困りのときは「管理者を呼んで」と言ってください。"),
    # ---- 실물 주차장 차량의 출차 질문('23번 출차해도 돼?')
    "출차 전에 요금을 먼저 결제해 주세요. ‘결제해줘’라고 하면 모의 결제를 진행해요.": (
        "Please pay the fee before leaving. Say \"Pay\" and I will start the mock payment.",
        "出场前请先支付费用。说“帮我支付”即可进行模拟支付。", "出庫前に料金をお支払いください。「支払って」と言うと模擬決済を行います。"),
    "결제가 끝났어요. 출구에서 현장 안내를 따라 출차해 주세요.": (
        "Payment is complete. Please follow the on-site guidance at the exit to leave.",
        "已完成支付。请在出口按现场指引出场。", "お支払いは完了しています。出口で現場の案内に従って出庫してください。"),
    # ---- 차량 번호를 묻는 답의 둘째 줄. 일본어 번역만 빠져 있어 일본어 화면에서 '번역 불가'가 났다(11/12 전수검사)
    "김해공항·신세계 주차장은 번호판 뒤 __NUMBER0__자리를 알려주세요.": (
        "For Gimhae Airport and Shinsegae parking lots, please provide the last __NUMBER0__ digits of your license plate.",
        "金海机场·新世界停车场，请输入车牌后__NUMBER0__位。", "金海空港・新世界の駐車場は、ナンバープレートの下__NUMBER0__桁をお知らせください。"),
    # ---- 무료 회차(#140): 0원인 이유
    "무료 시간(__NUMBER0__분 이내)이라 지금 나가면 __AMOUNT1__이에요.": (
        "It is within the free period (__NUMBER0__ minutes), so leaving now costs __AMOUNT1__.",
        "目前在免费时间内（__NUMBER0__分钟以内），现在出场费用为__AMOUNT1__。", "無料時間内（__NUMBER0__分以内）のため、今出庫すると__AMOUNT1__です。"),
    "무료 시간이라 지금 나가면 __AMOUNT0__이에요.": (
        "It is within the free period, so leaving now costs __AMOUNT0__.",
        "目前在免费时间内，现在出场费用为__AMOUNT0__。", "無料時間内のため、今出庫すると__AMOUNT0__です。"),
    "결제할 금액이 없어요. 출구에서 현장 안내를 따라 출차해 주세요.": (
        "There is nothing to pay. Please follow the on-site guidance at the exit to leave.",
        "没有需要支付的费用。请在出口按现场指引出场。", "お支払いいただく金額はありません。出口で現場の案内に従って出庫してください。"),
    # ---- 무료 회차 정책 안내('무료 시간 있어?', 요금표·요금 답 뒤의 한 줄). 분 수는 서버 값
    "입차 후 __NUMBER0__분 이내에 출차하면 무료예요.": (
        "Leaving within __NUMBER0__ minutes of entry is free.", "入场后__NUMBER0__分钟内出场免费。", "入庫後__NUMBER0__分以内に出庫すれば無料です。"),
    "네, 입차 후 __NUMBER0__분 이내에 출차하면 무료(__AMOUNT1__)예요. __NUMBER2__분이 지나면 기본요금부터 계산돼요.": (
        "Yes, leaving within __NUMBER0__ minutes of entry is free (__AMOUNT1__). After __NUMBER2__ minutes, the base fee applies.",
        "是的，入场后__NUMBER0__分钟内出场免费（__AMOUNT1__）。超过__NUMBER2__分钟后从基本费用开始计算。",
        "はい、入庫後__NUMBER0__分以内に出庫すれば無料（__AMOUNT1__）です。__NUMBER2__分を過ぎると基本料金から計算されます。"),
    "지금은 무료 회차 시간이 없어요. 입차하면 기본요금부터 계산돼요.": (
        "There is currently no free period. The base fee applies from entry.", "目前没有免费时间。入场后即从基本费用开始计算。", "現在、無料時間はありません。入庫すると基本料金から計算されます。"),
    "주차장마다 무료 시간이 달라요.": (
        "The free period differs by parking lot.", "各停车场的免费时间不同。", "無料時間は駐車場ごとに異なります。"),
    "· __NAME0__: 입차 후 __NUMBER1__분 이내 무료": (
        "· __NAME0__: free within __NUMBER1__ minutes of entry", "· __NAME0__：入场后__NUMBER1__分钟内免费", "· __NAME0__：入庫後__NUMBER1__分以内は無料"),
}


def apply_fixes(seed):
    import re
    changed = 0
    for masked, translations in HAND.items():
        for language, translated in zip(("en", "zh-CN", "ja"), translations):
            if seed.setdefault(language, {}).get(masked) != translated:
                seed[language][masked] = translated
                changed += 1
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
