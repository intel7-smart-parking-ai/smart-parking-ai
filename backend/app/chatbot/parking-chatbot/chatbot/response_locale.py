"""Translate presentation strings after the engine has fixed facts and actions."""
from collections import Counter, OrderedDict
from pathlib import Path
from copy import deepcopy
from functools import lru_cache
import json
import re
import threading
import time
import urllib.request

from . import config, prompts
from .transport import load_json

LANGUAGES = {"en": "English", "zh-CN": "Simplified Chinese", "ja": "Japanese"}
LOT_NAMES = {
    "개발원 주차장": ("Development Center Parking", "开发院停车场", "開発院駐車場"),
    "개발원주차장": ("Development Center Parking", "开发院停车场", "開発院駐車場"),
    "부산역 주차장": ("Busan Station Parking", "釜山站停车场", "釜山駅駐車場"),
    "부산역주차장": ("Busan Station Parking", "釜山站停车场", "釜山駅駐車場"),
    "김해공항 주차장": ("Gimhae Airport Parking", "金海机场停车场", "金海空港駐車場"),
    "신세계 백화점 주차장": ("Shinsegae Department Store Parking", "新世界百货停车场", "新世界百貨店駐車場"),
    "신세계백화점주차장": ("Shinsegae Department Store Parking", "新世界百货停车场", "新世界百貨店駐車場"),
}
_PROTECTED = re.compile("|".join(re.escape(name) for name in sorted(LOT_NAMES, key=len, reverse=True))
                        + r"|\d{2,3}[가-힣]\s?\d{4}|[A-D]-\d{2}"
                        + r"|(?<=[뒤끝앞]\s)\d+(?=\s*자리)|(?<=[뒤끝앞])\d+(?=\s*자리)|\d+(?=\s*자리\s*(?:숫자|수|번호))"
                        + r"|\d+(?:[.,]\d+)*\s*(?:자리|칸|면)|\d+(?:[.,]\d+)*\s*원|\d+(?:[.,:]\d+)*")
_TOKEN = re.compile(r"__(?:NAME|NUMBER|AMOUNT|COUNT|PLATE|SPACE)\d+__")
# 모델이 자리표시를 고쳐 쓰는 경우(11/11 실측: '__NUMBER4__' → '%NUMBER4__', '__NUMBER0__' → '__PLATE0__').
# 한 문장 안에서 번호는 하나뿐이라, 종류 이름·밑줄이 달라져도 번호가 같으면 같은 자리표시다.
_LOOSE_TOKEN = re.compile(r"[_%＿]{1,3}\s?(?:NAME|NUMBER|AMOUNT|COUNT|PLATE|SPACE)\s?(\d+)\s?[_%＿]{1,3}")
# 동시에 도는 답 번역 수(config.TRANSLATE_MAX_INFLIGHT). 자리가 없으면 바로 포기하지 않고 잠깐 기다린다
# (봇 점검 11/11: 동시 4명에서 외국어 답의 26~36%가 한국어).
_slot_lock = threading.Lock()
_slot_state = {"size": 0, "slots": None}
_SLOT_WAIT_SEC = 3.0
_ATTEMPT_SEC = 6.0      # 번역 호출 한 번의 최대 시간. 멈춘 호출을 끝까지 기다리기보다 한 번 더 부른다.
_RETRY_MIN_SEC = 2.5    # 다시 부르려면 남아 있어야 하는 시간


def _slots():
    with _slot_lock:
        if _slot_state["slots"] is None or _slot_state["size"] != config.TRANSLATE_MAX_INFLIGHT:
            _slot_state.update(size=config.TRANSLATE_MAX_INFLIGHT,
                               slots=threading.BoundedSemaphore(config.TRANSLATE_MAX_INFLIGHT))
        return _slot_state["slots"]


