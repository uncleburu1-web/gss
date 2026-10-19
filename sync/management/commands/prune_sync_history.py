"""python manage.py prune_sync_history [--days 90] [--dry-run]

Retention (decided 2026-10): SyncOperation and RealtimeEvent keep 90 days; AuditLog and the stock
ledger are kept forever and are never touched here.

Never deletes a row that is still needed:
  * SyncOperation is the idempotency record that stops a re-sent till operation from being applied
    twice. A row is only pruned once the till that sent it has synced successfully at least a day
    AFTER it (so that till has had its answer), or the device is gone / unknown. A till that has
    been offline for months keeps every one of its records until it comes back and syncs.
  * RealtimeEvent: the newest event of every shop is always kept (it anchors the sequence clients
    compare against), whatever its age.
Deletes in small batches so a big backlog never holds long locks.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import F, Max, Q
from django.utils import timezone

from realtime.models import RealtimeEvent
from sync.models import SyncOperation

BATCH = 2000
ACK_MARGIN = timedelta(days=1)


def prunable_sync_operations(cutoff):
    old = SyncOperation.objects.filter(applied_at__lt=cutoff)
    return old.filter(
        Q(device__isnull=True)
        | Q(device__is_deleted=True)
        | Q(device__last_sync_at__gt=F('applied_at') + ACK_MARGIN)
    )


def prunable_realtime_events(cutoff):
    newest = RealtimeEvent.objects.values('shop_id').annotate(m=Max('sequence'))
    keep = Q()
    for row in newest:
        keep |= Q(shop_id=row['shop_id'], sequence=row['m'])
    qs = RealtimeEvent.objects.filter(created_at__lt=cutoff)
    return qs.exclude(keep) if newest else qs


def delete_in_batches(qs):
    total = 0
    while True:
        ids = list(qs.values_list('pk', flat=True)[:BATCH])
        if not ids:
            return total
        deleted, _ = qs.model.objects.filter(pk__in=ids).delete()
        total += deleted


class Command(BaseCommand):
    help = 'Delete sync idempotency records and realtime events older than --days (default 90), safely.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = max(options['days'], 30)  # never shorter than a month, whatever is passed
        cutoff = timezone.now() - timedelta(days=days)
        ops = prunable_sync_operations(cutoff)
        events = prunable_realtime_events(cutoff)
        if options['dry_run']:
            self.stdout.write(f'Would delete {ops.count()} sync operation(s) and {events.count()} realtime event(s) '
                              f'older than {days} days.')
            return
        n_ops = delete_in_batches(ops)
        n_events = delete_in_batches(events)
        self.stdout.write(self.style.SUCCESS(
            f'Deleted {n_ops} sync operation(s) and {n_events} realtime event(s) older than {days} days.'
        ))
