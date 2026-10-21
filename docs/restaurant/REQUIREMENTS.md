# Restaurant requirements (first release)

What the first restaurant release must do, from the owner's decisions of **21 Oct 2026**.
How it is built: [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md), [`ORDER_LIFECYCLE.md`](ORDER_LIFECYCLE.md),
[`KITCHEN_WORKFLOW.md`](KITCHEN_WORKFLOW.md), [`OFFLINE_SYNCHRONIZATION.md`](OFFLINE_SYNCHRONIZATION.md).
Status: **design, awaiting review.**

## 1. Workflows

### Table service (decision 5: first)

1. A waiter opens a **table** (number of guests optional).
2. An **order** is created on it.
3. Menu items are added, each with its **modifiers** ("no pepper", "large", "extra chicken").
4. The waiter **sends** the new items to the kitchen. Nothing reaches the kitchen before that.
5. The kitchen **tracks preparation**: queued → preparing → ready.
6. The waiter **is told when items are ready** (on the hub screen).
7. The waiter marks items **served**.
8. The cashier **prints the bill**.
9. The cashier **takes payment**: cash, transfer, card, mobile money, or a mix, in part or in full.
10. The order **closes** and the **table is free** again.

### Counter service and takeaway (same order engine)

- **Takeaway:** same as above with no table.
- **Counter (pay first):** order → bill → payment → send to kitchen → ready → collected. The
  only difference is the order of steps, so it is the same order, not a second system.

Each order records three separate things (decision 5): its **service type**
(`dine_in`, `takeaway`, `counter`), its **payment status** (unpaid, partly paid, paid) and its
**kitchen status** (nothing sent, in the kitchen, ready, served).

### Not in the first release

Table merge or split, split bills, reservations, delivery dispatch, recipes and ingredient stock,
waiter phones (step 2), happy-hour pricing, loyalty, refunds of a closed restaurant bill (voids
before payment are in).

## 2. Business rules

| # | Rule |
| --- | --- |
| R1 | Prices, discounts, service charge and taxes are computed by the hub from the menu, never typed by hand. The cloud recomputes them when the order arrives and rejects a mismatch |
| R2 | An item not yet sent can be changed or removed freely |
| R3 | Once sent, an item cannot be swapped for a different dish; more of it is a **new** item sent to the kitchen, less of it is a **void** of the difference |
| R4 | Voiding a sent item needs a reason and the `void_sent_item` capability. The kitchen is told immediately. If cooking had started, the void is marked **waste** (food cost lost, not charged) |
| R5 | One open order per table at a time. Moving an order to another table needs that table to be free |
| R6 | A bill can be printed any time; adding items afterwards updates it while it is unpaid. A discount or void can never make the bill smaller than what has already been paid |
| R7 | Payments are append-only records. The order closes only when the bill is fully paid and every item is served or voided |
| R8 | An order with nothing paid can be cancelled with a reason; if anything was sent to the kitchen, cancelling needs the `cancel_order` capability (manager) |
| R9 | Cash payments need an open cashier shift (same shifts as the supermarket) |
| R10 | Optional per branch: a dine-in bill cannot be settled while kitchen items are not yet served, unless a manager overrides it (recorded in the audit log) |
| R11 | A sold-out item can be switched off at the hub; it disappears from ordering until switched on |
| R12 | Every void, discount, cancellation, table move, reprint and override writes an audit entry with who, when and why |
| R13 | Everything above works with the internet down |

## 3. Kitchen output (decision 6: printer first, screen too)

- Each branch defines **kitchen stations** (Kitchen, Grill, Bar). Each menu category (or item) is
  routed to one station.
- On **send**, each station gets one **kitchen ticket** with only its items. It is **printed** on the
  station's printer, **and/or** shown on the station's **kitchen screen**, per station settings.
  A restaurant may have printers only, screens only, or both.
- **Reprint** needs the `reprint_ticket` capability and is audited; a reprint is marked "COPY".
- Voids after sending print/show a **VOID** slip for that station.

Example: pizza, fries and a soft drink → the pizza and fries go to the Kitchen printer and screen;
the drink goes to the Bar printer.

## 4. Taxes and charges (decision 7)

### What the system supports

