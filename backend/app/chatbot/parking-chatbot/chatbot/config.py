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
# 팀 결정 D-18. 별도 모델 시험에는 명시적인 LLM_MODEL 설정을 사용한다.
DEFAULT_OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it"
LLM_MODEL = getenv("LLM_MODEL", "") or (DEFAULT_OPENROUTER_MODEL if LLM_PROVIDER == "openrouter" else "")
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
# 질문 하나의 처리 시간 상한(초). 화면은 챗봇 응답을 20초까지 기다린다. 대화 AI(호출 2번, 입력 약 1.3만 토큰)가
# 6초 안에서는 22%가 시간 초과로 규칙 답이 됐고 8초에서는 사진 사례가 모두 AI 답이었다(11/09 실측).
REQUEST_BUDGET_SEC = _number("REQUEST_BUDGET_SEC", "8", float)
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

# Optional classifier examples; rehearsal behavior remains unchanged by default.
LLM_FEWSHOT = getenv("LLM_FEWSHOT", "0") == "1"
FALLBACK_HINTS = getenv("FALLBACK_HINTS", "0") == "1"

# ---------------------------------------------------------------- v3.1 대화 에이전트 예산
# 대화 에이전트는 LLM_MAX_PER_SESSION_MIN·LLM_MAX_PER_MIN·LLM_MAX_PER_DAY를 '모델 호출'이 아니라
# '질문(턴)' 단위로 센다(llm_limits.admit_turn). 질문 1개는 보통 모델 호출 2~3회다.
# 서버 전체 동시 모델 호출 자리와, 자리가 없을 때 기다리는 최대 시간(초)
LLM_MAX_INFLIGHT = _number("LLM_MAX_INFLIGHT", "4", int)
LLM_SLOT_WAIT_SEC = _number("LLM_SLOT_WAIT_SEC", "1", float)
# 조회 결과를 받은 뒤 답을 쓰는 모델 호출의 제한 시간(초). 도구 선택 호출은 LLM_TIMEOUT_SEC
LLM_ANSWER_TIMEOUT_SEC = _number("LLM_ANSWER_TIMEOUT_SEC", "5", float)
# 대화 AI 답의 출력 토큰 상한. 너무 낮으면 추천·비교 답이 잘리고, 너무 높으면 긴 목록 답으로 시간 초과가 잦다.
LLM_MAX_OUTPUT_TOKENS = _number("LLM_MAX_OUTPUT_TOKENS", "600", int)
# 근거 검사에 걸린 답을 고쳐 쓰려면 남아 있어야 하는 최소 시간(초). 고쳐 쓰기는 조회를 다시 해 답을 살리는 경로다.
LLM_REPAIR_MIN_SEC = _number("LLM_REPAIR_MIN_SEC", "1.5", float)
# 확실한 조회(말한 주차장의 빈자리, 말한 차량의 위치·요금 등)는 코드가 먼저 실행해 대화 AI에 넘긴다.
# 모델 호출이 2번(도구 선택 → 답)에서 1번으로 줄어 답이 빨라진다. AI는 필요하면 조회를 더 할 수 있다. 0이면 끔.
LLM_PREFETCH = getenv("LLM_PREFETCH", "1") == "1"
# 한 턴의 입력+출력 토큰 상한. 도구 설명·지시문이 매 호출에 다시 들어가므로 호출 수 × 약 5천을 잡는다.
LLM_TURN_TOKEN_LIMIT = _number("LLM_TURN_TOKEN_LIMIT", "40000", int)
# 근거 검사에 걸린 답을 이유와 함께 한 번 고쳐 쓰게 한다(0이면 바로 대체 답).
LLM_REPAIR = getenv("LLM_REPAIR", "1") == "1"
for _key in ("LLM_MAX_INFLIGHT", "LLM_TURN_TOKEN_LIMIT"):
    if globals()[_key] < 1:
        raise ValueError(f"{_key}는 양의 정수여야 합니다.")
for _key in ("LLM_SLOT_WAIT_SEC", "LLM_ANSWER_TIMEOUT_SEC"):
    if not math.isfinite(globals()[_key]) or globals()[_key] < 0 or (_key == "LLM_ANSWER_TIMEOUT_SEC" and globals()[_key] == 0):
        raise ValueError(f"{_key}는 유한한 양수여야 합니다.")

# ---------------------------------------------------------------- 모니터링 (Langfuse, 기본 꺼짐)
# off: 기록 안 함 / local: 턴마다 JSON 한 줄 파일 기록 / langfuse: Langfuse로 전송(설치·키 필요)
MONITOR_BACKEND = getenv("MONITOR_BACKEND", "off").strip().lower()
MONITOR_LOCAL_PATH = getenv("MONITOR_LOCAL_PATH", str(Path(__file__).resolve().parents[1] / ".monitoring" / "turns.jsonl"))
# 질문·답 원문을 기록할지(1) 길이·해시만 기록할지(0)
MONITOR_CAPTURE_TEXT = getenv("MONITOR_CAPTURE_TEXT", "1") == "1"
MONITOR_SAMPLE_RATE = _number("MONITOR_SAMPLE_RATE", "1", float)
LANGFUSE_PUBLIC_KEY = getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = getenv("LANGFUSE_HOST", "") or getenv("LANGFUSE_BASE_URL", "") or "https://cloud.langfuse.com"
if MONITOR_BACKEND not in ("off", "local", "langfuse"):
    raise ValueError("MONITOR_BACKEND는 off, local, langfuse 중 하나여야 합니다.")
if not math.isfinite(MONITOR_SAMPLE_RATE) or not 0 <= MONITOR_SAMPLE_RATE <= 1:
    raise ValueError("MONITOR_SAMPLE_RATE는 0~1이어야 합니다.")
