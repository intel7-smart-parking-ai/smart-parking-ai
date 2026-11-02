"""3팀 LED 모의 화재 감지 단위 테스트 (공동: 윤명수 · 서정범)"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.app.vision.fire.detector import LedFireDetector
from backend.app.vision.fire.roi_matcher import match_led_to_space
from backend.app.vision.fire.events import create_mock_fire_event

def test_fire():
  detector = LedFireDetector()
  det = detector.detect_from_frame(mock_scenario="LED_ON")
  assert len(det) == 1
  assert match_led_to_space(det[0]["center_xy"], "A") == "A-02"
  assert len(detector.detect_from_frame(mock_scenario="RED_CAR_ONLY")) == 0
  print("[PASS] test_fire: LED 점등 검출, A-02 ROI 매칭 및 적색 미니카 오탐 차단 확인")

if __name__ == "__main__":
  test_fire()