# 번역이 한국어 수사를 숫자로 옮기는 것은 사실을 더한 것이 아니다('하루 최대' → '1日最大', '두 자리 숫자' → '2桁').
_NUMBER_WORDS = (
    (re.compile(r"하루|일일|한\s*(?:번|시간|대|곳|가지|칸|자리|달|분|개|명)|첫"), "1"),
    (re.compile(r"이틀|두\s*(?:번|시간|대|곳|가지|칸|자리|달|분|개|명)"), "2"),
    (re.compile(r"사흘|세\s*(?:번|시간|대|곳|가지|칸|자리|달|분|개|명)"), "3"),
)


def resolve_language(language, text=None):
    if language != "ko" or not text or re.search(r"[가-힣]", text):
        return language
    if re.search(r"[ぁ-んァ-ヶ]", text):
        return "ja"
    if re.search(r"[一-鿿]", text):
        return "zh-CN"
    if re.search(r"[A-Za-z]{2,}", text):
        return "en"
    return "ko"


def _mask(text, language):
    replacements = {}
    index = list(LANGUAGES).index(language)
    def replace(match):
        value = match.group()
        kind = "NUMBER"
        replacement = value
        if value in LOT_NAMES:
            kind, replacement = "NAME", LOT_NAMES[value][index]
        elif re.search(r"(?:자리|칸|면)$", value):
            kind = "COUNT"
            count = re.sub(r"\s*(?:자리|칸|면)$", "", value)
            replacement = (count + " spaces", count + "个车位", count + "台")[index]
        elif value.endswith("원"):
            kind = "AMOUNT"
            amount = value[:-1].strip()
            replacement = ("KRW " + amount, amount + "韩元", amount + "ウォン")[index]
        elif re.fullmatch(r"[A-D]-\d{2}", value):
            kind = "SPACE"
        elif re.search(r"[가-힣]", value):
            kind = "PLATE"
        token = f"__{kind}{len(replacements)}__"
        replacements[token] = replacement
        return token
    return _PROTECTED.sub(replace, text), replacements


def _digits(text):
    """글에 나온 숫자들. 전각(２)·앞자리 0은 같은 수로 본다(일본어 번역의 '２桁'가 숫자 추가로 걸렸다)."""
    return {str(int(found)) for found in re.findall(r"\d+", text)}


def _canonical(text, replacements):
    """모델이 고쳐 쓴 자리표시('%NUMBER4__', '__PLATE0__')를 원래 이름으로 되돌린다. 기준은 번호다."""
    by_number = {re.search(r"\d+", token)[0]: token for token in replacements}
    return _LOOSE_TOKEN.sub(lambda m: by_number.get(m[1], m[0]), text)


def _restore(text, masked, replacements):
    if not isinstance(text, str):
        raise ValueError("Translation changed protected facts")
    text = _canonical(text, replacements)
    if Counter(_TOKEN.findall(text)) != Counter(_TOKEN.findall(masked)):
        raise ValueError("Translation changed protected facts")
    added = _digits(_TOKEN.sub("", text))
    if added - {digit for pattern, digit in _NUMBER_WORDS if pattern.search(masked)}:
        raise ValueError("Translation added a number")
    if _HANGUL.search(_TOKEN.sub("", text)):
        # 한국어 낱말이 그대로 남은 번역(11/11 실측: '…拨打 119 进行 신고…', '基本料金 기준으로…') → 다시 받는다
        raise ValueError("Translation left Korean words")
    restored = _TOKEN.sub(lambda match: replacements[match.group()], text)
    # 개수 자리표시는 단위까지 넣어 돌려주는데 번역이 단위를 또 붙이는 경우(봇 점검 11/10)
    # 'N spaces vacant spaces' → 'N vacant spaces', 'N spaces spaces' → 'N spaces'(봇 점검 11/10 15:55)
    restored = re.sub(r"(\d[\d,]*) spaces?\s+((?:(?:available|empty|free|open|parking|vacant|unoccupied|remaining)\s+)*)spaces?\b",
                      lambda m: f"{m[1]} {m[2]}spaces", restored)
    # '34个车位 个空余车位' → '34个空余车位', '33个车位 个空车位' → '33个空车位'(봇 점검 11/10 16:40)
    restored = re.sub(r"(\d[\d,]*)个车位\s*个?((?:空余|空闲|空置|可用|剩余|空)?)(?:的)?车位",
                      lambda m: f"{m[1]}个{m[2]}车位", restored)
    restored = re.sub(r"(\d[\d,]*台)\s*(?:分)?台", r"\1", restored)
    restored = re.sub(r"(\d[\d,]*台)\s*(?:の)?\s*(空き)?\s*(?:台|スペース)(?=[^ぁ-んァ-ヶ一-鿿]|$|が|は|を|あ)",
                      lambda m: m[1] + ("分の空き" if m[2] else ""), restored)
    # 봇 점검 11/11(500문항): 시각의 분이 한 자리('12:2 PM'), 단수에 복수형('1 minutes'), 한자 사이 띄어쓰기('午前 11 時 58 分')
    restored = re.sub(r"(?<![\d:])(\d{1,2}):(\d)(?![\d:])", r"\1:0\2", restored)
    restored = re.sub(r"(?<![\d,.])1 (minute|hour|space|day|vehicle|car|spot)s\b", r"1 \1", restored)
    restored = re.sub(r"(?<=[぀-ヿ一-鿿]) (?=\d)|(?<=\d) (?=[぀-ヿ一-鿿])", "", restored)
    return restored


