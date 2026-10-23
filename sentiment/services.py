import logging
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from .exposure import store_exposures
from .models import SentimentSnapshot
from .myfxbook import fetch_community_outlook

logger = logging.getLogger('django')

POLL_LOCK_KEY = 'myfxbook:poll-lock'
POLL_LOCK_TTL = 300  # seconds; expires on its own if a worker dies mid-poll


class PollInProgressError(Exception):
    """Another poll already holds the lock."""


def _dec(value) -> Decimal:
    return Decimal(str(value))


def save_snapshots(symbols: list[dict], fetched_at) -> int:
    rows = []
    for item in symbols:
        try:
            rows.append(SentimentSnapshot(
                fetched_at=fetched_at,
                symbol=item['name'].upper(),
                long_pct=_dec(item['longPercentage']),
                short_pct=_dec(item['shortPercentage']),
                long_volume=_dec(item['longVolume']),
                short_volume=_dec(item['shortVolume']),
                long_positions=int(item['longPositions']),
                short_positions=int(item['shortPositions']),
                avg_long_price=_dec(item['avgLongPrice']) if item.get('avgLongPrice') is not None else None,
                avg_short_price=_dec(item['avgShortPrice']) if item.get('avgShortPrice') is not None else None,
                raw_data=item,
            ))
        except (KeyError, ValueError, ArithmeticError, AttributeError) as exc:
            logger.warning(f"[save_snapshots] skipping malformed symbol entry: {type(exc).__name__}")
    with transaction.atomic():
        SentimentSnapshot.objects.bulk_create(rows, ignore_conflicts=True)
    return len(rows)


def poll_once() -> dict:
    """Fetch the outlook once and store it. Only one poll may run at a time."""
    if not cache.add(POLL_LOCK_KEY, 1, POLL_LOCK_TTL):
        raise PollInProgressError('A Myfxbook poll is already running')
    try:
        body = fetch_community_outlook()
        fetched_at = timezone.now()
        saved = save_snapshots(body.get('symbols', []), fetched_at)
        try:
            exposures = store_exposures(fetched_at)
        except Exception:
            # The snapshots are the source of truth; exposures can be rebuilt with recompute_exposures.
            logger.exception('[poll_once] failed to compute currency exposures')
            exposures = 0
        logger.info(f"[poll_once] stored {saved} symbols, {exposures} exposures at {fetched_at.isoformat()}")
        return {'fetched_at': fetched_at.isoformat(), 'symbols_saved': saved, 'exposures_saved': exposures}
    finally:
        cache.delete(POLL_LOCK_KEY)
