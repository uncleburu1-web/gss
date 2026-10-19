"""python manage.py nightly_maintenance

The one command to schedule once a day (e.g. a Railway cron service running this at 03:00 Lagos).
Runs, in order, and keeps going if one step fails:

  1. prune_sync_history        SyncOperation + RealtimeEvent, 90 days, never what is still needed
  2. prune_idempotency_records web/Android Idempotency-Key records, 7 days
  3. check_stock_drift         report (never fix) any batch whose ledger disagrees with its stock

AuditLog and the stock ledger are kept forever; nothing here deletes them.
Exit status 1 if any step failed or stock drift was found, so the scheduler can alert.
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Daily retention and integrity checks (safe to run any time).'

    def handle(self, *args, **options):
        failed = []
        for name in ('prune_sync_history', 'prune_idempotency_records', 'check_stock_drift'):
            try:
                call_command(name, stdout=self.stdout, stderr=self.stderr)
            except SystemExit as exc:
                if exc.code:
                    failed.append(name)
            except Exception as exc:  # one broken step must not stop the others
                self.stderr.write(f'{name} failed: {exc}')
                failed.append(name)
        if failed:
            self.stderr.write(f'Needs attention: {", ".join(failed)}')
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS('Nightly maintenance finished.'))
