# Codebase gap analysis

Baseline for the next phase (restaurant). Checked against `main` in all four repos on
**21 Oct 2026**. How the system works is in [`CURRENT_ARCHITECTURE.md`](CURRENT_ARCHITECTURE.md);
what is being done about each gap is in [`PROJECT_TRACKER.md`](../../PROJECT_TRACKER.md).

**How to read the status column**

| Status | Meaning |
| --- | --- |
| Verified | Exists, and automated tests that exercise it passed on 20–21 Oct 2026 |
| Exists, untested | Code exists and is used in production, but no automated test covers it |
| Partial | Some of it exists; the missing part is named |
| Missing | Not in the code |
| Broken | Exists but does not work |
| Not applicable | Not needed for Benchline's direction |

## 1. Feature inventory

The brief's audit checklist (Part 3.1), plus what was added in stages 1–11.
"Restaurant reuse" is a recommendation, not a decision (see section 5).

| Feature | Status | Where (evidence) | Limits | Restaurant reuse |
| --- | --- | --- | --- | --- |
| Tenancy (organization → branches) | Verified | `core/models.py` `Organization`, `Shop`; `core/mixins.py`; `core/tests.py`, `sync/test_stage1.py` | One CEO user owns an organization | **Reuse as is.** A restaurant is an organization; a restaurant location is a `Shop` |
| Staff, roles, capabilities | Verified | `staff/models.py` `Worker.ROLE_CHOICES`; `core/capabilities.py`; `staff/tests.py`, `audit/tests.py` | No waiter / kitchen / cashier roles | **Extend:** add restaurant roles to the same model |
| Products and variants | Verified | `inventory/models.py` `InventoryItem`, `ItemVariant`; `inventory/tests.py` | Variants are cloud only, not on the till | **Do not reuse directly.** A menu item has modifiers, stations and availability; see FloCafe analysis |
| Barcodes | Verified | `InventoryItem.barcode` (unique per branch); `inventory/test_barcode_api.py`; till scale labels (`PosScreen.jsx` `readScaleLabel`) | | Not needed for menus |
| Inventory batches, FEFO | Verified | `StockBatch`; `sales/views.py` allocation; `inventory/tests.py` | | Ingredients later, if recipes are added |
| Stock ledger (sale, return, damage, count, transfer) | Verified | `StockMovement`, `inventory/ledger.py`; `inventory/test_ledger.py`; nightly drift check | Offline oversell is detected, not prevented (section 4) | Reuse for ingredient stock if recipes come |
| Sell by weight | Verified | `InventoryItem.sold_by_weight`, `ledger.check_quantity`; `inventory/test_weight.py`; till `test/stage11.test.js`; `e2e/till-weight.cjs` | | Not needed |
| Sales (till, web, phone) | Verified | `sales/*`; `sales/tests.py`, `sync/test_stage2.py`; till `test/stage2.test.js` | | **Do not reuse as the order.** An order is open for a long time and changes; a sale is final. A closed order can *produce* a sale record for reports |
| Payments (split, installments, mobile money) | Verified | `SalePayment`; `sales/tests.py`; till `test/payments.test.js`, `test/stage7.test.js` | Payments belong to a sale | **Extend:** payments against an order bill should follow the same append-only rule |
| Returns and refunds | Verified | `sales/returns.py`; `sales/test_returns.py`; till `test/stage8.test.js` | | Restaurant voids/refunds differ (food is not restocked) |
| Shifts, cash in/out, Z-report | Verified | `shifts/*`; `shifts/tests.py`; till `test/shifts.test.js`, `test/stage3.test.js` | | **Reuse as is.** A cashier shift is the same in a restaurant |
| Promotions | Verified | `promotions/engine.py`, `promotions.js` ×3; `promotions/tests.py`; till `test/promotions.test.js` | | Happy-hour rules could reuse later |
| Branch stock transfers | Verified | `inventory/transfers.py`; `inventory/test_transfers.py` | | Not needed first |
| Customers | Verified | `customers/models.py`; covered through `sync/tests.py`, `sales/tests.py` | No own test file | Reuse |
| Suppliers | Verified | `suppliers/tests.py` | Web/phone only | Reuse |
| Purchases | Partial | Receiving stock = creating a `StockBatch` with a cost | No purchase orders, no supplier invoices | Not needed first |
| Expenses, liabilities | Verified | `expenses/tests.py`, `liabilities/tests.py` | Web only (expenses) | Reuse |
| Repairs (service desk) | Exists, untested | `repairs/*`; `repairs/tests.py` has **0 tests** | | Not applicable |
| Reports | Verified | `reports/views.py` (14 endpoints); `reports/tests.py` | Built on `Sale` | Restaurant reports need order/table/kitchen-time data |
| Product images | Exists, untested | `InventoryItem.image_url` via Cloudinary (`core/cloudinary_utils.py`) | Not on the till | Menu photos could reuse |
| Subscriptions and billing | Partial | Gate verified (`audit/tests.py` `SubscriptionGateTests`); Paystack checkout/webhook in `subscriptions/views.py` | **No tests for the Paystack payment path** | Shared, per organization |
| Audit log | Verified | `audit/models.py` (append-only); `audit/tests.py`, `audit/test_stage7.py` | | **Reuse as is** |
| Till offline persistence | Verified | `db.js` (WAL, one transaction per sale); till `test/cart.recovery.test.js`, `test/stage2.test.js` | | **Reuse the engine; add new document types** |
| Cloud sync (outbox, idempotency) | Verified | `sync.js`, `sync/views.py`; `sync/test_stage1*.py`, `test_stage2.py`; two-till E2E 13/13 + 4/4 (20 Oct) | Tills only meet through the cloud | **Reuse the protocol.** Not enough on its own for a kitchen that must work offline (section 5) |
| WebSockets | Verified | `realtime/*`; `realtime/tests.py` | Section 2, R1 and R2 | **Extend** with restaurant events |
| Device monitoring | Verified | `devices/models.py`; `devices/test_stage1b.py` | | Reuse for KDS screens and waiter phones |
| Till auto-update | **Broken** | `updater.js` + GitHub Releases | All releases since v1.0.17 are drafts (B1) | Same pipeline |
| Web/phone automated tests | Missing | Only hand-run E2E scripts in `gssfrontend/e2e/` | | Needed before restaurant screens |

