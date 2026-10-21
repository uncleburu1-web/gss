# Offline operation and synchronisation

How the restaurant keeps working without internet, and how data moves between kitchen screens, the
hub till and the cloud without duplicates or conflicts. Architecture and the reasons for a hub:
[`TARGET_ARCHITECTURE.md`](../architecture/TARGET_ARCHITECTURE.md#2-the-restaurant-runs-on-a-hub-till-on-the-shops-wi-fi).
Status: **design, awaiting review.**

## 1. Three links, three rules

| Link | Transport | Rule |
| --- | --- | --- |
| Kitchen screen ↔ hub | HTTP commands + Server-Sent Events on the Wi-Fi | The screen holds nothing of record; it shows the hub's state and asks the hub to change it |
| Hub ↔ cloud | The existing sync protocol (outbox push, cursor pull), version **6** | Config flows down; finished orders flow up as one document each |
| Hub ↔ hub | Not allowed | One hub per branch (`RestaurantSettings.hub_device`); the cloud refuses restaurant ops from any other device |

## 2. What works with the internet down

Everything in the first release: opening orders, adding items, sending to the kitchen (print and
screen), kitchen status, voids, bills, all payment methods that do not need the internet (cash;
card/transfer/mobile money are recorded as the cashier confirms them, as on supermarket tills),
closing orders, shifts and Z-reports.

What needs the internet: changing the menu, tables, stations or taxes (edited in the cloud); the
CEO's live view from elsewhere; uploading finished orders (they queue meanwhile).

What needs the **hub**: everything. If the hub PC is off, orders cannot be taken on the system
(mitigations in TARGET_ARCHITECTURE §2).

## 3. Kitchen screens catching up

Every change on the hub writes an event row in the **same transaction** as the change (a
transactional outbox, fixing gap R1 for the restaurant):

```text
hub_events(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, type TEXT, station_ids TEXT,
           entity_type TEXT, entity_id TEXT, data TEXT, at TEXT)
```

- The kitchen screen opens `GET /hub/v1/events` (Server-Sent Events). Each event carries its `seq`
  as the SSE `id`.
- When the connection drops, the browser reconnects **by itself** and sends `Last-Event-ID`. The
  hub replays every event after that number that concerns the screen's stations, then continues
  live.
- If the screen was away longer than the event log keeps (7 days), or the hub's log was reset, the
  hub sends `resync` and the screen reloads the full current state with `GET /hub/v1/kds/state`.
- Events only say *what changed*. The screen always applies the latest full state of the ticket the
  event names, so receiving an event twice is harmless.

## 4. Hub ↔ cloud

### Down: configuration

New pull entity types: `restaurant_settings`, `dining_area`, `restaurant_table`, `kitchen_station`,
`menu_category`, `menu_item`, `modifier_group`, `modifier_option`, `menu_item_modifier_group`,
`tax_rule`. Same cursor, overlap and paging rules as products today. A change made in the cloud
while an order is open never alters that order (prices and rates are snapshotted).

### Up: orders

| Op | When | Contents | Applied in the cloud |
| --- | --- | --- | --- |
| `order_doc` | When an order **closes or is cancelled** | The order, its items and modifiers, voids (with waste flag), tickets with their timestamps, the bill with its charges, all payments | In **one** Postgres transaction: recompute the bill with the Python tax engine and **reject a mismatch**; write the restaurant rows; write a `Sale` (`source='restaurant'`, linked to the order) with lines by name, tax and payments, so reports and Z-reports include it; audit entries |
| `order_snapshot` | After each send / payment while an order is open, when online | Same shape, flagged *open* | Stored as a non-authoritative mirror (CEO's live view, recovery if the hub PC is lost). Replaced by the `order_doc` when it arrives |
| `menu_availability` | Sold out / back on | Item id, flag, who, when | Updates the cloud copy; audited |
| `audit_event`, `shift`, `cash_movement` | As today | | As today |

### Duplicates and conflicts

- **Ids:** every row is created on the hub with a UUID and keeps it in the cloud. No temporary ids.
- **Idempotency:** each op's UUID becomes the `SyncOperation` id; a resend returns
  `already_applied`; the same id with different content is rejected (`payload_hash`), as today.
- **One writer:** only the branch's hub may send restaurant ops (device → branch → `hub_device`), so
  there is no second copy of an open order to conflict with.
- **Cloud config vs hub sold-out switch:** the hub's flag wins during the business day; the cloud
  records it and shows who switched it.
- **Partial upload:** an `order_doc` is all or nothing in the cloud. If 30 orders are queued, each
  is its own op; a failure on one does not block the others (same rule as `sale_doc`).

### Clocks

Order numbers, ticket numbers and the business day come from the hub's own counters and its
configured time zone (`Shop.timezone`), not from comparing clocks between machines. The cloud stores
the hub's timestamps as "when it happened" and its own `applied_at` as "when it arrived"; it never
orders data by device clocks.

## 5. Failure scenarios

| Scenario | What happens |
| --- | --- |
| Internet down all evening | Restaurant runs normally; finished orders queue; they upload once each when the internet returns |
| Hub loses power mid-order | On restart, every committed change is there (WAL); print jobs not confirmed are flagged for reprint; screens reconnect |
| Kitchen screen loses Wi-Fi for 2 minutes | Reconnects automatically; replays the events it missed; no refresh needed |
| Cloud accepts an `order_doc` but the reply is lost | Resend → `already_applied`; no duplicate order or sale |
| Totals disagree between hub and cloud (bug or tampering) | Op `rejected` with the reason; shows as a failed sync on the hub; the order stays safe on the hub; audited |
| Subscription expires | Cloud answers 402; the hub keeps running the restaurant and queues orders ("paused"), as tills do |
| Hub PC destroyed | Open orders mirrored by the last `order_snapshot` can be restored onto a new hub; anything after the last upload is lost. Hence UPS and staying online when possible |
| Someone tries a second hub | Cloud refuses its restaurant ops (`not_the_hub`); the owner must reassign the hub in settings |

## 6. Security on the Wi-Fi

- The local server listens only while the branch has the restaurant module and kitchen screens are
  enabled.
- Every request needs either a signed-in staff session on the hub UI or a **paired screen token**
  bound to the branch and its stations. Unknown or revoked tokens get 401; a screen acting outside
  its stations gets 403.
- Pairing codes expire after 10 minutes and work once. Too many wrong codes lock pairing for a while.
- Tokens are stored hashed on the hub. Screens hold no customer data beyond the open tickets.
- First release uses plain HTTP inside the shop's Wi-Fi (a router password is the outer wall).
  Customer payment card data never touches the system. HTTPS on the LAN is designed with waiter
  phones in step 2.
