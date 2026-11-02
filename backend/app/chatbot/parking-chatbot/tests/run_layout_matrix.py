"""동일 코어 회귀 세트를 3개/4개 설정에서 실행. 새 D 시나리오는 별도 test_navigation.py."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
root=Path(__file__).resolve().parent.parent
base=json.loads((root/'chatbot/lots.json').read_text(encoding='utf-8'))
results=[]
code="""
import pathlib,unittest
suite=unittest.TestSuite();loader=unittest.TestLoader()
for p in sorted(pathlib.Path('tests').glob('test*.py')):
 if not p.name.startswith('test_navigation'):
  suite.addTests(loader.discover('tests',pattern=p.name))
r=unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not r.wasSuccessful())
"""
for size in (3,4):
 with tempfile.TemporaryDirectory()as directory:
  settings={**base,'lots':base['lots'][:size]}
  path=Path(directory)/'lots.json';path.write_text(json.dumps(settings,ensure_ascii=False),encoding='utf-8')
  p=subprocess.run([sys.executable,'-c',code],cwd=root,env={**os.environ,'PYTHONIOENCODING':'utf-8','PARKING_SETTINGS_PATH':str(path)},capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
  results.append({'lots':size,'exit_code':p.returncode,'output':p.stdout+p.stderr})
print(json.dumps(results,ensure_ascii=False,indent=2))
raise SystemExit(any(r['exit_code']for r in results))
