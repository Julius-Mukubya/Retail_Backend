import json
from datetime import timedelta
from pathlib import Path

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from sentiment.exposure import store_exposures
from sentiment.models import ApiCallLog
from sentiment.services import save_snapshots
from sentiment.symbols import classify

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'community_outlook.json').read_text())


def load_poll(minutes_ago: int):
    at = timezone.now() - timedelta(minutes=minutes_ago)
    save_snapshots(FIXTURE['symbols'], at)
    store_exposures(at)
    return at


class ClassifyTests(TestCase):
    def test_groups(self):
        expected = {
            'EURUSD': 'majors', 'USDJPY': 'majors', 'EURGBP': 'crosses', 'AUDNZD': 'crosses',
            'USDTRY': 'exotics', 'EURCZK': 'exotics', 'XAUUSD': 'metals', 'XAGEUR': 'metals',
            'BTCUSD': 'crypto', 'SOLUSD': 'crypto',
        }
        for symbol, group in expected.items():
            self.assertEqual(classify(symbol), group, symbol)

    def test_every_fixture_symbol_gets_a_group(self):
        groups = {classify(s['name']) for s in FIXTURE['symbols']}
        self.assertEqual(groups, {'majors', 'crosses', 'exotics', 'metals', 'crypto'})


class EmptyDatabaseTests(TestCase):
    def test_lists_are_empty_not_errors(self):
        for name in ('/api/pairs/', '/api/currencies/'):
            body = self.client.get(name).json()
            self.assertTrue(body['success'])
            self.assertEqual(body['data'], [])

    def test_status_reports_stale_with_no_data(self):
        data = self.client.get('/api/status/').json()['data']
        self.assertTrue(data['stale'])
        self.assertIsNone(data['last_fetched_at'])
        self.assertEqual(data['coverage']['polls'], 0)


class PairEndpointTests(TestCase):
    def setUp(self):
        load_poll(60)
        load_poll(30)

    def test_latest_snapshot_for_every_symbol(self):
        body = self.client.get('/api/pairs/').json()
        self.assertEqual(len(body['data']), len(FIXTURE['symbols']))
        eurusd = next(p for p in body['data'] if p['symbol'] == 'EURUSD')
        self.assertEqual(eurusd['long_pct'], 84.0)
        self.assertEqual(eurusd['group'], 'majors')
        self.assertEqual(len(eurusd['sparkline']), 2)

    def test_group_filter(self):
        data = self.client.get('/api/pairs/?group=crypto').json()['data']
        self.assertTrue(data)
        self.assertTrue(all(p['group'] == 'crypto' for p in data))

    def test_unknown_group_is_rejected(self):
        self.assertEqual(self.client.get('/api/pairs/?group=bogus').status_code, 400)

    def test_history_is_chronological_and_case_insensitive(self):
        data = self.client.get('/api/pairs/eurusd/history/').json()['data']
        self.assertEqual(len(data), 2)
        self.assertLess(data[0]['fetched_at'], data[1]['fetched_at'])

    def test_history_hours_window(self):
        # setUp loaded polls 60 and 30 minutes ago; only the 30-minute-old one is inside 1 hour.
        data = self.client.get('/api/pairs/EURUSD/history/?hours=1').json()['data']
        self.assertEqual(len(data), 1)
        data = self.client.get('/api/pairs/EURUSD/history/?hours=2').json()['data']
        self.assertEqual(len(data), 2)

    def test_unknown_symbol_is_404(self):
        self.assertEqual(self.client.get('/api/pairs/NOPE/history/').status_code, 404)


class CurrencyEndpointTests(TestCase):
    def setUp(self):
        load_poll(30)

    def test_volume_method_has_buy_sell_and_traded(self):
        body = self.client.get('/api/currencies/').json()
        self.assertEqual(body['method'], 'volume')
        self.assertEqual(len(body['data']), 8)
        eur = next(c for c in body['data'] if c['currency'] == 'EUR')
        self.assertAlmostEqual(eur['buy_pct'] + eur['sell_pct'], 100)
        self.assertAlmostEqual(eur['total_traded'], eur['bought'] + eur['sold'], places=2)
        self.assertAlmostEqual(eur['net'], eur['bought'] - eur['sold'], delta=1)

    def test_sorted_by_net_descending(self):
        nets = [c['net'] for c in self.client.get('/api/currencies/').json()['data']]
        self.assertEqual(nets, sorted(nets, reverse=True))

    def test_percent_method_has_no_gross_figures(self):
        data = self.client.get('/api/currencies/?method=percent').json()['data']
        self.assertEqual(len(data), 8)
        self.assertNotIn('bought', data[0])

    def test_bad_method_rejected(self):
        self.assertEqual(self.client.get('/api/currencies/?method=x').status_code, 400)

    def test_history(self):
        load_poll(60)
        body = self.client.get('/api/currencies/eur/history/?method=volume').json()
        self.assertEqual(len(body['data']), 2)
        self.assertNotIn('pairs', body['data'][0])
        self.assertEqual(body['gap_threshold_minutes'], 60)

    def test_unsupported_currency_is_404(self):
        self.assertEqual(self.client.get('/api/currencies/TRY/history/').status_code, 404)


@override_settings(MYFXBOOK_DAILY_REQUEST_CAP=85, MYFXBOOK_POLL_INTERVAL_MINUTES=30)
class StatusEndpointTests(TestCase):
    def test_fresh_data_is_not_stale_and_budget_is_reported(self):
        load_poll(10)
        for _ in range(3):
            ApiCallLog.objects.create(called_at=timezone.now(), endpoint='x', status='ok')
        data = self.client.get('/api/status/').json()['data']
        self.assertFalse(data['stale'])
        self.assertEqual(data['budget']['used_24h'], 3)
        self.assertEqual(data['budget']['remaining'], 82)

    def test_old_data_is_stale(self):
        load_poll(120)
        self.assertTrue(self.client.get('/api/status/').json()['data']['stale'])

    def test_gap_between_polls_is_reported(self):
        load_poll(300)
        load_poll(280)
        load_poll(30)
        data = self.client.get('/api/status/').json()['data']
        self.assertEqual(data['coverage']['polls'], 3)
        self.assertEqual(len(data['coverage']['gaps']), 1)
        self.assertEqual(data['coverage']['gaps'][0]['minutes'], 250)
        self.assertLess(data['coverage']['coverage_pct'], 5)
