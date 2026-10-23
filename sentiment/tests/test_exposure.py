import json
from decimal import Decimal
from pathlib import Path

from django.test import TestCase
from django.utils import timezone

from sentiment.exposure import compute_exposures, recompute_all, split_pair, store_exposures
from sentiment.models import CurrencyExposure, SentimentSnapshot
from sentiment.services import save_snapshots

FIXTURE = json.loads((Path(__file__).parent / 'fixtures' / 'community_outlook.json').read_text())
PERCENT = CurrencyExposure.Method.PERCENT
VOLUME = CurrencyExposure.Method.VOLUME


def snap(symbol, long_pct, short_pct, long_vol, short_vol, price):
    """Unsaved snapshot with identical long/short entry prices so the pair price is exact."""
    return SentimentSnapshot(
        fetched_at=timezone.now(), symbol=symbol,
        long_pct=Decimal(long_pct), short_pct=Decimal(short_pct),
        long_volume=Decimal(long_vol), short_volume=Decimal(short_vol),
        long_positions=1, short_positions=1,
        avg_long_price=Decimal(price), avg_short_price=Decimal(price),
    )


class SplitPairTests(TestCase):
    def test_majors_only(self):
        self.assertEqual(split_pair('EURUSD'), ('EUR', 'USD'))
        self.assertEqual(split_pair('AUDNZD'), ('AUD', 'NZD'))
        for symbol in ('XAUUSD', 'BTCUSD', 'USDTRY', 'EURCZK', 'EURUSDX', 'EUREUR'):
            self.assertIsNone(split_pair(symbol), symbol)


class HandCheckedExposureTests(TestCase):
    """EURUSD 60/40 (150 vs 50 lots @1.10) and USDJPY 70/30 (30 vs 10 lots @150)."""

    def setUp(self):
        self.result = compute_exposures([
            snap('EURUSD', '60', '40', '150', '50', '1.10'),
            snap('USDJPY', '70', '30', '30', '10', '150'),
        ])

    def test_percent_method(self):
        pct = self.result[PERCENT]
        # EURUSD net +20, USDJPY net +40
        self.assertEqual(pct['EUR'][0], Decimal('20'))
        self.assertEqual(pct['USD'][0], Decimal('10'))    # (-20 + 40) / 2
        self.assertEqual(pct['JPY'][0], Decimal('-40'))

    def test_volume_method(self):
        vol = self.result[VOLUME]
        # EURUSD: 100 net lots * 100,000 * 1.10 = 11,000,000 USD
        # USDJPY: 20 net lots * 100,000 * 1 (USD base) = 2,000,000 USD
        self.assertEqual(vol['EUR'][0], Decimal('11000000'))
        self.assertEqual(vol['USD'][0], Decimal('-9000000'))   # -11M + 2M
        self.assertEqual(vol['JPY'][0], Decimal('-2000000'))

    def test_volume_scores_sum_to_zero(self):
        self.assertEqual(sum(e.score for e in self.result[VOLUME].values()), 0)

    def test_bought_and_sold(self):
        vol = self.result[VOLUME]
        # EURUSD: longs 150 lots = 16.5M USD, shorts 50 lots = 5.5M USD
        # USDJPY: longs 30 lots = 3M USD, shorts 10 lots = 1M USD
        self.assertEqual((vol['EUR'].bought, vol['EUR'].sold), (Decimal('16500000'), Decimal('5500000')))
        # USD: bought = EURUSD shorts 5.5M + USDJPY longs 3M ; sold = EURUSD longs 16.5M + USDJPY shorts 1M
        self.assertEqual((vol['USD'].bought, vol['USD'].sold), (Decimal('8500000'), Decimal('17500000')))
        # JPY: bought = USDJPY shorts 1M ; sold = USDJPY longs 3M
        self.assertEqual((vol['JPY'].bought, vol['JPY'].sold), (Decimal('1000000'), Decimal('3000000')))

    def test_net_equals_bought_minus_sold_for_every_currency(self):
        for e in self.result[VOLUME].values():
            self.assertEqual(e.score, e.bought - e.sold)

    def test_percent_rows_have_no_gross_figures(self):
        for e in self.result[PERCENT].values():
            self.assertIsNone(e.bought)
            self.assertIsNone(e.sold)

    def test_gross_traded_and_buy_share_in_this_example(self):
        eur = self.result[VOLUME]['EUR']
        self.assertEqual(eur.bought + eur.sold, Decimal('22000000'))
        self.assertEqual(eur.bought / (eur.bought + eur.sold), Decimal('0.75'))

    def test_pairs_used_lists_contributions(self):
        usd_pairs = {p['symbol']: p['value'] for p in self.result[VOLUME]['USD'][1]}
        self.assertEqual(usd_pairs, {'EURUSD': '-11000000.0000', 'USDJPY': '2000000.0000'})

    def test_currencies_without_pairs_are_absent(self):
        self.assertNotIn('GBP', self.result[PERCENT])
        self.assertNotIn('GBP', self.result[VOLUME])


