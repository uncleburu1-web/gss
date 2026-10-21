# FloCafe analysis (restaurant reference)

Initial study of [FloCafe](https://github.com/FreeOpenSourcePOS/FloCafe) for Benchline's restaurant
domain, as asked in Parts 7 and 15 (stage 4) of the brief. Read on **21 Oct 2026** from a shallow
clone at commit `0818717` (20 Oct 2026). FloCafe is MIT-licensed: we may reuse code if we keep its
copyright notice, but its stack (TypeScript, Express, Next.js, one SQLite file) differs from ours,
so we take **ideas, not code**.

Sources read: `README.md`, `docs/architecture/overview.md`, `docs/architecture/order-lifecycle.md`,
`docs/decisions/0001-offline-first-core.md`, `docs/reference/api.md` (tables, kitchen stations,
orders, KDS sections), `shared/order-item-status.ts`, `shared/role-permissions.ts`, and the table
list in `main/db.ts`. Statements below are what those files say; where I infer, I say so.

## 1. How FloCafe is built

**One computer is the restaurant.** A single Electron app holds the only database (SQLite) and runs
three small web servers inside itself (`docs/architecture/overview.md`):

| Port | Serves | Who uses it |
| --- | --- | --- |
| 3001 | The API + the POS screen + the kitchen WebSocket `/kds` | The counter |
| 3002 | The kitchen display (KDS) | A tablet or screen in the kitchen, over the shop's Wi-Fi |
| 3003 | "Server App", a cut-down ordering screen | Waiters' phones, over Wi-Fi |

Other devices are just browsers on the local network; the app announces itself as `flo.local`
(mDNS). Cloud features (backup, reporting, a back office) are optional and never on the path of
taking an order (ADR `0001-offline-first-core.md`).

**Why this matters for us:** FloCafe never has two databases that disagree about an order, because
there is only one. Benchline has one database per till, joined through the cloud. That works for a
supermarket; for a kitchen it would mean an outage cuts the waiter off from the kitchen.

## 2. Restaurant feature inventory

| Area | What FloCafe has | Evidence |
| --- | --- | --- |
| Order types | `dine_in`, `takeaway`, `delivery`, `online` | `order-lifecycle.md` "Creating the order" |
| Held carts | A parked cart per table (`held_orders`), not yet an order: no price, no stock, no kitchen ticket | `order-lifecycle.md` "Cart and held carts" |
| Server-side pricing | Price, tax, charges and discounts are always computed by the server from the catalog; a price sent by a screen is never trusted | ADR `0002`, `order-lifecycle.md` |
| Order status | `pending → preparing → ready → served → completed`, or `cancelled` from any non-terminal state; cancelling needs a manager PIN | `api.md` Orders, `PATCH /:id/status` |
| Order-item status | Kitchen states `pending, preparing, ready, served`; terminal `cancelled, voided, void_adjustment, refunded` | `shared/order-item-status.ts`, `api.md` KDS |
| Editing after the kitchen started | More of an item → a **new** pending line for the extra units; less → the line is reduced and the difference recorded as `wasted_quantity`; swapping the dish is refused once cooking started. Guarded by an `edit_version` number (optimistic locking) | `api.md` `PATCH /:orderId/items/:itemId/edit` |
| Bills | A bill is separate from the order; an order can be split into several bills; the order completes when every bill is paid | `order-lifecycle.md` "Bills" |
| Payments | Many lines per bill; cash over-tender becomes change; external `transaction_id` must be unique per method; `Idempotency-Key` on orders, payments and refunds | `order-lifecycle.md` "Payments" |
| Kitchen-before-payment gate | Optional: a bill cannot be settled while kitchen items are undelivered, unless a manager overrides with a PIN (audited) | `order-lifecycle.md` "Payments" |
| Discounts | Order, line or bill level; caps; optional manager approval; never below what was already paid | `order-lifecycle.md` "Discounts" |
| Tables | Number, name, capacity, floor, section, x/y position on a floor plan, a default kitchen station; status `available / occupied / held / reserved`; **move an order to another table**; soft deactivate | `api.md` Tables |
| Table merge / split | **Not present** (only splitting a *bill*) | `api.md` Tables has no merge endpoint |
| Reservations | Only as a table status with a customer attached | `api.md` `PATCH /tables/:id/status` |
| Menu | Categories, products, product variants, add-on groups and add-ons (modifiers), recipes and supplies (ingredient stock) | `main/db.ts` tables `addon_groups`, `addons`, `product_variants`, `recipes`, `supplies` |
| Kitchen stations | Name, the categories it cooks, its printer, assigned staff | `api.md` Kitchen stations |
| KDS | WebSocket `/kds`: connect → authenticate within 5 s → snapshot → live updates; each cook sees only their stations and categories; status changes use compare-and-set (`expected_status`), re-checked inside the write | `api.md` "KDS WebSocket", "KDS category and station scoping" |
| Kitchen tickets on paper | A separate `KotDocument` print format per station printer | `docs/architecture/printing.md` |
| Roles | `owner, manager, cashier, server, chef` | `shared/role-permissions.ts` |
| Shifts and cash | Cash sessions, drawer movements, day closure; cash payments can require an open shift | `main/db.ts`, `order-lifecycle.md` "Shift gate" |
| Audit | `order_audit_log` (discounts, overrides, edits), `authorization_audit_log` | `main/db.ts` |
| Cloud | An outbox to its own back office (`cloud_sync_outbox`) | `main/db.ts` |

## 3. Where FloCafe and the brief disagree

The brief (Part 8) asks to keep **order items** and **kitchen ticket items** apart. FloCafe does
**not**: its kitchen screen works directly on `order_items` and their status. There is no ticket
table; a "kitchen ticket" exists only as a printout.

Why the brief is right for Benchline:

- *Example.* A table orders 2 jollof rice. The kitchen starts cooking. The waiter adds a third.
  FloCafe handles this by inserting a new pending order line, so the kitchen sees "2 (cooking)" and
  "1 (new)". That works, but billing and kitchen now share one row, so every kitchen rule (fire, bump,
  recall, re-print) is a rule about a billing row too.
- With a separate `KitchenTicket` / `KitchenTicketItem`, "fire the starters now, mains later",
  "re-send to the grill", "the kitchen voided it, but the customer still pays for the wasted
  plate" are kitchen facts that never rewrite the bill.

**Recommendation:** keep FloCafe's *status vocabulary and edit rules* (they are well thought out),
but model them on a ticket that **points at** the order item.

## 4. What to take, what to leave

| Take (adapted) | Leave |
| --- | --- |
| Order ≠ bill ≠ payment, each with its own status | One process hosting three HTTP servers (we already have Django for the cloud) |
| Server decides prices; screens never send a trusted price (already Benchline's rule for sales) | Its tax packs (Benchline's VAT handling is separate work) |
| The edit-after-cooking rules (new line for increases, waste for decreases, no swap once cooking) | Loyalty wallet, WhatsApp, telemetry |
| `edit_version` / compare-and-set for concurrent edits from two waiters | Store-and-forward to its own back office |
| Station + category routing for the kitchen screen | Recipes / ingredient stock in the first version |
| Kitchen-before-payment gate with a manager override | |
| Move order between tables | |
| `Idempotency-Key` on every order, payment and refund write (Benchline already does this) | |

## 5. The big design question: how devices talk during an outage

A supermarket till can work alone. A restaurant cannot: the waiter, the kitchen and the cashier are
different people on different devices, and they must share one version of each order even when the
internet is down. The brief (Part 11) says not to pick an approach without comparing them.

| Option | How it works | Good | Bad |
| --- | --- | --- | --- |
| **A. Cloud only** (what tills do today) | Every device talks to Railway | Nothing new to build | Internet down = kitchen gets nothing. Not acceptable for a kitchen |
| **B. A hub till on the local network** (FloCafe's model) | One Benchline till per restaurant also runs a small local server. Kitchen screen and waiter phones talk to it over Wi-Fi. The hub syncs to the cloud with the existing outbox | Proven by FloCafe; one local truth for orders and tables, so no conflicts between waiters; works with the internet down | The hub is a single point of failure (if that PC is off, ordering stops); waiter phones need a "local mode"; a new server inside the till must be secured |
| **C. Tills sync with each other** (peer to peer) | Every device keeps a copy and they merge | No single point of failure | Two waiters editing one order offline must be merged; very hard to get right; no reference to learn from |
| **D. Cloud first + kitchen printer fallback** | Orders go to the cloud and KDS while online. When offline, the till prints the kitchen ticket straight to a kitchen printer on the LAN, and queues the order | Smallest change; paper is what many Nigerian kitchens already use | No shared table status between devices while offline; the KDS is blank during an outage |

**Recommendation (needs approval):** **B, built in two steps.**

1. *Step 1:* one hub till runs the restaurant: orders, tables, kitchen tickets. The kitchen sees
   tickets on a KDS screen in a browser on the same Wi-Fi, **and** on a kitchen printer. No waiter
   phones yet. This is FloCafe's proven shape and keeps our existing cloud sync for reporting.
2. *Step 2:* waiter phones order through the hub over Wi-Fi, falling back to the cloud when away
   from the shop.

D is a sensible **fallback inside B** (print the ticket if the KDS screen is off), not an
alternative to it.

## 6. Smallest end-to-end restaurant workflow (proposal)

What the first restaurant release would do, and nothing more:

1. Owner sets up a menu (categories, items, one level of modifiers with prices), tables, and kitchen stations.
2. Cashier/waiter at the hub till opens a dine-in or takeaway order, adds items with modifiers, and sends it.
3. The send creates kitchen tickets per station; the KDS shows them and the station printer prints them.
4. The cook moves items `pending → preparing → ready`; the till sees "ready" live.
5. The waiter marks items served.
6. The cashier takes payment (cash, card, transfer, mobile money, split), inside an open shift.
7. The order closes, the table frees, and the order goes up to the cloud as one document.
8. Reports show it.
9. All of steps 2–7 work with the internet cable unplugged.

Out of the first release: reservations, table merge/split, split bills, delivery, recipes and
ingredient stock, waiter phones, happy-hour pricing.

## 7. Questions for the owner

These change the design, so they need answers before stage 5 (requirements):

1. Offline model: approve option B (hub till) or prefer D (printer fallback only)?
2. Is a restaurant a whole organization, or can one organization have both supermarket and restaurant
   branches? (Today `Organization.business_type` is per organization and drives optional modules such
   as Service.)
3. Which first restaurant to design for: counter service (pay first) or table service (pay last)?
4. Kitchen output: KDS screen, printer, or both?
5. Does the restaurant charge VAT and/or a service charge on the bill?
