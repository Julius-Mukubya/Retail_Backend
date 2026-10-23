"""Thin Myfxbook API client. Every request is budgeted via budget.reserve_call().

Exceptions raised here never contain the request URL, because the URL carries the
password (login) or the session ID.
"""
import logging

import requests
from django.conf import settings
from django.core.cache import cache

from .budget import reserve_call

logger = logging.getLogger('django')

SESSION_CACHE_KEY = 'myfxbook:session'
SESSION_CACHE_TTL = 60 * 60 * 24 * 25  # sessions last about a month; renew early
REQUEST_TIMEOUT = 30


class MyfxbookError(Exception):
    """Base error for Myfxbook API failures."""


class NetworkError(MyfxbookError):
    """Transport-level failure (timeout, connection, TLS). Safe to retry later."""


class AuthenticationError(MyfxbookError):
    """Login rejected. Retrying with the same credentials will not help."""


class InvalidSessionError(MyfxbookError):
    """The stored session is no longer valid (expired, or the IP address changed)."""


class RateLimitedError(MyfxbookError):
    """Myfxbook itself reports a usage limit."""


def _request(endpoint: str, params: dict) -> dict:
    call = reserve_call(endpoint)
    try:
        response = requests.get(
            f"{settings.MYFXBOOK_API_BASE_URL}/{endpoint}.json",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        body = response.json()
    except requests.RequestException as exc:
        call.status, call.detail = 'error', type(exc).__name__
        call.save(update_fields=['status', 'detail'])
        raise NetworkError(type(exc).__name__) from None
    except ValueError:
        call.status, call.detail = 'error', 'non-JSON response'
        call.save(update_fields=['status', 'detail'])
        raise MyfxbookError('Myfxbook returned a non-JSON response') from None

    if body.get('error'):
        message = str(body.get('message', ''))
        call.status, call.detail = 'error', message[:255]
        call.save(update_fields=['status', 'detail'])
        lowered = message.lower()
        if 'session' in lowered:
            raise InvalidSessionError(message)
        if 'limit' in lowered or 'too many' in lowered:
            raise RateLimitedError(message)
        raise MyfxbookError(message or 'Myfxbook returned an error')

    call.status = 'ok'
    call.save(update_fields=['status'])
    return body


def login() -> str:
    if not settings.MYFXBOOK_EMAIL or not settings.MYFXBOOK_PASSWORD:
        raise AuthenticationError('MYFXBOOK_EMAIL and MYFXBOOK_PASSWORD are not set')
    try:
        body = _request('login', {
            'email': settings.MYFXBOOK_EMAIL,
            'password': settings.MYFXBOOK_PASSWORD,
        })
    except (NetworkError, RateLimitedError):
        raise
    except MyfxbookError as exc:
        raise AuthenticationError(str(exc)) from None
    session = body.get('session')
    if not session:
        raise AuthenticationError('Login succeeded but no session was returned')
    cache.set(SESSION_CACHE_KEY, session, SESSION_CACHE_TTL)
    return session


def get_session() -> str:
    return cache.get(SESSION_CACHE_KEY) or login()


def fetch_community_outlook() -> dict:
    """Return the outlook body. Re-logs in at most once if the session was rejected."""
    session = get_session()
    try:
        return _request('get-community-outlook', {'session': session})
    except InvalidSessionError:
        logger.info('[myfxbook] session rejected, logging in again')
        cache.delete(SESSION_CACHE_KEY)
        session = login()
        return _request('get-community-outlook', {'session': session})
