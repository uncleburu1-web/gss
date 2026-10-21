# Real-time events

One event shape for both places events are sent: the **hub** (Server-Sent Events to kitchen screens
and the hub UI, inside the restaurant) and the **cloud** (the existing WebSocket `/ws/live/`, to the
web and phone apps). Events say what changed; the database is the record and clients re-read state
over HTTP. Status: **design, awaiting review.**

## 1. Envelope

```json
{
  "id": "5f0c3e1e-8a8a-4f43-9d0b-0c1d2e3f4a5b",
  "seq": 1842,
  "type": "ticket.item_changed",
  "schema": 1,
  "organization_id": "…",
  "branch_id": "…",
  "entity": { "type": "kitchen_ticket_item", "id": "…" },
  "at": "2026-10-21T12:41:07.120+01:00",
  "data": { "state": "ready", "version": 4, "station_id": "…", "order_id": "…" }
}
```

| Field | Meaning |
| --- | --- |
| `id` | Unique event id (UUID) |
| `seq` | Gap-free per branch on the hub (`hub_events.seq`) or per shop in the cloud (`Shop.last_event_sequence`); a client that sees a gap knows it missed something |
| `type` | From the lists below |
| `schema` | Version of this event's `data` shape; a client ignores versions it does not know and re-reads state |
| `organization_id`, `branch_id` | Scope; the server also enforces it (section 3) |
| `entity` | What changed |
| `at` | When it happened, by the clock of the machine that made the change |
| `data` | Small hints only (new state, version, ids). Never prices, never customer details |

## 2. Event types

### On the hub (kitchen screens and hub UI)

| Type | When (transition) | Sent to |
| --- | --- | --- |
| `order.opened`, `order.updated`, `order.cancelled`, `order.closed` | T1, T2/T5/T6/T9, T12, T11 | Hub UI |
| `order.ready` | All items of an order ready (T4) | Hub UI (waiter "ready" badge) |
| `ticket.created` | T3 | Screens of that station, hub UI |
| `ticket.item_changed` | T4, T5 | Screens of that station, hub UI |
| `ticket.voided` | T6, T12 | Screens of that station, hub UI |
| `table.changed` | T1, T7, T8, T11, T12 | Hub UI |
| `bill.printed`, `payment.received` | T8, T10 | Hub UI |
| `menu.availability` | T13 | Hub UI, screens |
| `print.failed` | A print job reports failure | Hub UI |
| `resync` | The client is too far behind | That client only |

### In the cloud (web and phone)

| Type | When |
| --- | --- |
| `restaurant.order_closed` | An `order_doc` is applied (one event per push batch, as `sync.applied` today) |
| `restaurant.order_mirror` | An `order_snapshot` is stored (CEO live view) |
| `restaurant.config_changed` | Menu, tables, stations or taxes change; the hub pulls on receiving it |
| `restaurant.availability` | Sold-out switch uploaded |

## 3. Who receives what

- **Hub:** a kitchen screen only receives events whose `station_ids` include one of its paired
  stations; the hub filters before sending, not the browser. The hub UI receives all events for its
  branch.
- **Cloud:** unchanged rule: a socket joins one group per branch the signed-in user may see
  (`realtime/consumers.py`). Restaurant events go to the branch group only, so another business, or
  a branch the user does not belong to, never receives them.
- No event contains data the receiver could not already read over HTTP.

## 4. Reliability

- Hub events are written in the **same transaction** as the change (transactional outbox), then
  sent. Restaurant events in the cloud are written the same way (ADR-017), fixing gap R1 for them.
- Recovery: SSE `Last-Event-ID` on the hub; `hello` + sequence comparison in the cloud (existing).
- Retention: hub events 7 days; cloud events 90 days (existing rule).
