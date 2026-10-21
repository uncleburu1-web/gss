# Restaurant API specification

Two APIs: the **cloud API** (Django, `/api/restaurant/…`) for configuration, reports and sync, and
the **hub API** (inside the hub till, `http://<hub>:7700/hub/v1/…`) for taking and running orders in
the building. Rules: [`REQUIREMENTS.md`](REQUIREMENTS.md); transitions T1–T13:
[`ORDER_LIFECYCLE.md`](ORDER_LIFECYCLE.md). Status: **design, awaiting review.**

## Conventions (both APIs)

| Topic | Rule |
| --- | --- |
| Errors | JSON `{"detail": "<plain-English message>", "code": "<machine code>"}`; 400 validation, 401 not signed in, 402 subscription ended (cloud), 403 not allowed / `module_disabled` / `not_the_hub`, 404 not found **or not yours** (never reveals another business's ids), 409 conflict (state changed, table taken, version mismatch) |
| Idempotency | Every create or state change accepts `Idempotency-Key` (UUID). Same key + same body → the first response again; same key + different body → 409 `key_reused` (existing `core/idempotency.py` rule) |
| Tenancy | Cloud: branch resolved by `get_shop_for_user` (+ `X-Branch-ID` for a CEO); hub: the hub serves exactly one branch. Every object id is looked up **within** that branch |
| Module gate | Cloud restaurant endpoints and sync handlers require `RequiresModule('restaurant')` on the branch |
| Money / quantities | Strings with fixed decimals in responses (`"4800.00"`, `"2.000"`); numbers accepted in requests |
| Not exposed | No generic "update any field" endpoint for orders, bills or payments; only the named business operations below |

## 1. Cloud API

### Configuration (owner / branch manager / CEO; audited `restaurant.config.changed`)

Standard list / create / update / deactivate on each resource. Deleting is a soft deactivate so past
orders keep their references.

| Method | Path | Purpose | Validation highlights |
| --- | --- | --- | --- |
| GET, PUT | `/api/restaurant/settings/` | Branch settings: service charge, R10 switch, order prefix | `0 ≤ service_charge_rate ≤ 100` |
| PUT | `/api/restaurant/settings/hub/` | Make a paired till this branch's hub | The device must be a desktop paired to this branch; replaces the previous hub (audited) |
| GET, POST, PATCH | `/api/restaurant/areas/`, `/tables/`, `/stations/` | Floor and kitchen setup | Unique names / labels per branch; a station must print, show, or both |
| GET, POST, PATCH | `/api/restaurant/menu/categories/`, `/menu/items/` | Menu | `price ≥ 0`; station and category must be in the same branch |
| GET, POST, PATCH | `/api/restaurant/menu/modifier-groups/`, `/menu/modifier-options/` | Modifiers | `0 ≤ min_select ≤ max_select` |
| GET, POST, PATCH | `/api/restaurant/tax-rules/` | Taxes | `0 ≤ rate ≤ 100`; `valid_to > valid_from`; overlapping rules with the same name refused |
| POST | `/api/restaurant/tax-rules/preview/` | Compute a sample bill with the current rules (no write) | Same engine as the hub; used by the settings screen to show the owner the effect |

### Reading orders and reports (capability `view_all_sales` or own orders, as for sales today)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/restaurant/orders/?status=&date=&table=` | Closed / cancelled orders, plus mirrored open ones (flagged) |
| GET | `/api/restaurant/orders/{id}/` | One order with items, voids, tickets, bill, charges, payments |
| GET | `/api/reports/restaurant/summary/?from=&to=` | Covers, average bill, takings by service type, service charge, taxes by rule, voids and waste, kitchen times by station |

### Sync (hub only; existing endpoints, protocol 6)

| Method | Path | Change |
| --- | --- | --- |
| POST | `/api/sync/push/` | New op types `order_doc`, `order_snapshot`, `menu_availability`. Each applied in one transaction; `order_doc` totals recomputed and compared; ops from a device that is not the branch hub → `rejected` `not_the_hub` |
| GET | `/api/sync/pull/` | New entity types listed in [`OFFLINE_SYNCHRONIZATION.md`](OFFLINE_SYNCHRONIZATION.md#4-hub--cloud). Returned only when the branch has the restaurant module |

`order_doc` payload (abridged):

```json
{
  "id": "order uuid", "number": "K-0042", "service_type": "dine_in", "table_id": "…",
  "status": "closed", "opened_at": "…", "closed_at": "…", "business_date": "2026-10-21",
  "waiter_id": "…", "guests": 4,
  "items": [{ "id": "…", "menu_item_id": "…", "name": "Jollof rice", "unit_price": "3500.00",
              "quantity": "2.000", "state": "served", "voided_quantity": "0.000", "waste": false,
              "modifiers": [{ "option_id": "…", "name": "Large", "price_delta": "500.00" }] }],
  "tickets": [{ "id": "…", "station_id": "…", "number": "K-118", "kind": "new", "sent_at": "…",
                "items": [{ "id": "…", "order_item_id": "…", "quantity": "2.000", "state": "served",
                            "started_at": "…", "ready_at": "…", "served_at": "…" }] }],
  "bill": { "id": "…", "subtotal": "8000.00", "discount": "0.00", "total": "9400.00",
            "charges": [{ "kind": "service_charge", "name": "Service", "rate": "10.0000", "base": "8000.00", "amount": "800.00" },
                        { "kind": "tax", "tax_rule_id": "…", "name": "VAT", "rate": "7.5000", "inclusive": false, "base": "8000.00", "amount": "600.00" }],
            "payments": [{ "id": "…", "method": "cash", "amount": "9400.00", "tendered": "10000.00", "change": "600.00", "tip": "0.00", "shift_ref": "…", "received_at": "…" }] }
}
```

Rejections an `order_doc` can get: `bill_mismatch` (recomputed total differs), `unknown_menu_item`
(not in this branch), `bill_not_settled` (closed without full payment), `not_the_hub`,
`module_disabled`.

## 2. Hub API (on the shop's Wi-Fi)

Auth: **staff** = signed in on the hub UI (same accounts as the till, offline sign-in included);
**screen** = paired kitchen screen token (`Authorization: Bearer <screen token>`).

| Method | Path | Who | Purpose (transition) | Notes |
| --- | --- | --- | --- | --- |
| POST | `/hub/v1/pair` | screen | Exchange a pairing code for a screen token | Code single-use, 10-minute expiry, rate-limited |
| GET | `/hub/v1/kds/state` | screen | Full current tickets for its stations | Used on first load and after `resync` |
| GET | `/hub/v1/events` | screen, staff | Server-Sent Events stream (`Last-Event-ID` resumes) | Filtered by station for screens |
| POST | `/hub/v1/ticket-items/{id}/state` | screen (`kitchen`), staff | T4 / T5: `{"to": "preparing", "version": 3}` | 409 `version_mismatch` with current state; 403 if not this screen's station |
| POST | `/hub/v1/tickets/{id}/bump` | screen | Mark every item on the ticket ready | Same version rules per item |
| POST | `/hub/v1/tickets/{id}/reprint` | staff (`reprint_ticket`) | Reprint as COPY | Audited |
| POST | `/hub/v1/orders` | staff (`take_orders`) | T1 open order | 409 `table_occupied` |
| POST | `/hub/v1/orders/{id}/items` | staff (`take_orders`) | T2 add pending item(s) with modifiers | 400 `modifier_rules`, `sold_out` |
| PATCH / DELETE | `/hub/v1/orders/{id}/items/{item_id}` | staff (`take_orders`) | T2 change / remove a pending item | 409 `already_sent` |
| POST | `/hub/v1/orders/{id}/send` | staff (`send_to_kitchen`) | T3 | Idempotent: one set of tickets per key |
| POST | `/hub/v1/orders/{id}/items/{item_id}/void` | staff (`void_sent_item`) | T6 `{"quantity": 1, "reason": "…"}` | 409 `below_paid` |
| POST | `/hub/v1/orders/{id}/move` | staff (`move_table`) | T7 `{"table_id": "…"}` | 409 `table_occupied` |
| POST | `/hub/v1/orders/{id}/bill` | staff (`take_payment`) | T8 print / refresh bill | Returns the bill with its charges |
| POST | `/hub/v1/orders/{id}/discount` | staff (`apply_discount`) | T9 | 409 `below_paid` |
| POST | `/hub/v1/bills/{id}/payments` | staff (`take_payment`) | T10 `{"method": "cash", "amount": "9400", "tendered": "10000", "tip": "0"}` | 409 `no_open_shift` (cash), `not_served` (R10, unless `override` by a manager); closes the order (T11) when complete |
| POST | `/hub/v1/orders/{id}/cancel` | staff (`take_orders` / `cancel_order`) | T12 `{"reason": "…"}` | 409 `has_payments` |
| POST | `/hub/v1/menu/items/{id}/availability` | staff (`mark_sold_out`) | T13 | |
| GET | `/hub/v1/tables`, `/hub/v1/orders?status=open` | staff | Floor view and open orders | |
| GET | `/hub/v1/health` | anyone on the LAN | "Network check": hub version, time, cloud status | No data |

Every write runs in one SQLite transaction with its audit entry, its `hub_events` row and its outbox
row ([`ORDER_LIFECYCLE.md`](ORDER_LIFECYCLE.md)).
