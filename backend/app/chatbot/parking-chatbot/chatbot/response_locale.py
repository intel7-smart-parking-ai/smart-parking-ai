"""Translate presentation strings after the engine has fixed facts and actions."""
from collections import Counter
from copy import deepcopy
from functools import lru_cache
import json
import re
import threading
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
_slots = threading.BoundedSemaphore(2)


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
    if not isinstance(text, str) or Counter(_TOKEN.findall(text)) != Counter(_TOKEN.findall(masked)):
        raise ValueError("Translation changed protected facts")
    if re.search(r"\d", _TOKEN.sub("", text)):
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


@lru_cache(maxsize=128)
def _translate(strings, language):
    if config.LLM_PROVIDER != "openrouter" or not config.api_key("openrouter"):
        raise ValueError("Translation provider unavailable")
    masked = [_mask(text, language) for text in strings]
    body = {
        "model": config.LLM_MODEL,
        "temperature": 0,
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": prompts.render("translate_answer", language=LANGUAGES[language])},
            {"role": "user", "content": json.dumps({"strings": [item[0] for item in masked]}, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(
        config.OPENROUTER_BASE_URL + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + config.api_key("openrouter")},
    )
    result = load_json(request, max(0.5, min(8.0, translate_timeout.get())))
    content = result["choices"][0]["message"]["content"]
    start, end = content.find("{"), content.rfind("}")
    translated = json.loads(content[start:end + 1])["translations"]
    if not isinstance(translated, list) or len(translated) != len(strings):
        raise ValueError("Incomplete translation")
    return tuple(_restore(text, original, replacements)
                 for text, (original, replacements) in zip(translated, masked))


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
    result = load_json(request, 2.0)
    content = result["choices"][0]["message"]["content"]
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
    except (OSError, ValueError, KeyError, TypeError, IndexError):
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
    acquired = _slots.acquire(blocking=False)
    try:
        if not acquired:
            raise ValueError("Translation busy")
        translated = dict(zip(strings, _translate(strings, language)))
        for node, key in fields:
            node[key] = translated[node[key]]
        result["translation_status"] = "translated"
    except (OSError, ValueError, KeyError, TypeError, IndexError):
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
        if acquired:
            _slots.release()
    return result
