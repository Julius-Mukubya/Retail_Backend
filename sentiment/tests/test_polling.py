import json
from datetime import timedelta
from pathlib import Path
from unittest import mock

import requests
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from sentiment import budget, myfxbook
from sentiment.models import ApiCallLog, SentimentSnapshot
from sentiment.services import POLL_LOCK_KEY, PollInProgressError, poll_once

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'community_outlook.json').read_text())
LOGIN_OK = {'error': False, 'message': '', 'session': 'SESSION-1'}
INVALID_SESSION = {'error': True, 'message': 'Invalid session.'}


def fake_response(body):
    response = mock.Mock()
    response.json.return_value = body
    return response


@override_settings(
    MYFXBOOK_EMAIL='me@example.com',
    MYFXBOOK_PASSWORD='hunter2-secret',
    MYFXBOOK_DAILY_REQUEST_CAP=5,
)
class PollingTestCase(TestCase):
    def setUp(self):
        cache.clear()

    def patch_get(self, *bodies):
        return mock.patch('sentiment.myfxbook.requests.get', side_effect=[fake_response(b) for b in bodies])


class BudgetGuardTests(PollingTestCase):
    def test_blocks_request_once_cap_is_reached(self):
        with self.patch_get(*[FIXTURE] * 5) as get:
            cache.set(myfxbook.SESSION_CACHE_KEY, 'SESSION-1')
            for _ in range(5):
                myfxbook.fetch_community_outlook()
            with self.assertRaises(budget.BudgetExceededError):
                myfxbook.fetch_community_outlook()
        self.assertEqual(get.call_count, 5)
        self.assertEqual(ApiCallLog.objects.count(), 5)

    def test_failed_requests_count_against_budget(self):
        with mock.patch('sentiment.myfxbook.requests.get', side_effect=requests.ConnectTimeout('boom')):
            cache.set(myfxbook.SESSION_CACHE_KEY, 'SESSION-1')
            with self.assertRaises(myfxbook.NetworkError):
                myfxbook.fetch_community_outlook()
        self.assertEqual(budget.calls_in_window(), 1)
        self.assertEqual(ApiCallLog.objects.get().status, 'error')

    def test_calls_older_than_24_hours_free_up_budget(self):
        old = timezone.now() - timedelta(hours=25)
        for _ in range(5):
            ApiCallLog.objects.create(called_at=old, endpoint='x', status='ok')
        self.assertEqual(budget.calls_in_window(), 0)
        self.assertEqual(budget.remaining(), 5)
        budget.reserve_call('get-community-outlook')

    def test_remaining_never_negative(self):
        for _ in range(7):
            ApiCallLog.objects.create(called_at=timezone.now(), endpoint='x', status='ok')
        self.assertEqual(budget.remaining(), 0)