# 번역 호출 제한 시간(초). 화면 제한(약 8초) 안에 끝나도록 요청마다 남은 시간으로 줄인다.
from contextvars import ContextVar
translate_timeout: ContextVar[float] = ContextVar("translate_timeout", default=8.0)


def _translate_call(masked_strings, language, timeout):
    """모델에 한 번 묻는다. 가린 문장 목록 → 번역된 문장 목록(자리표시는 그대로)."""
    body = {
        "model": config.LLM_MODEL,
        "temperature": 0,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": prompts.render("translate_answer", language=LANGUAGES[language])},
            {"role": "user", "content": json.dumps({"strings": list(masked_strings)}, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        config.OPENROUTER_BASE_URL + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.api_key("openrouter")},
    )
    result = load_json(request, timeout)
    # 내용이 비어(None) 오면 아래 JSON 해석에서 ValueError → 원문 안내로 떨어진다(전에는 AttributeError로 500, 감사 P0)
    content = result["choices"][0]["message"]["content"] or ""
    start, end = content.find("{"), content.rfind("}")
    translated = json.loads(content[start:end + 1])["translations"]
    if not isinstance(translated, list) or len(translated) != len(masked_strings):
        raise ValueError("Incomplete translation")
    return translated


# ---- 문장 틀 기억: 숫자·이름을 가린 뒤의 문장(틀)이 같으면 번역도 같다.
# '김해공항 주차장에 62자리 있어요'와 '신세계백화점주차장에 10자리 있어요'는 틀이 같아 모델을 한 번만 부른다.
# 규칙 답·버튼 이름은 틀이 몇십 개뿐이라 대부분 여기서 끝난다 → 모델이 느린 날에도 한국어로 떨어지지 않는다.
_SEED_PATH = Path(__file__).with_name("translation_seed.json")
_shape_lock = threading.Lock()
_shapes: OrderedDict = OrderedDict()      # (언어, 틀) → 번역된 틀. 실행 중에 배운 것(오래된 것부터 버림)
_seed: dict = {}                          # 미리 번역해 둔 틀(tools/build_translation_seed.py). 버리지 않는다
_SHAPE_LIMIT = 1024
_shape_stats = {"seed": 0, "learned": 0, "model": 0}


_HANGUL = re.compile(r"[가-힣]")
_MORNING = re.compile(r"(?i)\bA\.?M\b|morning|上午|早上|凌晨|午前|朝")
_AFTERNOON = re.compile(r"(?i)\bP\.?M\b|afternoon|evening|night|下午|晚上|傍晚|午後|夜|夕方")


def _valid_shape(masked, translated):
    """기억해도 되는 번역인가: 자리표시가 같고, 숫자를 더하지 않았고, 한국어가 남지 않았고, 오전·오후를 빠뜨리지 않았다."""
    if not isinstance(masked, str) or not isinstance(translated, str) or not translated.strip():
        return False
    if Counter(_TOKEN.findall(masked)) != Counter(_TOKEN.findall(translated)):
        return False
    plain = _TOKEN.sub("", translated)
    if _HANGUL.search(plain):
        return False
    if ("오전" in masked and not _MORNING.search(plain)) or ("오후" in masked and not _AFTERNOON.search(plain)):
        return False
    return not (_digits(plain) - {digit for pattern, digit in _NUMBER_WORDS if pattern.search(masked)})


def load_seed(path=None):
    """미리 번역해 둔 틀을 읽는다. 형식이 틀렸거나 검사에 걸리는 항목은 버린다(없어도 동작한다)."""
    loaded = {}
    try:
        raw = json.loads(Path(path or _SEED_PATH).read_text(encoding="utf-8"))
        for language, rows in raw.items():
            if language in LANGUAGES and isinstance(rows, dict):
                for masked, translated in rows.items():
                    if _valid_shape(masked, translated):
                        loaded[(language, masked)] = translated
    except (OSError, ValueError, AttributeError):
        loaded = {}
    with _shape_lock:
        _seed.clear()
        _seed.update(loaded)
    return len(loaded)


def _shape_get(language, masked):
    key = (language, masked)
    with _shape_lock:
        if key in _seed:
            _shape_stats["seed"] += 1
            return _seed[key]
        if key in _shapes:
            _shapes.move_to_end(key)
            _shape_stats["learned"] += 1
            return _shapes[key]
    return None


def _shape_put(language, masked, translated):
    if not _valid_shape(masked, translated):
        return      # 이번 답으로는 내보내되, 다음에 다시 쓰지는 않는다(예: 오전·오후를 빠뜨린 번역)
    with _shape_lock:
        _shapes[(language, masked)] = translated
        while len(_shapes) > _SHAPE_LIMIT:
            _shapes.popitem(last=False)


def shape_stats():
    with _shape_lock:
        return {**_shape_stats, "seed_size": len(_seed), "learned_size": len(_shapes)}


def clear_seed_for_tests():
    """미리 번역해 둔 틀 없이 시험한다(모델 호출을 세는 테스트용). 끝나면 load_seed()로 되돌린다."""
    with _shape_lock:
        _seed.clear()


def reset_shapes_for_tests():
    with _shape_lock:
        _shapes.clear()
        for key in _shape_stats:
            _shape_stats[key] = 0


def _lines_of(strings):
    """답을 줄 단위로 나눈다. 실제 서버의 답은 끝에 '※ 시연용 데이터예요', '(카메라 확인 중: …)' 같은 줄이 붙어
    답 전체를 한 틀로 보면 미리 번역해 둔 틀과 맞지 않는다(봇 점검 11/11: 규칙 답인데도 번역 불가). 줄마다 따로 기억한다."""
    return tuple(dict.fromkeys(line for text in strings for line in text.split("\n") if line.strip()))


def _join_lines(strings, table):
    return tuple("\n".join(table[line] if line.strip() else line for line in text.split("\n")) for text in strings)


def _translate_known(strings, language):
    """모델 없이 번역할 수 있으면(모든 줄이 아는 틀) 번역을, 아니면 None을 돌려준다."""
    table = {}
    for line in _lines_of(strings):
        shape, replacements = _mask(line, language)
        known = _shape_get(language, shape)
        if known is None:
            return None
        try:
            table[line] = _restore(known, shape, replacements)
        except ValueError:
            return None
    return _join_lines(strings, table)


def _translate(strings, language):
    """문장 묶음을 줄 단위로 번역한다. 아는 틀은 모델 없이 채우고, 모르는 줄만 모델에 묻는다.
    검사에 걸린 줄만 한 번 더 받고, 멈춘 호출도 시간이 남으면 한 번 더 부른다."""
    lines = _lines_of(strings)
    return _join_lines(strings, dict(zip(lines, _translate_lines(lines, language))))


def _translate_lines(strings, language):
    masked = [_mask(text, language) for text in strings]
    done = [None] * len(strings)
    pending = []
    for i, (shape, replacements) in enumerate(masked):
        known = _shape_get(language, shape)
        if known is not None:
            try:
                done[i] = _restore(known, shape, replacements)
                continue
            except ValueError:
                pass
        pending.append(i)
    if not pending:
        return tuple(done)
    if config.LLM_PROVIDER != "openrouter" or not config.api_key("openrouter"):
        raise ValueError("Translation provider unavailable")
    deadline = time.monotonic() + translate_timeout.get()
    failure = ValueError("Translation failed")
    for attempt in (1, 2):
        left = deadline - time.monotonic()
        if attempt == 2 and left < _RETRY_MIN_SEC:
            break
        try:
            with _shape_lock:
                _shape_stats["model"] += 1
            translated = _translate_call([masked[i][0] for i in pending], language, max(0.5, min(_ATTEMPT_SEC, left)))
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            failure = exc
            continue
        still = []
        for i, text in zip(pending, translated):
            shape, replacements = masked[i]
            try:
                done[i] = _restore(text, shape, replacements)
                _shape_put(language, shape, _canonical(text, replacements))   # 검사를 통과한 틀만 기억한다
            except ValueError as exc:
                failure = exc
                still.append(i)
        pending = still
        if not pending:
            return tuple(done)
    raise failure


_DIGITS = re.compile(r"\d+")
_LOT_HINTS = ("Development Center/開発院/开发院 → 개발원주차장, Busan Station/釜山站/釜山駅 → 부산역주차장, "
              "Gimhae Airport/金海机场/金海空港 → 김해공항 주차장, Shinsegae/新世界 → 신세계백화점주차장")


# 질문 속 숫자(번호판·차량 번호·시간·금액)는 자리표시로 가려서 보낸다. 모델이 숫자를 바꾸지 못한다
# (전에는 '0475' → '475'처럼 바뀌면 번역을 통째로 버렸고, 외국어 원문이 그대로 엔진에 들어갔다. 봇 점검 11/11 P1-3)
_Q_NUMBER = re.compile(r"\d{2,3}[가-힣]\s?\d{4}|\d+(?:[.,:]\d+)*")
# 'two hours' → '2시간'처럼 수사를 숫자로 옮기는 것은 허용한다(그 밖에 새로 생긴 숫자는 거절)
_Q_NUMBER_WORDS = (
    (re.compile(r"(?i)\b(?:one|an?\s+hour|a\s+day|first)\b|一|１|ひとつ"), "1"), (re.compile(r"(?i)\btwo\b|二|两|兩|ふたつ"), "2"),
    (re.compile(r"(?i)\bthree\b|三"), "3"), (re.compile(r"(?i)\bfour\b|四"), "4"), (re.compile(r"(?i)\bfive\b|五"), "5"),
    (re.compile(r"(?i)\bsix\b|六"), "6"), (re.compile(r"(?i)\bseven\b|七"), "7"), (re.compile(r"(?i)\beight\b|八"), "8"),
    (re.compile(r"(?i)\bnine\b|九"), "9"), (re.compile(r"(?i)\bten\b|十"), "10"), (re.compile(r"(?i)\bhalf\b|半"), "30"),
    (re.compile(r"(?i)\ba\s+day\b|\b24\b|一天|一日|終日|全天"), "24"),
)
# 한 번에 최대 2.5초, 전체는 호출자가 준 시간(http_router의 QUESTION_TRANSLATE_SEC=4초) 안에서 한 번 더 부른다.
# (1.8초로 줄여 봤더니 동시 4명에서 30문항 중 5개가 시간 초과였다. 2.5초에서는 0개. 11/11 실측)
_Q_ATTEMPT_SEC = 2.5
_Q_RETRY_MIN_SEC = 1.0
# 네 자리 숫자 뒤에 단위가 오면 차량 번호가 아니다('2000 won', '1500원', '2026年')
_Q_UNIT_AFTER = re.compile(r"\s*(?:won|krw|원|₩|円|元|块|塊|minutes?|mins?|hours?|hrs?|days?|years?|分|時間|时间|小时|小時|时|時|点|點|天|日|年|月|%|"
                           r"spaces?|slots?|spots?|자리|칸|분|시간|시|일|개|대)", re.I)


def _mask_question(text):
    """숫자를 자리표시로 가린다. 번호판과 네 자리 차량 번호는 __PLATE<n>__, 그 밖의 숫자는 __NUMBER<n>__.

    전부 __NUMBER__로 가렸더니 모델이 차량 번호를 시간으로 읽었다
    (봇 점검 11/11: 'Can 1251 leave now?' → '1251분 뒤에 나가도 될까요?', 실모델 3번 중 2번).
    """
    replacements = {}

    def replace(match):
        value = match.group()
        plate = bool(re.search(r"[가-힣]", value)) or bool(
            re.fullmatch(r"\d{4}", value) and not _Q_UNIT_AFTER.match(text, match.end())
            and not re.search(r"[₩$¥]\s*$", text[:match.start()]))
        token = f"__{'PLATE' if plate else 'NUMBER'}{len(replacements)}__"
        replacements[token] = value
        return token
    return _Q_NUMBER.sub(replace, text), replacements


def _question_call(masked, language, timeout):
    body = {
        "model": config.LLM_MODEL,
        "temperature": 0,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": prompts.render("translate_question", language=LANGUAGES[language],
                                                         lot_hints=_LOT_HINTS)},
            {"role": "user", "content": json.dumps({"message": masked}, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        config.OPENROUTER_BASE_URL + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.api_key("openrouter")},
    )
    result = load_json(request, timeout)
    content = result["choices"][0]["message"]["content"] or ""   # None이면 아래에서 ValueError → 원문 그대로
    start, end = content.find("{"), content.rfind("}")
    return json.loads(content[start:end + 1])["korean"]


