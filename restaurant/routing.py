"""Kitchen routing: which station makes which line, and which tickets a "send" creates.

Pure functions with no database access, written identically in JavaScript for the hub
(gssdesktop/src/main/restaurant/routing.js) and kept in step by the shared cases in
routing_vectors.json (ADR-018). Design: docs/restaurant/KITCHEN_WORKFLOW.md §1.

Rules:
  * A line goes to its item's station if that station is active, otherwise to its category's
    station if active, otherwise nowhere ("unrouted"). Unrouted lines are reported, never silently
    dropped, so the hub can warn "Jollof has no active kitchen station".
  * Only lines still PENDING are routed. A line already sent is never routed again: retrying a send
    after a network drop cannot create a duplicate ticket.
  * One ticket per station per send. Tickets come in the order their station first appears among the
    lines; items keep the order of the lines. The same input always gives the same output.
"""


def station_for(item_station_id, category_station_id, active_station_ids):
    active = set(active_station_ids or [])
    if item_station_id and item_station_id in active:
        return item_station_id
    if category_station_id and category_station_id in active:
        return category_station_id
    return None


def route(lines):
    """lines: [{"id", "station_id" (or None), "state" (default "pending"), ...}]
    -> {"tickets": [{"station_id", "items": [line id, ...]}], "unrouted": [line id, ...], "skipped": [line id, ...]}"""
    tickets, by_station, unrouted, skipped = [], {}, [], []
    for line in lines:
        if line.get('state', 'pending') != 'pending':
            skipped.append(line['id'])
            continue
        station = line.get('station_id')
        if not station:
            unrouted.append(line['id'])
            continue
        if station not in by_station:
            by_station[station] = {'station_id': station, 'items': []}
            tickets.append(by_station[station])
        by_station[station]['items'].append(line['id'])
    return {'tickets': tickets, 'unrouted': unrouted, 'skipped': skipped}