## 2. Core POS gaps

### Gaps found in the October audit (G1–G12): all closed

| # | Gap | Fixed in | Evidence |
| --- | --- | --- | --- |
| G1 | A sale uploaded as header + separate lines; could arrive half | Stage 2 (`sale_doc`) | `sync/test_stage2.py`, till `test/stage2.test.js` |
| G2 | Installment payments overwrote each other (`amount_paid` absolute) | Stage 2 (`SalePayment`, append-only) | same |
| G3 | CEO with 2+ branches got 403 on every till push | Stage 1 (branch from device) | `sync/test_stage1.py` |
| G4 | Pull cursor could skip a row committed mid-pull | Stage 1 (2-min overlap) | `sync/test_stage1.py` |
| G5 | No record of which device/user applied an op | Stage 1 | `SyncOperation.device`, `.user` |
| G6 | Same op id with new content silently accepted | Stage 1 (`payload_hash`) | `sync/test_stage1.py` |
| G7 | No stock ledger; changes had no reason or author | Stage 4 | `inventory/test_ledger.py` |
| G8 | Shifts and Z-reports lived only on the till | Stage 3 | `shifts/tests.py` |
| G9 | One WebSocket event per applied op (500 sales ≈ 1,500 events) | Stage 1b (one `sync.applied` per batch) | `sync/test_stage1b.py` |
| G10 | Tills waited up to 5 min after the server recovered | Stage 1b | till `test/stage1b.test.js` |
| G11 | First pull sent every row in one response | Stage 1b (paged pull) | `sync/test_stage1b.py` |
| G12 | Sync not gated by subscription; tokens in plain SQLite; no audit log | Stages 5–6 | `audit/tests.py`, till `test/stage6.test.js` |

### Open gaps found on 21 Oct 2026