def _check_question(korean, masked, replacements, original):
    """번역된 질문(자리표시가 든 채)을 검사해 자리표시를 원래 이름으로 맞춘 문장을 돌려준다."""
    if not isinstance(korean, str) or not korean.strip() or len(korean) > 240:
        raise ValueError("Invalid question translation")
    korean = _canonical(korean, replacements)
    if Counter(_TOKEN.findall(korean)) != Counter(_TOKEN.findall(masked)):
        raise ValueError("Question translation changed numbers")
    added = _digits(_TOKEN.sub("", korean))
    if added - {digit for pattern, digit in _Q_NUMBER_WORDS if pattern.search(original)}:
        raise ValueError("Question translation added a number")
    return korean.strip()


def _restore_question(korean, masked, replacements, original):
    checked = _check_question(korean, masked, replacements, original)
    return _TOKEN.sub(lambda match: replacements[match.group()], checked).strip()


@lru_cache(maxsize=512)
def _question_shape(masked, language):
    """숫자를 가린 질문(틀) → 한국어 틀. 번호만 다른 같은 질문('Find car 1724'·'Find car 8928')은 모델을 한 번만 부른다."""
    replacements = {token: token for token in _TOKEN.findall(masked)}
    # 제한 시간은 호출자가 정한다(http_router의 QUESTION_TRANSLATE_SEC). 그 안에서 한 번 더 부를 수 있다.
    deadline = time.monotonic() + max(1.0, min(4.0, translate_timeout.get()))
    failure = ValueError("Question translation failed")
    for attempt in (1, 2):
        left = deadline - time.monotonic()
        if left < (_Q_RETRY_MIN_SEC if attempt == 2 else 0.3):
            break
        try:
            return _check_question(_question_call(masked, language, min(_Q_ATTEMPT_SEC, left)), masked, replacements, masked)
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            failure = exc
    raise failure       # 실패는 기억하지 않는다(다음에 다시 시도)


