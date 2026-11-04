"""R1 브라우저 검사: 실제 LLM 대신 고정된 도구 선택만 주입한다. 유료 호출 없음."""
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.argv = [str(Path(__file__).with_name('run_browser_checks.py')), 'browser_v2_2.cjs']
with patch('chatbot.llm_router.route', return_value={'intent':'call_admin','params':{}}):
    runpy.run_path(str(Path(__file__).with_name('run_browser_checks.py')), run_name='__main__')
