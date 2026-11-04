"""v2.10: 팀 main.py 등록 진입점, 칸 배정 전 '입차 확인 중', 서버 vehicle_type 우선."""
import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path

from chatbot import briefing, config, engine as e
from tests import test_api_contract as contract_base

ROOT = Path(__file__).resolve().parent.parent          # parking-chatbot
REPO = ROOT.parents[3]                                  # 팀 레포 루트 (backend/app/chatbot/parking-chatbot 기준)


def _dotenv_sets(key):
    env = ROOT / ".env"
    return env.exists() and any(line.split("=", 1)[0].strip() == key
                                for line in env.read_text(encoding="utf-8").splitlines() if "=" in line)


class EmbeddedConfigTests(unittest.TestCase):
    @unittest.skipIf(_dotenv_sets("DATA_SOURCE"), ".env에 DATA_SOURCE가 있어 기본값 확인 불가")
    def test_embedded_defaults_to_team_api(self):
        env = {k: v for k, v in os.environ.items() if k not in ("DATA_SOURCE", "DEMO_ENABLED", "BACKEND_BASE_URL")}
        env.update(CHATBOT_EMBEDDED="1", LLM_PROVIDER="none", PYTHONIOENCODING="utf-8")
        out = subprocess.run([sys.executable, "-c", "from chatbot import config; print(config.DATA_SOURCE, config.DEMO_ENABLED, config.BACKEND_BASE_URL)"],
                             cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["api", "False", "http://127.0.0.1:8000"])

    def test_standalone_default_stays_mock(self):
        if _dotenv_sets("DATA_SOURCE") or os.getenv("DATA_SOURCE"):
            self.skipTest("DATA_SOURCE가 따로 설정됨")
        self.assertFalse(config.EMBEDDED)
        self.assertEqual(config.DATA_SOURCE, "mock")

    @unittest.skipUnless(importlib.util.find_spec("fastapi") and (REPO / "backend" / "__init__.py").exists(),
                         "FastAPI 미설치 또는 팀 레포 밖에서 실행")
    def test_team_main_can_include_router(self):
        code = ("from fastapi import FastAPI; from fastapi.testclient import TestClient; "
                "from backend.app.chatbot.router import router; "
                "app = FastAPI(); app.include_router(router); "
                "response = TestClient(app).post('/api/v1/chat', json={'session_id':'embed', 'text':'A 빈자리'}); "
                "assert response.status_code == 200, response.text; "
                "assert response.json()['text']; print('/api/v1/chat', response.status_code)")
        env = {**os.environ, "DATA_SOURCE": "mock", "LLM_PROVIDER": "none", "PYTHONIOENCODING": "utf-8"}
        out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("/api/v1/chat", out.stdout)


class PendingLocationTests(contract_base.ApiContractTests):
    """번호는 인식됐지만 칸 배정 전. 위치를 지어내지 않는다."""

    def test_pending_status_says_checking(self):
        self.location_state = "pending"
        text = self.ask("11번 차 어디 있어?")["text"]
        self.assertIn("입차 확인 중", text)
        self.assertNotIn("A-01", text)

    def test_null_space_is_pending(self):
        self.location_space = None
        self.location_state = "occupied"
        r = self.ask("11번 차 어디 있어?")
        self.assertIn("개발원주차장 입차 확인 중", r["text"])
        self.assertIsNone(r.get("card"))

    def test_pending_fee_does_not_query_billing(self):
        self.location_state = "entering"
        self.session_id = "session-01"
        text = self.ask("11번 차 요금 얼마야?")["text"]
        self.assertIn("입차 확인 중", text)
        self.assertFalse([r for r in self.requests if r[1].startswith("/api/v1/sessions/")])

    def test_pending_payment_never_posts(self):
        self.location_state = "pending"
        self.session_id = "session-01"
        self.ask("11번 결제")
        self.assertEqual(self.posts(), [])

    def test_pending_vehicle_context(self):
        self.location_state = "pending"
        self.ask("11번 차 어디 있어?")
        self.assertEqual(e.vehicle_context(e.get_session("s"))["state"], "pending")


# 부모 클래스의 기존 계약 테스트는 test_api_contract.py에서 이미 돈다. 여기서는 새 테스트만 실행한다.
for _name in dir(contract_base.ApiContractTests):
    if _name.startswith("test_"):
        setattr(PendingLocationTests, _name, None)


class ServerVehicleTypeTests(unittest.TestCase):
    def test_server_vehicle_type_beats_lots_json(self):
        ev_listed = config.catalog.settings["ev_vehicles"][0]
        alert = {"lot_id": "A", "spot_no": 1, "level": 2}
        cars = [{"car_id": ev_listed, "lot_id": "A", "spot_no": 2, "vehicle_type": "general"},
                {"car_id": "77", "lot_id": "A", "spot_no": 7, "vehicle_type": "ev"}]
        data = briefing.build(alert, cars, True)
        text = str(data)
        self.assertIn(f"{ev_listed}번 · 전기차 목록에 없음", text)

    def test_lots_json_used_without_server_type(self):
        ev_listed = config.catalog.settings["ev_vehicles"][0]
        data = briefing.build({"lot_id": "A", "spot_no": 1, "level": 2},
                              [{"car_id": ev_listed, "lot_id": "A", "spot_no": 2}], True)
        self.assertIn(f"{ev_listed}번 · 전기차", str(data))


if __name__ == "__main__":
    unittest.main()