def _question_to_korean(text, language):
    masked, replacements = _mask_question(text)
    shape = _question_shape(masked, language)
    return _TOKEN.sub(lambda match: replacements[match.group()], shape).strip()


_question_to_korean.cache_clear = _question_shape.cache_clear     # 테스트·운영 도구가 쓰던 이름 그대로


def question_to_korean_noted(text, language):
    """(한국어 문장, 사유). 번역하지 못하면 원문과 사유(timeout·numbers·format·provider_off)를 돌려준다.
    사유는 응답 meta에 남겨 '왜 원문이 그대로 들어갔는지'를 추적한다(봇 점검 11/11: kept_original의 원인을 알 수 없었다)."""
    if language == "ko" or language not in LANGUAGES or not isinstance(text, str) or not text.strip():
        return text, None
    if config.LLM_PROVIDER != "openrouter" or not config.api_key("openrouter"):
        return text, "provider_off"
    try:
        return _question_to_korean(text.strip(), language), None
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        if isinstance(exc, TimeoutError) or type(exc).__name__ in ("timeout", "URLError"):
            return text, "timeout"
        if "number" in str(exc):
            return text, "numbers"
        return text, "format" if isinstance(exc, (ValueError, KeyError, TypeError, IndexError, AttributeError)) else "network"


