"""Bounded payment slot ownership."""
import threading
import time
import unittest
from unittest.mock import patch
from chatbot import config, engine as e
from chatbot.adapters import request_deadline
from chatbot.state import Session, Pending, Flow

class BoundaryTests(unittest.TestCase):
    def test_wait_timeout_never_enters_payment_or_releases_owner(self):
        pending=Pending(Flow.CONFIRM_ACTION,'pay_fee',{},'17')
        token=request_deadline.set(time.monotonic()+.05)
        before=set(e._uncertain_payments)
        try:
            with patch.object(e,'_payment_active',True), patch.object(e,'_execute_payment_locked') as send:
                began=time.monotonic();result=e._execute_payment(Session(car_id='17'),pending)
                self.assertLess(time.monotonic()-began,.2)
                self.assertTrue(e._payment_active);send.assert_not_called()
                self.assertEqual(result,(e.T.pay({'status':'unavailable'},'17'),None,['fee','call_admin'],None))
                self.assertEqual(e._uncertain_payments,before)
        finally:request_deadline.reset(token)

    def test_release_before_deadline_runs_once_and_next_request_progresses(self):
        pending=Pending(Flow.CONFIRM_ACTION,'pay_fee',{},'17')
        def release():
            with e._payment_condition:
                e._payment_active=False;e._payment_condition.notify_all()
        with patch.object(e,'_payment_active',True),patch.object(e,'_execute_payment_locked',return_value=('sent',None,[],None)) as send:
            timer=threading.Timer(.03,release);timer.start()
            token=request_deadline.set(time.monotonic()+.3)
            try:self.assertEqual(e._execute_payment(Session(),pending)[0],'sent')
            finally:request_deadline.reset(token);timer.join()
            self.assertEqual(send.call_count,1);self.assertFalse(e._payment_active)
            e._execute_payment(Session(),pending)
            self.assertEqual(send.call_count,2);self.assertFalse(e._payment_active)

    def test_no_context_uses_default_wait_limit(self):
        pending=Pending(Flow.CONFIRM_ACTION,'pay_fee',{},'17')
        token=request_deadline.set(None)
        try:
            with patch.object(e,'_payment_active',True),patch.object(config,'REQUEST_BUDGET_SEC',.02),patch.object(e,'_execute_payment_locked') as send:
                self.assertIsNone(e._execute_payment(Session(),pending)[1]);send.assert_not_called()
                self.assertTrue(e._payment_active)
        finally:request_deadline.reset(token)

