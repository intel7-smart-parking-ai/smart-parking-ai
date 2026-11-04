"""환경 설정. .env 파일 또는 환경변수로 바꿀 수 있음 (외부 라이브러리 없이 직접 읽음)."""
import os
import math
import json
from datetime import timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .diagnostics import logger

KST = timezone(timedelta(hours=9))


def _read_dotenv():
    path = Path(__file__).resolve().parent.parent / ".env"
    values = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values.setdefault(key.strip(), value.strip())
    return values


_dotenv = _read_dotenv()


def getenv(key, default=None):
    """Process environment wins over the chatbot's local file and defaults."""
    return os.environ.get(key, _dotenv.get(key, default))


def _source(key):
    if key in os.environ:
        return "environment"
    return ".env" if key in _dotenv else "default"


def _number(key, default, convert):
    try:
        return convert(getenv(key, default))
    except ValueError as exc:
        raise ValueError(f"{key}: invalid numeric value") from exc


# 제공자별 API 키 변수 이름. openrouter는 DECISIONS D-18(팀 결정)의 OPENROUTER_API_KEY를 쓴다.
API_KEY_NAMES = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openrouter": "OPENROUTER_API_KEY"}
# OpenRouter는 OpenAI와 같은 요청 형식을 받는다. 주소는 고정값이며 비밀값이 아니다.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def api_key_name(provider):
    return API_KEY_NAMES.get(provider, "ANTHROPIC_API_KEY")


def api_key(provider):
    return getenv(api_key_name(provider), "")


def log_startup():
    log = logger(__name__)
    try:
        parts = urlsplit(BACKEND_BASE_URL)
        endpoint = f"{parts.scheme}://{parts.netloc.rsplit('@', 1)[-1]}"
    except ValueError:
        endpoint = "<invalid>"
    values = {
        "DATA_SOURCE": DATA_SOURCE,
        "BACKEND_BASE_URL": endpoint,
        "CHATBOT_EMBEDDED": EMBEDDED,
        "DEMO_ENABLED": DEMO_ENABLED,
        "LLM_PROVIDER": LLM_PROVIDER,
    }
    log.info("Chatbot settings: %s", " ".join(
        f"{key}={value} ({_source(key)})" for key, value in values.items()
    ))
    if EMBEDDED and str(getenv("WEB_CONCURRENCY", "")).isdigit() and int(getenv("WEB_CONCURRENCY")) > 1:
        log.warning("Chatbot sessions, alerts and reports are per process; configure one team server worker")


# mock = 가짜 데이터 / api = 팀 백엔드(API v1) 호출
# The private package loaded by the team router defaults to embedded mode.
EMBEDDED = getenv("CHATBOT_EMBEDDED", "1" if __package__.endswith("._parking_runtime") else "0") == "1"
DATA_SOURCE = getenv("DATA_SOURCE", "api" if EMBEDDED else "mock")
BACKEND_BASE_URL = getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000")

# none = LLM 없이 규칙 파서만 / anthropic / openai / openrouter
LLM_ENGINE = getenv("LLM_ENGINE", "langchain")
LLM_PROVIDER = getenv("LLM_PROVIDER", "none")
LLM_MODEL = getenv("LLM_MODEL", "")
LLM_TIMEOUT_SEC = _number("LLM_TIMEOUT_SEC", "3", float)

# billing.calculate_parking_fee_pure와 같은 목업 요금 규칙의 기본값 (API 요금은 서버 값 사용).
# 주차장별 목업 정책은 lots.json의 rate_policy가 우선한다.
BASE_MINUTES = _number("BASE_MINUTES", "30", int)
BASE_FEE = _number("BASE_FEE", "1000", int)
UNIT_MINUTES = _number("UNIT_MINUTES", "10", int)
UNIT_FEE = _number("UNIT_FEE", "500", int)
DAILY_CAP = _number("DAILY_CAP", "10000", int)
for _key in ("BASE_MINUTES", "BASE_FEE", "UNIT_MINUTES", "UNIT_FEE", "DAILY_CAP"):
    if globals()[_key] <= 0:
        raise ValueError(f"{_key}는 양수여야 합니다.")

from .catalog import load_catalog
catalog = load_catalog(Path(getenv("PARKING_SETTINGS_PATH", Path(__file__).with_name("lots.json"))))
VEHICLE_MIN, VEHICLE_MAX = catalog.settings["vehicle_min"], catalog.settings["vehicle_max"]
# 초기 설정의 호환 별칭. 실행 중 명부는 catalog를 사용한다.
LOT_IDS = catalog.ids()
LOT_CAPACITY = {k:v["capacity"] for k,v in catalog.lots().items()}
ASSIGNED_CAR_IDS = catalog.settings.get("assigned_car_ids")
DESTINATION_TTL_SEC = _number("DESTINATION_TTL_SEC", "1800", float)
QUERY_CONTEXT_TTL_SEC = _number("QUERY_CONTEXT_TTL_SEC", "300", float)
ALERT_SHARE_SEC = _number("ALERT_SHARE_SEC", "0.2", float)
REQUEST_BUDGET_SEC = _number("REQUEST_BUDGET_SEC", "6", float)
# 같은 세션·차량·사건의 관리자 호출을 하나로 합치는 시간(초). 반복 호출 접수에 적용
CALL_MERGE_SEC = _number("CALL_MERGE_SEC", "600", float)
ADMIN_TOKEN = getenv("ADMIN_TOKEN", "")
DEMO_ENABLED = getenv("DEMO_ENABLED", "1" if DATA_SOURCE == "mock" else "0") == "1"

