"""Server evacuation signals block payment without changing unrelated flows."""
import unittest
from unittest.mock import patch

from chatbot import contracts, engine as e
from chatbot.adapters import DataUnavailable
import test_api_contract as fixtures


class ServerPaymentBlockTests(unittest.TestCase):
    # Reuse fixture lifecycle without inheriting/discovering its 24 tests again.
    setUp = fixtures.ApiContractTests.setUp
    tearDown = fixtures.ApiContractTests.tearDown
    posts = fixtures.ApiContractTests.posts
    ask = fixtures.ApiContractTests.ask

    def fee_signal(self, value):
        original = self.api._request

        def request(method, path, body=None):
            status, response, headers = original(method, path, body)
            if method == 'GET' and path.startswith('/api/v1/sessions/'):
                response = {**response, 'payment_blocked': value}
            return status, response, headers

        return patch.object(self.api, '_request', side_effect=request)

    def assert_blocked(self, result):
        self.assertEqual(result['flow'], 'idle')
        self.assertTrue({'pay', 'confirm_yes'}.isdisjoint(c['id'] for c in result['chips']))
        self.assertIn('모의 화재', result['text'])
        self.assertIn('들어오지 마세요', result['text'])
        self.assertIsNotNone(result['safety'])
        self.assertIn('들어오지 마세요', result['safety']['guidance'])
        self.assertIsNotNone(result['banner'])
        self.assertIn('차단', result['banner']['text'])
        self.assertIsNone(e.get_session('s').pending)
        self.assertFalse(e._uncertain_payments)

    def test_server_blocked_never_opens_payment(self):
        self.session_id = 'session-01'
        with self.fee_signal(True):
            self.assert_blocked(self.ask('11번 결제'))
        self.assertEqual(self.posts(), [])

    def test_fee_view_does_not_offer_pay_when_blocked(self):
        self.session_id = 'session-01'
        with self.fee_signal(True):
            result = self.ask('11번 요금')
            self.assert_blocked(result)
            self.assertIn('2,500원', result['text'])
        self.assertEqual(self.posts(), [])

    def test_blocked_after_confirmation_opens_sends_no_post(self):
        self.session_id = 'session-01'
        question = self.ask('11번 결제')
        self.adapter.cache.clear()
        with self.fee_signal(True):
            self.assert_blocked(e.handle('s', chip_id='confirm_yes', confirmation_token=question['confirmation_token']))
        self.assertEqual(self.posts(), [])

    def test_evacuation_error_never_retries_or_marks_uncertain(self):
        self.session_id = 'session-01'
        self.payment_code = 'EVACUATION_ACTIVE'
        question = self.ask('11번 결제')
        self.assert_blocked(e.handle('s', chip_id='confirm_yes', confirmation_token=question['confirmation_token'], request_id='blocked-once'))
        e.handle('s', chip_id='confirm_yes', confirmation_token=question['confirmation_token'], request_id='blocked-once')
        e.handle('s', text='네')
        self.assertEqual(len(self.posts()), 1)
        self.assertFalse(e._uncertain_payments)

    def test_explicit_false_keeps_existing_payment_flow(self):
        self.session_id = 'session-01'
        with self.fee_signal(False):
            question = self.ask('11번 결제')
            self.assertEqual(question['flow'], 'confirm_action')
            result = e.handle('s', chip_id='confirm_yes', confirmation_token=question['confirmation_token'])
        self.assertEqual(result['card']['type'], 'payment')
        self.assertEqual(len(self.posts()), 1)

    def test_invalid_block_flag_is_not_treated_as_unblocked(self):
        self.session_id = 'session-01'
        with self.fee_signal('false'):
            with self.assertRaises((DataUnavailable, contracts.ContractError)):
                self.api.get_fee('11')
        self.assertEqual(self.posts(), [])
