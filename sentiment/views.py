from datetime import timedelta

from django.conf import settings
from django.db.models import Max
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import budget
from .exposure import MAJORS
from .models import CurrencyExposure, SentimentSnapshot
from .symbols import GROUPS, classify

SPARKLINE_POINTS = 24
DEFAULT_HISTORY_HOURS = 72
MAX_HISTORY_HOURS = 24 * 30
COVERAGE_DAYS = 7


def _num(value):
    return None if value is None else float(value)


def _ok(data, **extra):
    return Response({'success': True, 'data': data, **extra})


def _fail(message, code):
    return Response({'success': False, 'error': message}, status=code)


def _gap_threshold() -> timedelta:
    """A silence longer than two poll intervals is treated as a gap in the history."""
    return timedelta(minutes=settings.MYFXBOOK_POLL_INTERVAL_MINUTES * 2)


def _history_window(request):
    try:
        hours = int(request.query_params.get('hours', DEFAULT_HISTORY_HOURS))
    except ValueError:
        hours = DEFAULT_HISTORY_HOURS
    hours = min(max(hours, 1), MAX_HISTORY_HOURS)
    return timezone.now() - timedelta(hours=hours)


def _pair_item(snapshot, sparkline):
    return {
        'symbol': snapshot.symbol,
        'group': classify(snapshot.symbol),
        'fetched_at': snapshot.fetched_at,
        'long_pct': _num(snapshot.long_pct),
        'short_pct': _num(snapshot.short_pct),
        'long_volume': _num(snapshot.long_volume),
        'short_volume': _num(snapshot.short_volume),
        'long_positions': snapshot.long_positions,
        'short_positions': snapshot.short_positions,
        'avg_long_price': _num(snapshot.avg_long_price),
        'avg_short_price': _num(snapshot.avg_short_price),
        'sparkline': sparkline,
    }


class PairListView(APIView):
    """Latest long/short positioning for every pair, with a short long% sparkline."""

    def get(self, request):
        latest = SentimentSnapshot.objects.aggregate(m=Max('fetched_at'))['m']
        if latest is None:
            return _ok([], fetched_at=None)

        group = request.query_params.get('group')
        if group and group not in GROUPS:
            return _fail(f"Unknown group. Choose from: {', '.join(GROUPS)}", status.HTTP_400_BAD_REQUEST)

        timestamps = list(
            SentimentSnapshot.objects.values_list('fetched_at', flat=True)
            .distinct().order_by('-fetched_at')[:SPARKLINE_POINTS]
        )
        sparklines: dict[str, list] = {}
        recent = (
            SentimentSnapshot.objects.filter(fetched_at__in=timestamps)
            .order_by('fetched_at').values_list('symbol', 'fetched_at', 'long_pct')
        )
        for symbol, fetched_at, long_pct in recent:
            sparklines.setdefault(symbol, []).append({'t': fetched_at, 'long_pct': _num(long_pct)})

        items = [
            _pair_item(s, sparklines.get(s.symbol, []))
            for s in SentimentSnapshot.objects.filter(fetched_at=latest).order_by('symbol')
        ]
        if group:
            items = [i for i in items if i['group'] == group]
        return _ok(items, fetched_at=latest)


class PairHistoryView(APIView):
    def get(self, request, symbol):
        symbol = symbol.upper()
        rows = list(
            SentimentSnapshot.objects.for_symbol(symbol)
            .filter(fetched_at__gte=_history_window(request)).order_by('fetched_at')
        )
        if not rows and not SentimentSnapshot.objects.for_symbol(symbol).exists():
            return _fail(f"No data for {symbol}", status.HTTP_404_NOT_FOUND)
        data = [{
            'fetched_at': r.fetched_at,
            'long_pct': _num(r.long_pct),
            'short_pct': _num(r.short_pct),
            'long_volume': _num(r.long_volume),
            'short_volume': _num(r.short_volume),
            'avg_long_price': _num(r.avg_long_price),
            'avg_short_price': _num(r.avg_short_price),
        } for r in rows]
        return _ok(data, symbol=symbol, gap_threshold_minutes=_gap_threshold().total_seconds() / 60)


