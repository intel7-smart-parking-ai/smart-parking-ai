"""Team billing boundary table; no API-side fee recalculation."""
from datetime import timedelta
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
from chatbot import config, templates as T
from chatbot.adapters import MockAdapter, calc_fee, fee_details, now


class MockFeePolicyTests(unittest.TestCase):
    def test_eleven_team_boundaries(self):
        for minutes, amount, capped in ((0,1000,False),(30,1000,False),(31,1500,False),
                (40,1500,False),(41,2000,False),(200,9500,False),(201,10000,True),
                (1440,10000,True),(1441,11000,True),(1671,20000,True),(2880,20000,True)):
            with self.subTest(minutes=minutes):
                self.assertEqual(fee_details(minutes), (amount, capped))
                self.assertEqual(calc_fee(minutes), amount)

    def test_seconds_rounded_up_in_quote_and_payment(self):
        fixed = now()
        with patch('chatbot.adapters.now', return_value=fixed):
            adapter = MockAdapter()
            for seconds, minutes, amount in ((0,1,1000),(1800,30,1000),(1801,31,1500),(86400,1440,10000),(86401,1441,11000)):
                with self.subTest(seconds=seconds):
                    car = adapter.cars['17']
                    car.update(entry_at=fixed-timedelta(seconds=seconds), paid_at=None)
                    d = adapter.get_fee('17')
                    self.assertEqual((d['minutes'],d['fee']), (minutes,amount))
                    self.assertEqual(adapter.pay('17', quote_amount=amount)['amount'],amount)
                    self.assertEqual('하루 최대 요금이 적용됐어요' in T.fee(d), d['is_daily_capped'])

    def test_all_fee_settings_positive_and_environment_overridable(self):
        root = Path(__file__).resolve().parent.parent
        for key in ('BASE_MINUTES','BASE_FEE','UNIT_MINUTES','UNIT_FEE','DAILY_CAP'):
            for value in ('0','-1'):
                env={**os.environ,key:value,'PYTHONIOENCODING':'utf-8'}
                r=subprocess.run([sys.executable,'-c','from chatbot import config'],cwd=root,
                    env=env,capture_output=True,text=True,encoding='utf-8',timeout=10)
                self.assertNotEqual(r.returncode,0)
                self.assertIn(key,r.stderr)
        with patch.object(config,'BASE_FEE',2000):
            self.assertEqual(calc_fee(1),2000)