- Any number of **tax rules per branch**, each with: name, rate, inclusive or exclusive, which menu
  categories it applies to, whether it applies to the service charge, and the dates it is valid.
- An optional **service charge** per branch: rate, which service types it applies to (dine-in by
  default), whether it is taxable.
- **Tips** are recorded on the payment, separate from revenue, tax and service charge.
- Every bill stores a **snapshot** of each rate and amount it used. Changing a rate later never
  changes a past bill.
- Order of calculation: item subtotal → discount → service charge → each tax on its own base →
  total. Each amount is rounded half-up to the kobo, once per line of the breakdown.

### What the rules are in Nigeria today (checked 21 Oct 2026, not legal advice)

| Item | Current position | Source |
| --- | --- | --- |
| VAT | **7.5%**, retained under the Nigeria Tax Act 2025, in force from **1 Jan 2026** | [Forvis Mazars](https://www.forvismazars.com/ng/en/services/tax/nigeria-s-tax-reform-in-focus/2025-nta-vat-key-business-changes) |
| Small businesses | Turnover ≤ **₦100 million** and fixed assets ≤ ₦250 million: exempt from registering for and charging VAT | same |
| Lagos restaurant consumption tax | **5%** on the bill **excluding VAT**, on food and drink sold in restaurants, bars and cafés in Lagos; restaurant collects, remits to LIRS by the 20th of the month | [Mondaq, 3 Jul 2025](https://www.mondaq.com/nigeria/tax-authorities/1646430/understanding-the-hotel-occupancy-and-restaurant-consumption-tax-law-of-lagos-state) |
| VAT vs Lagos consumption tax | The Court of Appeal (Lagos, July 2022) set aside the 2019 ruling that had barred VAT on hotels, restaurants and event centres in Lagos; a Supreme Court case on the wider dispute was pending at that time | [TheCable](https://www.thecable.ng/?p=719470), [KPMG](https://kpmg.com/ng/en/insights/2022/10/vat-affirmed-as-the-principal-tax-on-goods-and-services-in-hotels-restaurants-and-event-centers.html) |
| E-invoicing | The national fiscalisation system (Merchant Buyer Solution) is being rolled out from the largest taxpayers (₦5 billion+ turnover) | Forvis Mazars |

So the system must allow **two taxes on one bill** (VAT and a state consumption tax), each with its
own base, and a branch that **charges no VAT** (small business). Which rules a given restaurant
applies is the owner's or their accountant's call; Benchline only stores and applies them.

### Worked example (illustrative configuration, not tax advice)

A Lagos dine-in bill with a 10% service charge that is not taxed, Lagos consumption tax 5% and VAT
7.5% both on food and drink after discount:

| Line | Amount (₦) |
| --- | --- |
| Food and drink subtotal | 50,000.00 |
| Discount | −2,000.00 |
| Net | 48,000.00 |
| Service charge 10% | 4,800.00 |
| Lagos consumption tax 5% of 48,000 | 2,400.00 |
| VAT 7.5% of 48,000 | 3,600.00 |
| **Total payable** | **58,800.00** |

## 5. Acceptance criteria for the first release

1. The full table-service workflow (section 1) completes on a hub till with kitchen tickets printed
   and shown on a kitchen screen, and the closed order appears in the cloud **once** with the same
   totals.
2. The same with the **internet unplugged** from step 1 to step 10; it uploads once when the
   internet returns.
3. The hub is **restarted mid-order** (power cut): every open order, ticket state and payment is
   still there; kitchen screens reconnect by themselves and show the same state.
4. A kitchen screen that **missed updates** (Wi-Fi drop) shows the correct state when it comes back,
   without a refresh by hand.
5. Two people act on the same item at the same time (bump vs void): exactly one wins, the other is
   told why; nothing is applied twice.
6. Sending, paying, closing and syncing are **idempotent**: a retried request never duplicates a
   ticket, a payment or an order.
7. Bill totals match the shared tax test vectors in JavaScript (hub) and Python (cloud).
8. A user from another business, or another branch they do not belong to, cannot see or change the
   restaurant's data through the API, the sync endpoint or the kitchen screen.
9. Reports and the cashier's Z-report include restaurant takings.
10. Existing supermarket behaviour is unchanged (all existing tests pass).
