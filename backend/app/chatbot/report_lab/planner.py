import json
import os
import re
from urllib.request import Request, urlopen

TASKS = ("revenue", "alerts", "spaces", "fees", "vehicle", "policies")
PROMPT_VERSION = "planner-v1"
PROMPT_NAME = "parkon-report-planner"
PROMPT = """주차장 운영 보고서의 작업 분류기입니다. 질문에 답하거나 수치를 생성하지 마세요.
오직 {"tasks": [...]} JSON 객체를 반환하세요. 배열 값은 다음 여섯 종류만 허용합니다.
revenue: 오늘 매출, 수익, 결제 집계 조회.
alerts: 현재 경보, 화재 경보, 알림 현황 조회.
spaces: 현재 빈자리, 남은 주차 자리 조회.
fees: 기본 요금, 요금표, 요금 정책 조회.
vehicle: 두 자리 스티커 번호 또는 뒤 네 자리 번호의 차량 위치·입차 세션 조회.
policies: 운영 시간·시설·할인·예약·환불·장기 주차 등 확인된 이용 안내 조회.
개별 차량의 현재 요금·결제 상태 질문은 vehicle과 fees를 함께 선택하세요. 내 차인데 번호가 없어도
vehicle과 fees를 선택하면 담당이 번호를 되묻습니다. 차량 위치만 질문하면 vehicle만 선택합니다.
언급된 항목만 선택하세요. 일반적인 단일 조회 요청도 지원합니다. API 호출 가능 여부를 추측하지 마세요.
모르는 요청, 날짜가 다른 매출·빈자리 예측, 결제나 취소 실행은 {"tasks": []}로 반환하세요.
예시: "매출만 부탁해" -> {"tasks": ["revenue"]}
"알림 상태 부탁해" -> {"tasks": ["alerts"]}
"남은 주차 자리랑 기본 요금" -> {"tasks": ["spaces", "fees"]}
"4268번 차 위치랑 주차비" -> {"tasks": ["fees", "vehicle"]}
"공항 운영 시간이랑 전기차 빈자리" -> {"tasks": ["spaces", "policies"]}
"식당 추천" -> {"tasks": []}
순서는 revenue, alerts, spaces, fees, vehicle, policies이며 중복은 제외하세요."""


def plan(question, system_prompt=PROMPT, *, use_llm=True):
    if re.search(r"어제|내일|지난|다음|결제해|취소해", question):
        return []
    key = os.getenv("REPORT_OPENROUTER_KEY")
    if key and use_llm:
        body = {"model": os.environ["REPORT_MODEL"], "temperature": 0,
                "provider": {"require_parameters": True},
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "parking_report_tasks", "strict": True,
                    "schema": {"type": "object", "properties": {"tasks": {
                        "type": "array", "items": {"type": "string", "enum": list(TASKS)}}},
                        "required": ["tasks"], "additionalProperties": False}}},
                "messages": [{"role": "system", "content": system_prompt},
                             {"role": "user", "content": question}]}
        request = Request("https://openrouter.ai/api/v1/chat/completions",
                          data=json.dumps(body).encode(),
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urlopen(request, timeout=20) as response:
            choices = json.load(response)["choices"]
        content = choices[0]["message"]["content"].strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL | re.IGNORECASE)
        selected = json.loads(fenced.group(1) if fenced else content)
        if isinstance(selected, dict) and set(selected) == {"tasks"}:
            selected = selected["tasks"]
        if not isinstance(selected, list) or any(task not in TASKS for task in selected):
            raise ValueError("Invalid plan")
        return [task for task in TASKS if task in selected]
    words = {"revenue": ("매출", "수익"), "alerts": ("경보", "알림", "화재"),
             "spaces": ("빈자리", "빈 자리", "남은 자리"), "fees": ("요금", "주차비", "결제 상태")}
    selected = {task for task, keywords in words.items() if any(word in question for word in keywords)}
    if re.search(r"(?:결제|정산)\s*상태", question):
        selected.add("fees")
    from .query import parse
    scope = parse(question)
    if scope.individual_fee or re.search(r"(?:내\s*차|제\s*차|차량|\d+\s*번\s*차).{0,15}(?:어디|위치|찾)", question):
        selected.add("vehicle")
    if scope.vehicle_kind == "plate" and re.search(r"위치|어디|찾", question):
        selected.add("vehicle")
    if scope.topics:
        selected.add("policies")
        if scope.vehicle_id:
            selected.add("vehicle")
    if scope.topics and not re.search(r"기본\s*요금|요금\s*(?:정책|표)|주차비", question):
        selected.discard("fees")
    return [task for task in TASKS if task in selected]
