"""Request budget for the Myfxbook API (free tier: 100 requests per rolling 24 hours).

Every outbound request is written to ApiCallLog *before* it is sent, so a crash or a
failed request still counts. Callers must go through reserve_call(); nothing else may
talk to Myfxbook. Polls are serialised by the poll lock in services.py, so the
check-then-insert here does not need its own lock.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import ApiCallLog

WINDOW = timedelta(hours=24)


class BudgetExceededError(Exception):
    """Raised instead of sending a request that would pass the configured cap."""


def calls_in_window(now=None) -> int:
    now = now or timezone.now()
    return ApiCallLog.objects.filter(called_at__gt=now - WINDOW).count()


def remaining(now=None) -> int:
    return max(settings.MYFXBOOK_DAILY_REQUEST_CAP - calls_in_window(now), 0)


def reserve_call(endpoint: str) -> ApiCallLog:
    now = timezone.now()
    used = calls_in_window(now)
    if used >= settings.MYFXBOOK_DAILY_REQUEST_CAP:
        raise BudgetExceededError(
            f"Myfxbook budget exhausted: {used}/{settings.MYFXBOOK_DAILY_REQUEST_CAP} "
            f"requests used in the last 24 hours"
        )
    return ApiCallLog.objects.create(called_at=now, endpoint=endpoint, status='pending')