class UsdRateTests(TestCase):
    def test_inverse_pair_used_for_jpy_base_rate(self):
        # USDJPY @ 100 means 1 JPY = 0.01 USD. GBPJPY: 10 net lots long -> GBP notional uses GBPUSD.
        result = compute_exposures([
            snap('GBPUSD', '50', '50', '0', '0', '1.25'),
            snap('USDJPY', '50', '50', '0', '0', '100'),
            snap('GBPJPY', '55', '45', '20', '10', '125'),
        ])
        vol = result[VOLUME]
        # 10 net lots of GBPJPY * 100,000 * GBPUSD 1.25 = 1,250,000 USD
        self.assertEqual(vol['GBP'][0], Decimal('1250000'))
        self.assertEqual(vol['JPY'][0], Decimal('-1250000'))

    def test_pair_skipped_in_volume_when_base_rate_unknown(self):
        result = compute_exposures([snap('EURGBP', '60', '40', '10', '5', '0.86')])
        self.assertIn('EUR', result[PERCENT])
        self.assertNotIn('EUR', result[VOLUME])

    def test_weighted_price_when_long_and_short_prices_differ(self):
        s = snap('EURUSD', '50', '50', '30', '10', '1')
        s.avg_long_price, s.avg_short_price = Decimal('1.20'), Decimal('1.00')
        result = compute_exposures([s])
        # weighted price = (1.20*30 + 1.00*10) / 40 = 1.15 ; net 20 lots -> 2,300,000
        self.assertEqual(result[VOLUME]['EUR'][0], Decimal('2300000'))


class StoredFixtureTests(TestCase):
    def setUp(self):
        self.fetched_at = timezone.now()
        save_snapshots(FIXTURE['symbols'], self.fetched_at)

    def test_real_response_covers_all_majors_and_nets_to_zero(self):
        store_exposures(self.fetched_at)
        volume_rows = CurrencyExposure.objects.filter(method=VOLUME)
        self.assertEqual({r.currency for r in volume_rows}, {'USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD'})
        total = sum(r.net_score for r in volume_rows)
        # Rounding in the quantize step only; anything larger means a pair was applied one-sidedly.
        self.assertLess(abs(total), Decimal('1'))

    def test_percent_scores_stay_in_range(self):
        store_exposures(self.fetched_at)
        for row in CurrencyExposure.objects.filter(method=PERCENT):
            self.assertLessEqual(abs(row.net_score), 100)

    def test_storing_twice_does_not_duplicate(self):
        store_exposures(self.fetched_at)
        count = CurrencyExposure.objects.count()
        store_exposures(self.fetched_at)
        self.assertEqual(CurrencyExposure.objects.count(), count)

    def test_recompute_all_rebuilds_the_same_rows(self):
        store_exposures(self.fetched_at)
        before = {(r.currency, r.method): r.net_score for r in CurrencyExposure.objects.all()}
        recompute_all()
        after = {(r.currency, r.method): r.net_score for r in CurrencyExposure.objects.all()}
        self.assertEqual(before, after)