def question_to_korean(text, language):
    """외국어 질문을 엔진이 이해하는 한국어 문장으로 바꾼다. 실패하면 원문 그대로(엔진의 안전한 기본 응답)."""
    return question_to_korean_noted(text, language)[0]


def localize_response(response, language):
    if language == "ko":
        return response
    result = deepcopy(response)
    index = list(LANGUAGES).index(language)
    def localize_names(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "name" and isinstance(value, str) and value in LOT_NAMES:
                    node[key] = LOT_NAMES[value][index]
                else:
                    localize_names(value)
        elif isinstance(node, list):
            for value in node:
                localize_names(value)
    localize_names(result.get("card"))
    fields = []
    def collect(node):
        if not isinstance(node, dict):
            return
        if isinstance(node.get("text"), str) and node["text"]:
            fields.append((node, "text"))
        for key in ("banner", "safety"):
            detail = node.get(key)
            if isinstance(detail, dict):
                for field in ("text", "title", "guidance", "vehicle_help"):
                    if isinstance(detail.get(field), str) and detail[field]:
                        fields.append((detail, field))
        for chip in node.get("chips") or []:
            if isinstance(chip, dict) and isinstance(chip.get("label"), str):
                fields.append((chip, "label"))
        for message in node.get("messages") or []:
            collect(message)
    collect(result)
    strings = tuple(dict.fromkeys(node[key] for node, key in fields))
    result["language"] = language
    if not strings:
        return result
    try:
        ready = _translate_known(strings, language)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        ready = None
    if ready is not None:
        # 전부 아는 틀: 모델도, 번역 자리도 필요 없다(봇 점검 11/11: 자리가 찬 동안 아는 답까지 '번역 불가'가 됐다)
        translated = dict(zip(strings, ready))
        for node, key in fields:
            node[key] = translated[node[key]]
        result["translation_status"] = "translated"
        return result
    budget = translate_timeout.get()
    started = time.monotonic()
    # 번역 자체에 쓸 시간(3초)은 남기고 자리를 기다린다.
    wait = max(0.0, min(_SLOT_WAIT_SEC, budget - 3.0))
    slots = _slots()
    acquired = slots.acquire(timeout=wait) if wait > 0 else slots.acquire(blocking=False)
    token = translate_timeout.set(max(0.5, budget - (time.monotonic() - started)))
    try:
        if not acquired:
            raise ValueError("Translation busy")
        translated = dict(zip(strings, _translate(strings, language)))
        for node, key in fields:
            node[key] = translated[node[key]]
        result["translation_status"] = "translated"
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        # Preserve the verified original, especially safety/receipt details, on failure.
        notice = {
            "en": "Translation is temporarily unavailable. The original guidance follows.",
            "zh-CN": "翻译暂时不可用，以下为原始指引。",
            "ja": "翻訳を一時的に利用できません。以下は元の案内です。",
        }[language]
        if isinstance(result.get("text"), str):
            result["text"] = notice + "\n" + result["text"]
        result["translation_status"] = "unavailable"
    finally:
        translate_timeout.reset(token)
        if acquired:
            slots.release()
    return result

load_seed()
