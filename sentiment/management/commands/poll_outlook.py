import time

from django.core.management.base import BaseCommand, CommandError

from sentiment.budget import BudgetExceededError, calls_in_window, remaining
from sentiment.myfxbook import MyfxbookError
from sentiment.services import PollInProgressError, poll_once


class Command(BaseCommand):
    help = (
        'Poll the Myfxbook community outlook once, or repeatedly with --loop. '
        'Works without Redis or Celery. Always stays within the request budget.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--loop', action='store_true', help='Keep polling until interrupted.')
        parser.add_argument('--interval', type=int, default=30, help='Minutes between polls (default 30).')

    def handle(self, *args, **options):
        if not options['loop']:
            self._poll_and_report(raise_errors=True)
            return
        interval = max(options['interval'], 1) * 60
        self.stdout.write(f"Polling every {options['interval']} min. Ctrl+C to stop.")
        try:
            while True:
                self._poll_and_report(raise_errors=False)
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write('Stopped.')

    def _poll_and_report(self, raise_errors: bool):
        try:
            result = poll_once()
        except (MyfxbookError, BudgetExceededError, PollInProgressError) as exc:
            if raise_errors:
                raise CommandError(str(exc))
            self.stderr.write(f"Poll failed: {exc}")
            return
        self.stdout.write(
            f"{result['fetched_at']}: stored {result['symbols_saved']} symbols "
            f"({calls_in_window()} requests used, {remaining()} left in 24h)"
        )
