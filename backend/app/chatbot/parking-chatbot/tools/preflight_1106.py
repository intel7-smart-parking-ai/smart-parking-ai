"""Local 11/06 checks; never connects to the configured operating server."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rehearsal', action='store_true', help='run isolated six-question HTTP rehearsal')
    parser.add_argument('--unit', action='store_true', help='also run the complete unittest suite')
    parser.add_argument('--output', type=Path, default=ROOT / '.validation' / 'preflight_1106')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, 'LLM_PROVIDER': 'none', 'DATA_SOURCE': 'mock',
                   'PYTHONIOENCODING': 'utf-8'}
    questions = sorted((ROOT / 'tests').glob('questions*.csv'))
    checks = [('questions', [str(ROOT / 'tests/run_eval.py'), *map(str, questions)], 30)]
    if args.unit:
        checks.append(('unit', ['-m', 'unittest', 'discover', '-s', 'tests', '-v'], 240))
    if args.rehearsal:
        checks.append(('rehearsal', [str(ROOT / 'tools/rehearsal_1106.py')], 55))
    results = []
    for name, command, timeout in checks:
        log = output / (name + '.log')
        try:
            result = subprocess.run([sys.executable, *command], cwd=ROOT, env=environment,
                                    capture_output=True, encoding='utf-8', errors='replace', timeout=timeout)
            log.write_text(result.stdout + result.stderr, encoding='utf-8')
            status = 'PASS' if result.returncode == 0 else 'FAIL'
            # The rehearsal script explains missing repository/HTTP dependencies.
            if name == 'rehearsal' and 'PASS 11/06 리허설 6/6' not in result.stdout:
                status = 'BLOCKED' if result.returncode == 0 else 'FAIL'
            item = {'check': name, 'status': status, 'exit_code': result.returncode}
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.write_text(type(exc).__name__ + ': ' + str(exc), encoding='utf-8')
            item = {'check': name, 'status': 'BLOCKED', 'reason': type(exc).__name__}
        item['log'] = str(log)
        results.append(item)
        print(f"{item['status']} {name}: {log}", flush=True)
    report = {'at_kst': datetime.now(timezone(timedelta(hours=9))).isoformat(),
              'scope': 'local questions/unittest/isolated HTTP, LLM disabled; no browser or device QA',
              'results': results}
    (output / 'preflight.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(item['status'] == 'PASS' for item in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
