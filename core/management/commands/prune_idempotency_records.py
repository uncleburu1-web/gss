from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import IdempotencyRecord


class Command(BaseCommand):
    help = 'Delete idempotency records older than --days (default 7). A retry never arrives that late; schedule this daily.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options['days'])
        deleted, _ = IdempotencyRecord.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(f'Deleted {deleted} idempotency record(s) older than {options["days"]} day(s).')
