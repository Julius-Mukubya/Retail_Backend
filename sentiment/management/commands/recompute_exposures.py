from django.core.management.base import BaseCommand

from sentiment.exposure import recompute_all


class Command(BaseCommand):
    help = 'Rebuild all currency exposures from stored snapshots. Makes no Myfxbook requests.'

    def handle(self, *args, **options):
        count = recompute_all()
        self.stdout.write(f"Rebuilt {count} exposure rows.")
