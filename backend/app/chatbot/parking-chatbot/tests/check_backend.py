"""팀 API 연결 후 읽기 전용 점검. DATA_SOURCE=api일 때만 실행한다."""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chatbot import config
from chatbot.adapters import DataUnavailable, build_adapter


def main():
    if config.DATA_SOURCE != "api":
        print(".env에서 DATA_SOURCE=api와 BACKEND_BASE_URL을 설정한 뒤 실행하세요.")
        return 2
    adapter = build_adapter()
    vehicle = sys.argv[1] if len(sys.argv) > 1 else "07"
    if not vehicle.isdecimal() or not 1 <= int(vehicle) <= 24:
        print("차량 번호는 01~24입니다."); return 2
    vehicle = vehicle.zfill(2)
    checks = [("빈자리", adapter.get_empty_spots), ("경보", adapter.get_alert),
              ("차량 위치", lambda: adapter.find_car(vehicle)), ("요금", lambda: adapter.get_fee(vehicle)),
              ("주차 차량 목록", adapter.list_cars)]
    failures = 0
    for name, fn in checks:
        started = time.perf_counter()
        try:
            result = fn()
            if isinstance(result, dict) and result.get("stale"):
                raise DataUnavailable()
            print(f"PASS {name}: {round((time.perf_counter()-started)*1000)}ms" + (" (차량 기록 없음)" if result is None else ""))
        except DataUnavailable:
            failures += 1
            print(f"FAIL {name}: 연결·인증·응답 형식·갱신 시각을 확인하세요.")
    print("읽기 전용 검사 완료. 결제·출차·보고서 변경은 실행하지 않았습니다.")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
