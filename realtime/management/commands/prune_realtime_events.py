from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from realtime.models import RealtimeEvent


class Command(BaseCommand):
    help = (
        'Deletes RealtimeEvent rows older than --days (default 14). Safe to run on a schedule '
        '(e.g. a daily cron / Railway scheduled job) -- these rows only exist so a client can '
        'detect and catch up on a missed event; nothing needs one older than a client could '
        'plausibly have been offline for. This never touches actual business data (Sale, '
        'StockBatch, etc.) -- only this notification log.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=14)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options['days'])
        deleted, _ = RealtimeEvent.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {deleted} realtime event(s) older than {options["days"]} day(s).'
        ))
