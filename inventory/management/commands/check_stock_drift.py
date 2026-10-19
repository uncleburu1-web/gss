"""python manage.py check_stock_drift [--shop <uuid>] [--json]

Nightly check that the stock ledger explains every batch: for each live batch the sum of its
movements must equal quantity_remaining (and a deleted batch must net to zero). Read-only -- it
reports drift, it never changes stock. Exit status 1 when anything drifted, so a scheduler or
uptime monitor can alert on it.
"""
import json
import logging

from django.core.management.base import BaseCommand

from core.models import Shop
from inventory.ledger import drift

logger = logging.getLogger('benchline.stock')


class Command(BaseCommand):
    help = 'Report batches whose stock ledger does not add up to their quantity on hand.'

    def add_arguments(self, parser):
        parser.add_argument('--shop', help='Only check this branch (id).')
        parser.add_argument('--json', action='store_true', help='Print the findings as JSON.')

    def handle(self, *args, **options):
        shop = Shop.objects.get(id=options['shop']) if options.get('shop') else None
        problems = drift(shop)
        if options.get('json'):
            self.stdout.write(json.dumps(problems, indent=2))
        elif not problems:
            self.stdout.write(self.style.SUCCESS('Stock ledger matches every batch.'))
        else:
            for p in problems:
                self.stdout.write(
                    f"{p['item_name']} (batch {p['batch_id']}, shop {p['shop_id']}): on hand "
                    f"{p['quantity_remaining']}, ledger {p['ledger_total']}, difference {p['difference']}"
                )
        if problems:
            logger.warning('stock drift: %d batch(es) do not match their ledger', len(problems))
            raise SystemExit(1)