def _currency_item(row):
    item = {
        'currency': row.currency,
        'net': _num(row.net_score),
        'pairs': row.pairs_used,
    }
    if row.bought is not None and row.sold is not None:
        total = row.bought + row.sold
        item.update({
            'bought': _num(row.bought),
            'sold': _num(row.sold),
            'total_traded': _num(total),
            'buy_pct': _num(row.bought / total * 100) if total else None,
            'sell_pct': _num(row.sold / total * 100) if total else None,
        })
    return item


def _method(request):
    method = request.query_params.get('method', CurrencyExposure.Method.VOLUME)
    return method if method in CurrencyExposure.Method.values else None


class CurrencyListView(APIView):
    """Latest retail positioning per major currency.

    method=volume (default) adds bought / sold / total_traded / buy_pct / sell_pct in
    approximate USD notional. method=percent gives the pair-averaged long-minus-short.
    """

    def get(self, request):
        method = _method(request)
        if method is None:
            return _fail("method must be 'percent' or 'volume'", status.HTTP_400_BAD_REQUEST)
        latest = CurrencyExposure.objects.aggregate(m=Max('fetched_at'))['m']
        if latest is None:
            return _ok([], method=method, fetched_at=None)
        rows = CurrencyExposure.objects.filter(fetched_at=latest, method=method)
        items = sorted((_currency_item(r) for r in rows), key=lambda i: i['net'], reverse=True)
        return _ok(items, method=method, fetched_at=latest)


class CurrencyHistoryView(APIView):
    def get(self, request, code):
        code = code.upper()
        if code not in MAJORS:
            return _fail(f"{code} is not a supported currency", status.HTTP_404_NOT_FOUND)
        method = _method(request)
        if method is None:
            return _fail("method must be 'percent' or 'volume'", status.HTTP_400_BAD_REQUEST)
        rows = CurrencyExposure.objects.filter(
            currency=code, method=method, fetched_at__gte=_history_window(request),
        ).order_by('fetched_at')
        data = []
        for r in rows:
            point = _currency_item(r)
            point.pop('pairs')
            point['fetched_at'] = r.fetched_at
            data.append(point)
        return _ok(data, currency=code, method=method,
                   gap_threshold_minutes=_gap_threshold().total_seconds() / 60)


class StatusView(APIView):
    """Data freshness, request budget and history coverage."""

    def get(self, request):
        now = timezone.now()
        interval = settings.MYFXBOOK_POLL_INTERVAL_MINUTES
        latest = SentimentSnapshot.objects.aggregate(m=Max('fetched_at'))['m']

        since = now - timedelta(days=COVERAGE_DAYS)
        polls = list(
            SentimentSnapshot.objects.filter(fetched_at__gte=since)
            .values_list('fetched_at', flat=True).distinct().order_by('fetched_at')
        )
        expected = COVERAGE_DAYS * 24 * 60 // interval
        gaps = [
            {'from': a, 'to': b, 'minutes': round((b - a).total_seconds() / 60)}
            for a, b in zip(polls, polls[1:]) if b - a > _gap_threshold()
        ]
        age = None if latest is None else round((now - latest).total_seconds() / 60, 1)

        return _ok({
            'last_fetched_at': latest,
            'age_minutes': age,
            'stale': latest is None or (now - latest) > _gap_threshold(),
            'poll_interval_minutes': interval,
            'budget': {
                'used_24h': budget.calls_in_window(now),
                'remaining': budget.remaining(now),
                'cap': settings.MYFXBOOK_DAILY_REQUEST_CAP,
                'limit': settings.MYFXBOOK_DAILY_REQUEST_LIMIT,
            },
            'coverage': {
                'days': COVERAGE_DAYS,
                'polls': len(polls),
                'expected_polls': expected,
                'coverage_pct': round(min(len(polls) / expected, 1) * 100, 1),
                'gaps': gaps[-20:],
            },
        })
