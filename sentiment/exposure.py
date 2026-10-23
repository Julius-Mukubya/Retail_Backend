"""Retail net exposure per currency, derived from pair-level Myfxbook snapshots.

A retail long on BASE/QUOTE is long BASE and short QUOTE, so a pair's net position is
added to the base currency and subtracted from the quote currency. Only pairs made of
two major currencies are used; metals, crypto and exotics are ignored.

Two methods:
  percent  net = long% - short% per pair, averaged over the pairs each currency appears
           in. Range -100..100. Treats a thin pair the same as EURUSD.
  volume   net lots * 100,000 * (base currency -> USD rate), summed over pairs. This is
           USD notional, weighted by how much is actually traded. Because each pair adds
           the same amount to one currency and subtracts it from another, the scores
           across all currencies sum to zero. The volume rows also carry gross figures:
           "bought" (longs where the currency is the base, shorts where it is the quote)
           and "sold" (the reverse). score = bought - sold; bought + sold is how heavily
           the currency is traded, and bought / (bought + sold) is its buy share.

The base -> USD rate is estimated from the volume-weighted average entry price in the
same snapshot. That is an entry-price average, not live spot, so notionals are
approximate (typically within a few percent).
"""
from decimal import Decimal
from typing import NamedTuple, Optional

from .models import CurrencyExposure, SentimentSnapshot

MAJORS = ('USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD')
LOT_SIZE = Decimal('100000')
SCORE_PLACES = Decimal('0.0001')


class Exposure(NamedTuple):
    score: Decimal
    pairs: list
    bought: Optional[Decimal] = None  # volume method only
    sold: Optional[Decimal] = None


def split_pair(symbol: str):
    """Return (base, quote) for a pair of two majors, otherwise None."""
    if len(symbol) != 6:
        return None
    base, quote = symbol[:3], symbol[3:]
    if base in MAJORS and quote in MAJORS and base != quote:
        return base, quote
    return None


def pair_price(snapshot):
    """Volume-weighted average of the long and short entry prices."""
    long_price, short_price = snapshot.avg_long_price, snapshot.avg_short_price
    if long_price is None and short_price is None:
        return None
    if long_price is None:
        return short_price
    if short_price is None:
        return long_price
    total = snapshot.long_volume + snapshot.short_volume
    if total <= 0:
        return (long_price + short_price) / 2
    return (long_price * snapshot.long_volume + short_price * snapshot.short_volume) / total


def usd_rates(by_symbol: dict) -> dict:
    """Map each major to its value in USD, using whichever USD pair is available."""
    rates = {'USD': Decimal(1)}
    for currency in MAJORS:
        if currency == 'USD':
            continue
        direct = by_symbol.get(f'{currency}USD')
        inverse = by_symbol.get(f'USD{currency}')
        price = pair_price(direct) if direct else None
        if price:
            rates[currency] = price
            continue
        price = pair_price(inverse) if inverse else None
        if price:
            rates[currency] = 1 / price
    return rates


def compute_exposures(snapshots) -> dict:
    """Return {method: {currency: Exposure}} for one poll's snapshots."""
    by_symbol = {s.symbol: s for s in snapshots}
    rates = usd_rates(by_symbol)

    percent_parts = {c: [] for c in MAJORS}
    volume_parts = {c: [] for c in MAJORS}
    bought = {c: Decimal(0) for c in MAJORS}
    sold = {c: Decimal(0) for c in MAJORS}

    for symbol, snap in sorted(by_symbol.items()):
        pair = split_pair(symbol)
        if pair is None:
            continue
        base, quote = pair

        net_pct = snap.long_pct - snap.short_pct
        percent_parts[base].append((symbol, net_pct))
        percent_parts[quote].append((symbol, -net_pct))

        base_rate = rates.get(base)
        if base_rate is None:
            continue
        long_usd = snap.long_volume * LOT_SIZE * base_rate
        short_usd = snap.short_volume * LOT_SIZE * base_rate
        net_usd = long_usd - short_usd
        volume_parts[base].append((symbol, net_usd))
        volume_parts[quote].append((symbol, -net_usd))
        # A long buys the base and sells the quote; a short does the opposite.
        bought[base] += long_usd
        sold[quote] += long_usd
        sold[base] += short_usd
        bought[quote] += short_usd

    result = {CurrencyExposure.Method.PERCENT: {}, CurrencyExposure.Method.VOLUME: {}}
    for currency in MAJORS:
        parts = percent_parts[currency]
        if parts:
            score = sum(v for _, v in parts) / len(parts)
            result[CurrencyExposure.Method.PERCENT][currency] = Exposure(
                score.quantize(SCORE_PLACES), _contributions(parts))
        parts = volume_parts[currency]
        if parts:
            score = sum(v for _, v in parts)
            result[CurrencyExposure.Method.VOLUME][currency] = Exposure(
                score.quantize(SCORE_PLACES), _contributions(parts),
                bought[currency].quantize(SCORE_PLACES), sold[currency].quantize(SCORE_PLACES))
    return result


def _contributions(parts) -> list:
    return [{'symbol': symbol, 'value': str(value.quantize(SCORE_PLACES))} for symbol, value in parts]


def store_exposures(fetched_at) -> int:
    snapshots = list(SentimentSnapshot.objects.filter(fetched_at=fetched_at))
    rows = [
        CurrencyExposure(
            fetched_at=fetched_at, currency=currency, method=method,
            net_score=e.score, pairs_used=e.pairs, bought=e.bought, sold=e.sold,
        )
        for method, per_currency in compute_exposures(snapshots).items()
        for currency, e in per_currency.items()
    ]
    CurrencyExposure.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def recompute_all() -> int:
    """Rebuild every stored exposure from the snapshots (use after changing the formulas)."""
    CurrencyExposure.objects.all().delete()
    timestamps = SentimentSnapshot.objects.values_list('fetched_at', flat=True).distinct()
    return sum(store_exposures(ts) for ts in timestamps)