| # | Finding | Practical impact | Evidence | Severity | Proposed fix |
| --- | --- | --- | --- | --- | --- |
| B1 | Every till installer since v1.0.17 is a **draft** GitHub Release | Tills never see an update: `electron-updater` reads published releases only. The shop's till has not received stages 3–11 through auto-update | `gh api repos/uncleburu1-web/gssdesktop/releases`: v1.0.17–v1.0.22 `draft=true`; `releases/latest` → 404, so **no** release has ever been published | **High** | Publish the latest release once (owner's call), then set `releaseType: "release"` in the electron-builder config so future builds publish themselves |
| R1 | A `RealtimeEvent` row is written in `on_commit`, after the data commits, not in the same transaction | If the process dies in that gap, the data is safe but no event row exists, so a client that relies on sequence numbers does not learn it missed something. Harmless today (clients also poll). It **matters for a kitchen screen**, which must never miss a new ticket | `realtime/events.py` `broadcast()` → `transaction.on_commit(_send)` | Low now, High for restaurant | For restaurant events, write the event row inside the business transaction (a transactional outbox) and send after commit |
| R2 | Channel layer is in-memory | Works only while the backend is one process. A second Railway replica would split sockets into two worlds | `benchline/settings.py` `CHANNEL_LAYERS` | Low (one process today) | Add Redis only when a second process is needed; document the trigger |
| C1 | Web and phone are one app copied into two repos, and have drifted | Every change is made twice; fixes land in one and not the other (e.g. 319 differing lines in `Reports.jsx`) | `diff gssfrontend/src/pages gssandriod/src/pages` | Medium | Decide before restaurant screens: shared package or one repo with a Capacitor build. Needs owner approval (section 6) |
| T1 | No automated tests for web and phone screens | A broken page is found by users, not by CI | No test runner in `gssfrontend/package.json`; Android has template tests only | Medium | Run the existing browser E2E scripts in CI against a throwaway Postgres |
| T2 | Paystack payment path and repairs have no tests | A billing regression would only show when an owner tries to pay | `repairs/tests.py`: 0 tests; no Paystack test | Medium | Tests with a mocked Paystack response |
| P1 | No purchase orders | Restocking is recorded but not planned | No `Purchase` model | Low | Defer |

## 3. Failure scenarios

The brief (Part 5, step 4) asks what actually happens. Checked against code and tests.

| Scenario | What happens | Evidence |
| --- | --- | --- |
| Internet unavailable | The till sells normally; ops queue in `sync_queue`. Web and phone cannot work | `db.js` `createSale` never touches the network |
| App closes unexpectedly | WAL + one transaction: the whole sale (stock, payments, outbox row) exists or none of it does. The open cart survives in `carts` | till `test/cart.recovery.test.js` |
| Server applied the sale, response lost | Resend carries the same op id → `already_applied` → marked synced, no duplicate | `sync/test_stage1.py`, `SyncPushView` |
| Same request sent twice at once | The `SyncOperation` insert is the lock; the second waits then gets `already_applied` | `SyncPushView` (`IntegrityError` branch) |
| Same op id, changed content | `rejected: id reused with different content` | `sync/test_stage1.py` |
| Local and cloud stock differ | Stock is never overwritten. Each side applies the other's movements as deltas; the nightly job compares ledger and batches and reports drift | `inventory/ledger.py` `drift()`; nightly log 21 Oct: no drift |
| Several tills sell the last unit offline | Both sales succeed (the customers already have the goods). The cloud records `stock_shortfall` on the later sale and alerts the owner. **Offline tills cannot prevent this**; no code can while they are offline | `SaleItem.stock_shortfall`; two-till E2E |
| Subscription expires | API answers 402; the till shows "paused", keeps selling and keeps its queue | `audit/tests.py` `SubscriptionGateTests` |
| Postgres down | Push gets 5xx; rows back off; released on the first successful call | till `test/stage1b.test.js` |
| A rejected op | Row is `failed` with the server's reason, shown on the till; retried only by a person, and that retry is audited | `retryFailedSync`; `audit/test_stage7.py` |

## 4. Limits that are by design

- **Offline stock is a local truth, not a global one.** Two offline tills can each sell the last
  unit. Benchline detects and reports it; it does not pretend to prevent it.
- **Clock differences.** Server-side ordering uses per-shop sequence numbers and server timestamps;
  till timestamps are kept as "when the customer was served" but never used to order the ledger.
- **Tills do not talk to each other.** Fine for a supermarket, not enough for a restaurant kitchen
  during an outage (section 5).

## 5. What the restaurant needs that the platform does not have

| Need | Why the current platform falls short |
| --- | --- |
| A long-lived order that changes after it is sent to the kitchen | `Sale` is final when created; editing it rewrites history |
| Kitchen work separate from billing | Nothing models a ticket, a station, or prep status |
| Tables and their state | No table concept anywhere |
| Modifiers ("no onions", "extra cheese") with prices | `ItemVariant` is a fixed variant, not a choice per order line |
| Devices in one building talking **without internet** (waiter → kitchen) | Tills only meet through the cloud. With the internet down, a waiter's order would not reach the kitchen screen |
| Events that must not be missed (new ticket) | R1 above |

The last two are the big architectural choices; they are written up with options in
[`docs/restaurant/FLOCAFE_ANALYSIS.md`](../restaurant/FLOCAFE_ANALYSIS.md#5-the-big-design-question-how-devices-talk-during-an-outage).

## 6. Decisions that need the owner

1. **B1:** publish the v1.0.22 draft so the shop's till can update (a production action).
2. **C1:** how to stop the web/phone copy drift before restaurant screens are written.
3. **Restaurant offline model:** see the FloCafe analysis, section 5.