class SessionHandlingTests(PollingTestCase):
    def test_login_once_then_session_is_reused(self):
        with self.patch_get(LOGIN_OK, FIXTURE, FIXTURE) as get:
            myfxbook.fetch_community_outlook()
            myfxbook.fetch_community_outlook()
        endpoints = [c.args[0] for c in get.call_args_list]
        self.assertEqual(sum('login' in e for e in endpoints), 1)
        self.assertEqual(sum('get-community-outlook' in e for e in endpoints), 2)

    def test_invalid_session_triggers_exactly_one_relogin(self):
        cache.set(myfxbook.SESSION_CACHE_KEY, 'STALE')
        with self.patch_get(INVALID_SESSION, LOGIN_OK, FIXTURE) as get:
            body = myfxbook.fetch_community_outlook()
        self.assertEqual(len(body['symbols']), len(FIXTURE['symbols']))
        self.assertEqual(get.call_count, 3)
        self.assertEqual(cache.get(myfxbook.SESSION_CACHE_KEY), 'SESSION-1')

    def test_second_invalid_session_does_not_loop(self):
        cache.set(myfxbook.SESSION_CACHE_KEY, 'STALE')
        with self.patch_get(INVALID_SESSION, LOGIN_OK, INVALID_SESSION) as get:
            with self.assertRaises(myfxbook.InvalidSessionError):
                myfxbook.fetch_community_outlook()
        self.assertEqual(get.call_count, 3)

    def test_bad_credentials_raise_authentication_error(self):
        with self.patch_get({'error': True, 'message': 'Invalid email or password.'}):
            with self.assertRaises(myfxbook.AuthenticationError):
                myfxbook.fetch_community_outlook()

    def test_missing_credentials_make_no_request(self):
        with override_settings(MYFXBOOK_EMAIL='', MYFXBOOK_PASSWORD=''):
            with mock.patch('sentiment.myfxbook.requests.get') as get:
                with self.assertRaises(myfxbook.AuthenticationError):
                    myfxbook.fetch_community_outlook()
        get.assert_not_called()
        self.assertEqual(ApiCallLog.objects.count(), 0)

    def test_errors_never_leak_credentials_or_session(self):
        leaky = requests.ConnectionError('HTTPSConnectionPool url=/login.json?email=me@example.com&password=hunter2-secret')
        with mock.patch('sentiment.myfxbook.requests.get', side_effect=leaky):
            with self.assertRaises(myfxbook.NetworkError) as ctx:
                myfxbook.fetch_community_outlook()
        text = f"{ctx.exception!r} {ctx.exception.__cause__!r} {ApiCallLog.objects.get().detail}"
        self.assertNotIn('hunter2-secret', text)
        self.assertNotIn('me@example.com', text)
        self.assertIsNone(ctx.exception.__cause__)

    def test_non_json_response_is_an_error(self):
        bad = mock.Mock()
        bad.json.side_effect = ValueError('no json')
        cache.set(myfxbook.SESSION_CACHE_KEY, 'SESSION-1')
        with mock.patch('sentiment.myfxbook.requests.get', return_value=bad):
            with self.assertRaises(myfxbook.MyfxbookError):
                myfxbook.fetch_community_outlook()


class PollOnceTests(PollingTestCase):
    def test_stores_every_symbol_from_the_fixture(self):
        with self.patch_get(LOGIN_OK, FIXTURE):
            result = poll_once()
        self.assertEqual(result['symbols_saved'], len(FIXTURE['symbols']))
        self.assertEqual(SentimentSnapshot.objects.count(), len(FIXTURE['symbols']))
        eurusd = SentimentSnapshot.objects.get(symbol='EURUSD')
        self.assertEqual(float(eurusd.long_pct), 84)
        self.assertEqual(float(eurusd.long_volume), 16436.41)
        self.assertEqual(eurusd.long_positions, 29684)
        self.assertEqual(float(eurusd.avg_long_price), 1.1421)

    def test_all_symbols_in_one_poll_share_a_timestamp(self):
        with self.patch_get(LOGIN_OK, FIXTURE):
            poll_once()
        self.assertEqual(SentimentSnapshot.objects.values('fetched_at').distinct().count(), 1)

    def test_malformed_entry_is_skipped_not_fatal(self):
        body = {'error': False, 'symbols': [FIXTURE['symbols'][0], {'name': 'BROKEN'}]}
        with self.patch_get(LOGIN_OK, body):
            result = poll_once()
        self.assertEqual(result['symbols_saved'], 1)

    def test_second_poll_while_locked_is_refused_without_calling_api(self):
        cache.add(POLL_LOCK_KEY, 1, 300)
        with mock.patch('sentiment.myfxbook.requests.get') as get:
            with self.assertRaises(PollInProgressError):
                poll_once()
        get.assert_not_called()

    def test_lock_released_after_failure(self):
        with mock.patch('sentiment.myfxbook.requests.get', side_effect=requests.ConnectTimeout()):
            with self.assertRaises(myfxbook.NetworkError):
                poll_once()
        self.assertIsNone(cache.get(POLL_LOCK_KEY))

    def test_budget_exhaustion_stops_poll_before_any_request(self):
        for _ in range(5):
            ApiCallLog.objects.create(called_at=timezone.now(), endpoint='x', status='ok')
        with mock.patch('sentiment.myfxbook.requests.get') as get:
            with self.assertRaises(budget.BudgetExceededError):
                poll_once()
        get.assert_not_called()
