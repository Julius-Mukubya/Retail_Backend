from celery import shared_task
from celery.utils.log import get_task_logger

from .myfxbook import NetworkError
from .services import PollInProgressError, poll_once

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    autoretry_for=(NetworkError,),  # only transport failures; auth, budget and API errors fail fast
    retry_backoff=60,
    retry_backoff_max=900,
    retry_jitter=True,
    max_retries=3,
)
def poll_community_outlook_task(self) -> dict:
    """Scheduled poll of the Myfxbook community outlook.

    Retries stay inside the request budget because every attempt goes through
    budget.reserve_call(); a BudgetExceededError is not retried.
    """
    try:
        result = poll_once()
    except PollInProgressError:
        logger.warning('[poll_community_outlook_task] another poll is running, skipping')
        return {'skipped': 'poll in progress'}
    logger.info(f"[poll_community_outlook_task] {result}")
    return result