# 단일 프로세스 테스트 모델의 유효기간·메모리 상한 (초)
CACHE_MAX_STALE_SEC = _number("CACHE_MAX_STALE_SEC", "30", float)
DATA_MAX_AGE_SEC = _number("DATA_MAX_AGE_SEC", "30", float)
SESSION_IDLE_SEC = _number("SESSION_IDLE_SEC", "7200", float)
MAX_SESSIONS = _number("MAX_SESSIONS", "200", int)
BACKEND_TOKEN = getenv("BACKEND_TOKEN", "")
BACKEND_TIMEOUT_SEC = _number("BACKEND_TIMEOUT_SEC", "2", float)
# 목업 요금 견적 유효 시간(초). API 모드는 서버 quote_expires_at만 따른다.
MOCK_QUOTE_TTL_SEC = _number("MOCK_QUOTE_TTL_SEC", "300", float)
API_PATHS = json.loads((Path(__file__).parent / "api_paths.json").read_text(encoding="utf-8"))

if DATA_SOURCE not in ("mock", "api"):
    raise ValueError("DATA_SOURCE는 mock 또는 api여야 합니다.")
if min(BACKEND_TIMEOUT_SEC, REQUEST_BUDGET_SEC, LLM_TIMEOUT_SEC) <= 0 or ALERT_SHARE_SEC < 0:
    raise ValueError("외부 조회 제한 시간은 양수, 경보 공유 시간은 0 이상이어야 합니다.")

# 환경 설정 오류는 요청 중이 아니라 시작할 때 변수 이름과 함께 알린다.
for _key in ("LLM_TIMEOUT_SEC", "BACKEND_TIMEOUT_SEC", "REQUEST_BUDGET_SEC", "SESSION_IDLE_SEC",
             "DESTINATION_TTL_SEC", "QUERY_CONTEXT_TTL_SEC", "CALL_MERGE_SEC", "MOCK_QUOTE_TTL_SEC"):
    if not math.isfinite(globals()[_key]) or globals()[_key] <= 0:
        raise ValueError(f"{_key}는 유한한 양수여야 합니다.")
for _key in ("ALERT_SHARE_SEC", "CACHE_MAX_STALE_SEC", "DATA_MAX_AGE_SEC"):
    if not math.isfinite(globals()[_key]) or globals()[_key] < 0:
        raise ValueError(f"{_key}는 유한한 0 이상 값이어야 합니다.")
if MAX_SESSIONS < 1: raise ValueError("MAX_SESSIONS는 양수여야 합니다.")
if LLM_PROVIDER not in ("none","openai","anthropic","openrouter"): raise ValueError("LLM_PROVIDER 설정 오류")

if LLM_ENGINE not in ("langchain", "direct"):
    raise ValueError("LLM_ENGINE은 langchain 또는 direct여야 합니다.")

# Separate AnyIO worker capacity for requests originating in the team app.
EMBEDDED_CHAT_WORKERS = _number("EMBEDDED_CHAT_WORKERS", "4", int)
if EMBEDDED_CHAT_WORKERS < 1:
    raise ValueError("EMBEDDED_CHAT_WORKERS는 양의 정수여야 합니다.")

ADMIN_CALL_LIMIT = _number("ADMIN_CALL_LIMIT", "500", int)
INCIDENT_LIMIT = _number("INCIDENT_LIMIT", "100", int)
UNCERTAIN_PAYMENT_LIMIT = _number("UNCERTAIN_PAYMENT_LIMIT", "1000", int)
for _key in ("ADMIN_CALL_LIMIT", "INCIDENT_LIMIT", "UNCERTAIN_PAYMENT_LIMIT"):
    if globals()[_key] < 1:
        raise ValueError(f"{_key}는 양의 정수여야 합니다.")

CHAT_MAX_CONCURRENT = _number("CHAT_MAX_CONCURRENT", str(EMBEDDED_CHAT_WORKERS), int)
if CHAT_MAX_CONCURRENT < 1:
    raise ValueError("CHAT_MAX_CONCURRENT는 양의 정수여야 합니다.")

BACKEND_BREAKER_FAILS = _number("BACKEND_BREAKER_FAILS", "3", int)
BACKEND_BREAKER_OPEN_SEC = _number("BACKEND_BREAKER_OPEN_SEC", "10", float)
if BACKEND_BREAKER_FAILS < 1:
    raise ValueError("BACKEND_BREAKER_FAILS는 양의 정수여야 합니다.")
if not math.isfinite(BACKEND_BREAKER_OPEN_SEC) or BACKEND_BREAKER_OPEN_SEC <= 0:
    raise ValueError("BACKEND_BREAKER_OPEN_SEC는 유한한 양수여야 합니다.")

LLM_MAX_PER_SESSION_MIN = _number("LLM_MAX_PER_SESSION_MIN", "5", int)
LLM_MAX_PER_MIN = _number("LLM_MAX_PER_MIN", "30", int)
LLM_MAX_PER_DAY = _number("LLM_MAX_PER_DAY", "1000", int)
LLM_ROUTE_CACHE_SEC = _number("LLM_ROUTE_CACHE_SEC", "600", float)
for _key in ("LLM_MAX_PER_SESSION_MIN", "LLM_MAX_PER_MIN"):
    if globals()[_key] < 1:
        raise ValueError(f"{_key}는 양의 정수여야 합니다.")
if LLM_MAX_PER_DAY < 0:
    raise ValueError("LLM_MAX_PER_DAY는 0 이상의 정수여야 합니다.")
if not math.isfinite(LLM_ROUTE_CACHE_SEC) or LLM_ROUTE_CACHE_SEC < 0:
    raise ValueError("LLM_ROUTE_CACHE_SEC는 유한한 0 이상 값이어야 합니다.")
