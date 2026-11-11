"""Translate presentation strings after the engine has fixed facts and actions."""
from collections import Counter
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
                        + r"|\d{2,3}[가-힣]\s?\d{4}|[A-D]-\d{2}|\d+(?:[.,]\d+)*\s*(?:자리|칸|면)|\d+(?:[.,]\d+)*\s*원|\d+(?:[.,:]\d+)*")
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


def _restore(text, masked, replacements):
    if not isinstance(text, str):
        raise ValueError("Translation changed protected facts")
    by_number = {re.search(r"\d+", token)[0]: token for token in replacements}
    text = _LOOSE_TOKEN.sub(lambda m: by_number.get(m[1], m[0]), text)
    if Counter(_TOKEN.findall(text)) != Counter(_TOKEN.findall(masked)):
        raise ValueError("Translation changed protected facts")
    added = set(re.findall(r"\d+", _TOKEN.sub("", text)))
    if added - {digit for pattern, digit in _NUMBER_WORDS if pattern.search(masked)}:
        raise ValueError("Translation added a number")
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


@lru_cache(maxsize=128)
def _translate(strings, language):
    """문장 묶음을 번역한다. 검사에 걸린 문장만 한 번 더 받고, 멈춘 호출도 시간이 남으면 한 번 더 부른다."""
    if config.LLM_PROVIDER != "openrouter" or not config.api_key("openrouter"):
        raise ValueError("Translation provider unavailable")
    masked = [_mask(text, language) for text in strings]
    deadline = time.monotonic() + translate_timeout.get()
    done = [None] * len(strings)
    pending = list(range(len(strings)))
    failure = ValueError("Translation failed")
    for attempt in (1, 2):
        left = deadline - time.monotonic()
        if attempt == 2 and left < _RETRY_MIN_SEC:
            break
        try:
            translated = _translate_call([masked[i][0] for i in pending], language, max(0.5, min(_ATTEMPT_SEC, left)))
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
            failure = exc
            continue
        still = []
        for i, text in zip(pending, translated):
            try:
                done[i] = _restore(text, *masked[i])
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


@lru_cache(maxsize=256)
def _question_to_korean(text, language):
    body = {
        "model": config.LLM_MODEL,
        "temperature": 0,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": prompts.render("translate_question", language=LANGUAGES[language],
                                                         lot_hints=_LOT_HINTS)},
            {"role": "user", "content": json.dumps({"message": text}, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        config.OPENROUTER_BASE_URL + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.api_key("openrouter")},
    )
    # 제한 시간은 호출자가 정한다(http_router의 QUESTION_TRANSLATE_SEC). 전에는 2초로 고정돼 있어 그 값이 쓰이지 않았다.
    result = load_json(request, max(1.0, min(4.0, translate_timeout.get())))
    content = result["choices"][0]["message"]["content"] or ""   # None이면 아래에서 ValueError → 원문 그대로
    start, end = content.find("{"), content.rfind("}")
    korean = json.loads(content[start:end + 1])["korean"]
    if not isinstance(korean, str) or not korean.strip() or len(korean) > 200:
        raise ValueError("Invalid question translation")
    if sorted(_DIGITS.findall(korean)) != sorted(_DIGITS.findall(text)):
        raise ValueError("Question translation changed numbers")
    return korean.strip()


def question_to_korean(text, language):
    """외국어 질문을 엔진이 이해하는 한국어 문장으로 바꾼다. 실패하면 원문 그대로(엔진의 안전한 기본 응답)."""
    if language == "ko" or language not in LANGUAGES or not isinstance(text, str) or not text.strip():
        return text
    if config.LLM_PROVIDER != "openrouter" or not config.api_key("openrouter"):
        return text
    try:
        return _question_to_korean(text.strip(), language)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return text


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
