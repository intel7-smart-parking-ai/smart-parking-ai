"""V51 regression suite, no physical camera, model downloads, or real HTTP."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

root=Path(__file__).resolve().parent
parser=argparse.ArgumentParser()
parser.add_argument('--report',type=Path)
args=parser.parse_args()
test_temp=root/'.test_tmp';test_temp.mkdir(exist_ok=True)
os.environ['TEMP']=str(test_temp);os.environ['TMP']=str(test_temp);tempfile.tempdir=str(test_temp)
started=time.perf_counter()
suite=unittest.defaultTestLoader.discover(str(root/'tests'),pattern='test_v*.py')
result=unittest.TextTestRunner(verbosity=2).run(suite)
report={'product_version':'V51','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
        'skipped':len(result.skipped),'passed':result.wasSuccessful(),'seconds':round(time.perf_counter()-started,3),
        'scope':'V19-V51 geometry/color, identity evidence and OCR mocks, automatic ROI/anchor guards, Korean paired assets, copy counts, data integrity/audit/export, one-start Tk workflow, split workers, mocked HTTP; no physical camera or live server'}
if args.report:args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
raise SystemExit(0 if result.wasSuccessful() else 1)
