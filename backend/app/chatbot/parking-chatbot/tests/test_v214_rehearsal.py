"""Run the actual demo server rehearsal in a separate process."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[3]
AVAILABLE = (all(importlib.util.find_spec(name) for name in ("fastapi", "uvicorn", "httpx"))
             and (REPO / "backend/app/simulation/run_parking_demo.py").is_file())


class RehearsalTests(unittest.TestCase):
    @unittest.skipUnless(AVAILABLE, "팀 레포와 선택 HTTP 의존성이 필요합니다.")
    def test_actual_demo_rehearsal(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/rehearsal_1106.py")], cwd=REPO,
            env={**os.environ, "PYTHONIOENCODING":"utf-8", "LLM_PROVIDER":"none",
                 "FREE_TURNAROUND_MINUTES":"0"},  # 리허설 ④ 결제 장면은 방금 입차한 차라 무료 회차(5분)를 끈다
            capture_output=True, encoding="utf-8", errors="replace", timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS 11/06 리허설 6/6", result.stdout)
