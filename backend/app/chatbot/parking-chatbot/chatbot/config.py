"""환경 설정. .env 파일 또는 환경변수로 바꿀 수 있음 (외부 라이브러리 없이 직접 읽음)."""
import os
import math
import json
from datetime import timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# mock = 가짜 데이터 / api = 팀 백엔드(API v1) 호출
DATA_SOURCE = os.getenv("DATA_SOURCE", "mock")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8000")

# none = LLM 없이 규칙 파서만 / anthropic / openai
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "none")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_TIMEOUT_SEC = float(os.getenv("LLM_TIMEOUT_SEC", "3"))

# 임시 요금 규칙 (EPIC 05 확정 전까지 사용하는 가짜 값)
FREE_MINUTES = int(os.getenv("FREE_MINUTES", "30"))
FEE_UNIT_MINUTES = int(os.getenv("FEE_UNIT_MINUTES", "10"))
FEE_PER_UNIT = int(os.getenv("FEE_PER_UNIT", "500"))

from .catalog import load_catalog
catalog = load_catalog()
VEHICLE_MIN, VEHICLE_MAX = catalog.settings["vehicle_min"], catalog.settings["vehicle_max"]
# 초기 설정의 호환 별칭. 실행 중 명부는 catalog를 사용한다.
LOT_IDS = catalog.ids()
LOT_CAPACITY = {k:v["capacity"] for k,v in catalog.lots().items()}
DESTINATION_TTL_SEC = float(os.getenv("DESTINATION_TTL_SEC", "1800"))
QUERY_CONTEXT_TTL_SEC = float(os.getenv("QUERY_CONTEXT_TTL_SEC", "300"))
ALERT_SHARE_SEC = float(os.getenv("ALERT_SHARE_SEC", "0.2"))
REQUEST_BUDGET_SEC = float(os.getenv("REQUEST_BUDGET_SEC", "6"))
# 같은 세션·차량·사건의 관리자 호출을 하나로 합치는 시간(초). 반복 호출 접수에 적용
CALL_MERGE_SEC = float(os.getenv("CALL_MERGE_SEC", "600"))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
DEMO_ENABLED = os.getenv("DEMO_ENABLED", "1" if DATA_SOURCE == "mock" else "0") == "1"

# 단일 프로세스 테스트 모델의 유효기간·메모리 상한 (초)
CACHE_MAX_STALE_SEC = float(os.getenv("CACHE_MAX_STALE_SEC", "30"))
DATA_MAX_AGE_SEC = float(os.getenv("DATA_MAX_AGE_SEC", "30"))
SESSION_IDLE_SEC = float(os.getenv("SESSION_IDLE_SEC", "7200"))
MAX_SESSIONS = int(os.getenv("MAX_SESSIONS", "200"))
BACKEND_TOKEN = os.getenv("BACKEND_TOKEN", "")
BACKEND_TIMEOUT_SEC = float(os.getenv("BACKEND_TIMEOUT_SEC", "2"))
API_PATHS = json.loads((Path(__file__).parent / "api_paths.json").read_text(encoding="utf-8"))

if DATA_SOURCE not in ("mock", "api"):
    raise ValueError("DATA_SOURCE는 mock 또는 api여야 합니다.")
if FEE_UNIT_MINUTES <= 0 or min(FREE_MINUTES, FEE_PER_UNIT, CACHE_MAX_STALE_SEC, DATA_MAX_AGE_SEC) < 0:
    raise ValueError("요금 단위는 양수, 요금·유효기간은 0 이상이어야 합니다.")
if min(BACKEND_TIMEOUT_SEC, REQUEST_BUDGET_SEC, LLM_TIMEOUT_SEC) <= 0 or ALERT_SHARE_SEC < 0:
    raise ValueError("외부 조회 제한 시간은 양수, 경보 공유 시간은 0 이상이어야 합니다.")

# 환경 설정 오류는 요청 중이 아니라 시작할 때 변수 이름과 함께 알린다.
for _key in ("LLM_TIMEOUT_SEC", "BACKEND_TIMEOUT_SEC", "REQUEST_BUDGET_SEC", "SESSION_IDLE_SEC",
             "DESTINATION_TTL_SEC", "QUERY_CONTEXT_TTL_SEC", "CALL_MERGE_SEC"):
    if not math.isfinite(globals()[_key]) or globals()[_key] <= 0:
        raise ValueError(f"{_key}는 유한한 양수여야 합니다.")
for _key in ("ALERT_SHARE_SEC", "CACHE_MAX_STALE_SEC", "DATA_MAX_AGE_SEC"):
    if not math.isfinite(globals()[_key]) or globals()[_key] < 0:
        raise ValueError(f"{_key}는 유한한 0 이상 값이어야 합니다.")
if MAX_SESSIONS < 1: raise ValueError("MAX_SESSIONS는 양수여야 합니다.")
if LLM_PROVIDER not in ("none","openai","anthropic"): raise ValueError("LLM_PROVIDER 설정 오류")
