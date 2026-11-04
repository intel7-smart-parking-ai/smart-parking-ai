"""Send synthetic observations to the local Step 3 demo server."""
from argparse import ArgumentParser
from datetime import datetime, timezone
from getpass import getpass
from time import sleep
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from backend.app.simulation.mock_data import SPACES

BASE_URL = "http://127.0.0.1:8002"
EVENTS = "/api/v1/internal/vision-events"
LOTS = "/api/v1/parking-lots"
A_SPACES = tuple(space for space in SPACES if space.lot_id == "A")


class DemoError(Exception):
    pass


def local_base_url(value):
    url = urlsplit(value)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost"}
            or url.username is not None or url.path not in ("", "/") or url.query or url.fragment
            or not url.port):
        raise ValueError("로컬 HTTP 주소와 포트가 필요합니다.")
    return f"http://127.0.0.1:{url.port}"


def run_demo(client, token, *, short=False, hold=False, depart=False, keep_empty=False, wait=sleep,
             clock=lambda: datetime.now(timezone.utc), output=print):
    if hold and depart:
        raise DemoError("--hold와 --depart는 따로 실행하세요.")
    if keep_empty and not depart:
        raise DemoError("--keep-empty는 --depart와 함께 실행하세요.")
    selected = tuple(space for space in A_SPACES if not short or space.space_id in ("A-01", "A-02", "A-03"))
    selected_ids = {space.space_id for space in selected}

    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        if response.status_code in (401, 403):
            raise DemoError("인증 실패. 시연 서버에 입력한 토큰을 확인하세요.")
        expected = 202 if method == "POST" else 200
        if response.status_code != expected:
            if depart and method == "POST" and response.status_code in (409, 503):
                code = response.json().get("error", {}).get("code", "UNKNOWN")
                hint = ("이벤트 충돌과 동시 전송을 확인하세요." if response.status_code == 409
                        else "저장소 한도 또는 세션 저장 오류를 확인하세요.")
                raise DemoError(f"출차 확인 중단: HTTP {response.status_code} ({code}). {hint} "
                                "오류 원인을 해결한 뒤 같은 범위로 --depart를 다시 실행하세요.")
            raise DemoError(f"{method} {path}: HTTP {response.status_code}")
        return response.json()

    def verify(label, status, empty, *, report=True):
        spaces = request("GET", f"{LOTS}/A/spaces")["items"]
        lot = next(row for row in request("GET", LOTS)["items"] if row["lot_id"] == "A")
        states = {row["space_id"]: row["status"] for row in spaces}
        expected = {space.space_id: status if space.space_id in selected_ids else "empty"
                    for space in A_SPACES}
        if depart:
            expected = states | {space_id: "empty" for space_id in selected_ids}
            empty = sum(value == "empty" for value in states.values())
        if (set(states) != {space.space_id for space in A_SPACES} or states != expected
                or lot["total_spaces"] != len(A_SPACES) or lot["empty_spaces"] != empty):
            raise DemoError(f"{label} 확인 실패: 빈자리 {lot['empty_spaces']}, 칸 상태 {states}. "
                            "다른 이벤트 전송을 중지하고 동일 범위의 --depart를 실행하세요. "
                            "계속 실패하면 서버 오류와 조회 상태를 확인하세요.")
        if report:
            output(f"{label}: {', '.join(space.space_id for space in selected)} | {status} | 빈자리 {empty}/{len(A_SPACES)}")

    def send_round(occupied, *, targets=selected_ids):
        for number, space in enumerate(selected, start=10):
            if space.space_id not in targets:
                continue
            request("POST", EVENTS, headers={"Authorization": f"Bearer {token}"}, json={
                    "event_id": uuid4().hex,
                    "event_type": "vehicle_observed" if occupied else "space_empty_observed",
                    "source": "simulation", "lot_id": "A", "camera_id": "camera_a",
                    "space_id": space.space_id, "car_id": str(number) if occupied else None,
                    "vehicle_type": space.space_type if occupied else None,
                    "confidence": .95, "bbox_xyxy": [10, 10, 20, 20] if occupied else None,
                    "observed_at": clock().isoformat(),
            })

    if depart:
        # 조회의 uncertain에는 stale 활성 세션도 포함되므로 빈칸 증거를 다시 보낸다.
        rows = request("GET", f"{LOTS}/A/spaces")["items"]
        pending = {row["space_id"] for row in rows
                   if row["space_id"] in selected_ids and row["status"] != "empty"}
        if pending:
            for _ in range(4):
                wait(1.1)
                send_round(False, targets=pending)
        verify("출차", "empty", None)
        if keep_empty:
            output("빈자리 유지 중. 같은 칸에 실물 카메라 이벤트를 보내지 마세요. "
                   "다음 --hold 전에 Ctrl+C로 전송을 멈추세요.")
            while True:
                wait(1.1)
                send_round(False)
                verify("빈자리 유지", "empty", None, report=False)
        return

    verify("시작", "empty", len(A_SPACES))
    for occupied, rounds in ((True, 3), (False, 4)):
        # 칸마다 대기하면 먼저 처리한 칸이 stale 상태가 되므로 회차 단위로 관측한다.
        for tick in range(rounds):
            if tick or not occupied:
                wait(1.1)
            send_round(occupied)
        verify("입차" if occupied else "출차", "occupied" if occupied else "empty",
               len(A_SPACES) - len(selected) if occupied else len(A_SPACES))
        if occupied and hold:
            output("점유 유지 중. Ctrl+C로 관측 전송을 중단할 수 있습니다.")
            while True:
                wait(1.1)
                send_round(True)
                verify("점유 유지", "occupied", len(A_SPACES) - len(selected), report=False)


def main():
    parser = ArgumentParser(description="A주차장 합성 입차·출차 시연. 같은 칸에 실물 카메라 이벤트와 함께 사용하지 마세요.")
    parser.add_argument("--short", action="store_true", help="A-01~A-03만 시연")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--hold", action="store_true", help="입차 후 점유 유지")
    modes.add_argument("--depart", action="store_true", help="점유 유지 중단 후 대상 칸 출차")
    parser.add_argument("--keep-empty", action="store_true",
                        help="--depart 후 빈자리 유지. 다음 --hold 전에 Ctrl+C로 중단")
    parser.add_argument("--base-url", default=BASE_URL, help="로컬 시연 서버 주소")
    args = parser.parse_args()
    if args.keep_empty and not args.depart:
        parser.error("--keep-empty는 --depart와 함께 실행하세요.")
    try:
        base_url = local_base_url(args.base_url)
    except ValueError:
        parser.error("--base-url에 로컬 HTTP 주소와 포트를 입력하세요.")
    try:
        token = getpass("시연 서버 토큰: ")
        if not token.isascii() or len(token) < 16:
            raise SystemExit("시연 토큰은 ASCII 16자 이상이어야 합니다.")
        with httpx.Client(base_url=base_url, timeout=3, trust_env=False) as client:
            run_demo(client, token, short=args.short, hold=args.hold, depart=args.depart,
                     keep_empty=args.keep_empty)
    except KeyboardInterrupt:
        print("시연 이벤트 전송을 중단했습니다.")
    except httpx.RequestError:
        raise SystemExit(f"{base_url} 연결 실패 또는 응답 시간 초과. 시연 서버를 확인하세요.") from None
    except (DemoError, ValueError, KeyError, TypeError, StopIteration) as error:
        message = str(error) if isinstance(error, DemoError) else "시연 서버 응답 형식을 확인하세요."
        raise SystemExit(message) from None


if __name__ == "__main__":
    main()
