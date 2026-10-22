# Benchline project tracker

The single source of truth for where Benchline stands. A new session should be able to resume from
this file alone. Last updated **22 Oct 2026 (evening)**.

| Detail | Where |
| --- | --- |
| How the system works today | [`docs/architecture/CURRENT_ARCHITECTURE.md`](docs/architecture/CURRENT_ARCHITECTURE.md) |
| What is missing or broken | [`docs/architecture/CODEBASE_GAP_ANALYSIS.md`](docs/architecture/CODEBASE_GAP_ANALYSIS.md) |
| Why things are built the way they are | [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md) |
| Where Benchline is heading (branch modules, restaurant hub) | [`docs/architecture/TARGET_ARCHITECTURE.md`](docs/architecture/TARGET_ARCHITECTURE.md) |
| Restaurant reference study | [`docs/restaurant/FLOCAFE_ANALYSIS.md`](docs/restaurant/FLOCAFE_ANALYSIS.md) |
| Restaurant design | [`REQUIREMENTS`](docs/restaurant/REQUIREMENTS.md) · [`DOMAIN_MODEL`](docs/restaurant/DOMAIN_MODEL.md) · [`ORDER_LIFECYCLE`](docs/restaurant/ORDER_LIFECYCLE.md) · [`KITCHEN_WORKFLOW`](docs/restaurant/KITCHEN_WORKFLOW.md) · [`OFFLINE_SYNCHRONIZATION`](docs/restaurant/OFFLINE_SYNCHRONIZATION.md) · [`WEBSOCKET_EVENTS`](docs/restaurant/WEBSOCKET_EVENTS.md) · [`API_SPECIFICATION`](docs/restaurant/API_SPECIFICATION.md) |
| Till release gate | [`gssdesktop/docs/RELEASE_CHECKLIST.md`](https://github.com/uncleburu1-web/gssdesktop/blob/main/docs/RELEASE_CHECKLIST.md) (after gssdesktop PR #5) |
| Core engine plan and failure testing (Oct 2026) | [Architecture analysis doc](https://claude.ai/artifact/4fdJDw29CmJ5VcVMZGiYfX) |

## 1. Objectives

Benchline (GSS — Gavin's Software Solutions Limited) is an existing POS being strengthened into a
commercial business-management platform:

1. Preserve and improve the existing supermarket/retail POS.
2. Keep it reliable offline, with sound transactions and sync.
3. Strengthen shared infrastructure (payments, staff access, devices).
4. Add a separate restaurant domain on the same shared core.
5. Keep the architecture clean and documented.

## 2. Where we are

**Core POS engine (Parts 5–6 of the brief): complete** on the backend, web and phone. **The till
release is held:** the v1.0.22 release check found a stock bug (FIX-01, gssdesktop PR #5). The next
till build (v1.0.23) is published once that PR is merged and the checklist passes (OPS-01).

**Restaurant (Parts 7–14):** requirements and design reviewed and merged (gss PR #7). On the
`restaurant` branches, these are merged:
- configuration, packages and permissions (gss PR #8);
- kitchen routing (gssdesktop PR #6).

These are in review:
- the shared bill engine (gss PR #10);
- the hub's order engine and kitchen printing (gssdesktop PR #7).

A full table-service order already runs on a hub till with the server switched off, and its bill
matches the cloud's to the kobo (end-to-end test against a real server).

Still to build:
- the hub's screens;
- the Wi-Fi kitchen screen;
- the cloud receiving closed orders;
- the web set-up screens.

Nothing restaurant-related deploys until the complete workflow passes (REST-09).

Milestones, as the brief defines them:

| Area | Designed | Implemented | Tested | Integrated | Released |
| --- | --- | --- | --- | --- | --- |
| Core engine stages 0–11, backend | Yes | Yes | Yes (266/266, 21 Oct) | Yes | **Yes**, Railway (latest deploy 21 Oct 07:06 Lagos) |
| Core engine stages 0–11, web | Yes | Yes | E2E scripts only | Yes | Deployed by Cloudflare on merge; **stage 11 deploy not checked** |
| Core engine stages 0–11, phone | Yes | Yes | E2E scripts only (shared with web) | Yes | APK built 21 Oct; **install on devices not verified** |
| Core engine stages 0–11, till | Yes | Yes | Yes (137/137 with FIX-01; two-till E2E incl. outage) | Yes | **Pending:** v1.0.23 passed its checklist; the owner publishes it (OPS-01) |
| Branch modules and packages | Yes | Yes (`restaurant` branch, PR #8 merged) | Yes (full suite 292/292; again in the restaurant E2E) | On `restaurant` | No |
| Restaurant | Yes | Configuration, routing, bill engine, hub order engine, kitchen printing (gss #8 #10, gssdesktop #6 #7) | Engine yes (till 173/173; real-server E2E 18/18). Screens, LAN, cloud ingest not built | No | No |

## 3. Task board

Statuses: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `REVIEW_REQUIRED`, `TESTING`, `COMPLETED`, `DEFERRED`.
A task is `COMPLETED` only with evidence recorded.

### Open

| ID | Task | Repo | Branch | Status | Depends on | Acceptance criteria | Tests | Evidence | Blocker | Next action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OPS-01 | Release the till (gate: `docs/RELEASE_CHECKLIST.md`) | gssdesktop | `main` | TESTING | — | The shop's till installs a published build and reports its version | Checklist items 1–8 | **v1.0.23 published by the owner 22 Oct 11:36 UTC**: latest, not a pre-release, with `GSS-Setup.exe`, `latest.yml` and the blockmap (what auto-update reads). v1.0.22 stays a draft. Checklist passed 21 Oct (see session log) | — | Check the shop's till reports 1.0.23 on the web Devices page after its next start |
| OPS-06 | Turn off Django debug pages in production | Railway `gss` | — | IN_PROGRESS | — | Errors return a plain 500; no settings or request details shown | Unknown `/api/` path shows no debug page | `DEBUG=False` saved on the service 22 Oct (owner approved), goes live with the next deploy. The code default is still True | Railway outage | Verify after deploy; later make the code default False |
| OPS-03 | Rotate the Postgres password shared in chat | Railway | — | NOT_STARTED | — | New password set; services redeployed and healthy | Health check + one till sync | — | Owner action | Owner |
| OPS-04 | Delete the production test sale | prod data | — | NOT_STARTED | — | Test sale gone, stock restored, audited | Audit log entry | — | Owner action | Owner |
| OPS-05 | Confirm stage 11 is live on web | gssfrontend | `main` | NOT_STARTED | — | `pos.gssolu.com` shows the "Sold by weight" option in Inventory | Manual check | — | — | Open Inventory → Add item |
| REST-02 | Restaurant requirements (brief stage 5) | gss | PR #7 | COMPLETED | Owner decisions 21 Oct | Workflows, rules, tax/charges with sources, acceptance criteria | — | `docs/restaurant/REQUIREMENTS.md`; owner instructed review + merge (decision 9) | — | — |
| DOC-02 | Review of the restaurant design (PR #7) | gss | `design/restaurant-foundation` | COMPLETED | REST-03 | Full diff checked against the owner's list; issues fixed | Link check of every document | 14 issues fixed (session log, 21 Oct evening) | — | Merge |
| CORE-13 | Packages (`Package`, `Subscription.package`, feature checks) | gss, clients | `restaurant` (PR #8 merged) | TESTING | CORE-12 | Existing subscriptions unchanged (Retail); module switch-on needs the feature | Django tests | Package model, starter packages, trial = all features; owner merged PR #8; used again by the restaurant E2E (21 Oct) | Add-on price (billing only) | Final check in REST-09 |
| REST-10 | Link stocked menu items (bottled drinks) and recipes to stock | gss, gssdesktop | — | DEFERRED | REST-09 | Restaurant sales move stock for linked items | — | First release moves no stock (DOMAIN_MODEL §1) | — | — |
| REST-03 | Restaurant architecture (brief stage 6) | gss | PR #7 | COMPLETED | REST-02 | Domain model, state transitions T1–T13, kitchen workflow, offline sync, events, API | — | `TARGET_ARCHITECTURE.md` + six `docs/restaurant/` documents; reviewed (DOC-02) | — | — |
| CORE-12 | Branch modules (`BranchModule`, `RequiresModule`, `/api/me` modules) | gss, all clients | `restaurant` (PR #8 merged) | TESTING | REST-03 | Existing branches unchanged (retail, + service where enabled); a branch without `restaurant` gets 403 `module_disabled` | Django tests incl. cross-business isolation | BranchModule + endpoint + `/api/me`; full suite 292/292; restaurant E2E switches it on through the API and a supermarket till never asks for restaurant data | — | Final check in REST-09 |
| REST-04 | Cloud: restaurant configuration models + API + pull types | gss | `restaurant` (PR #8 merged) | TESTING | CORE-12, CORE-13 | Owner can set up menu, modifiers, tables, stations, taxes, service charge; tenant-isolated; audited | Django tests per endpoint, permission and isolation | Configuration API, tax activation safeguards, opt-in pull types; restaurant E2E sets a whole restaurant up through it and the hub pulls it | — | Web set-up screens (REST-12) |
| REST-05 | Shared tax / service-charge engine (JS + Python, shared vectors) | gss, gssdesktop | `restaurant` (gss PR #10, gssdesktop PR #7: merged) | TESTING | REST-04 | Identical results in both languages, incl. the Lagos example in REQUIREMENTS §4 | 15 shared hand-computed vectors in both languages | Lagos example ₦58,800 in both; real-server E2E: hub bill = Python bill line for line (₦11,162.50); per-rule `applies_to_service_charge` replaces the conflicting global flag (migration `restaurant 0002`); gss restaurant 28/28, full suite 294/294 (with `modules` in every pull) | — | Final check in REST-09 |
| REST-06 | Hub order engine T1–T13 + kitchen printing | gssdesktop | `restaurant` (PR #7 merged) | TESTING | REST-05 | Table-service workflow on the hub, tickets printed per station, everything offline | Till tests per transition, idempotency, restart | Till 173/173. Real-server E2E 18/18 with the server stopped during service. Only the branch's hub runs the restaurant (second till refused); restaurant audit entries wait on the hub for protocol 6 (today's cloud refused them). Printing tested with a stand-in window only: **no physical thermal printer tried yet** | Physical printer test needs the shop's hardware | Printer test in REST-09 |
| REST-07 | Hub local server, screen pairing, kitchen screen (SSE) | gssdesktop | `restaurant` (PR #8, then catch-up PR #10: merged) | TESTING | REST-06 | Screen shows and updates tickets live; catches up after a Wi-Fi drop; station isolation | Two-device LAN E2E | Till 178/178 (pairing, HTTP API, station isolation, live events, resume/resync, removal, lifecycle). Real Chromium as the screen, 18/18: pairing, live ticket, tap to cook, Wi-Fi drop with a void and a new order meanwhile then catch-up without reload, red after 20 min, second screen, "All ready", removal. Tested on one machine (browser and hub on 127.0.0.1), **not yet on two devices over Wi-Fi** | Windows Firewall asks once to allow the hub on the network | Test on the shop's Wi-Fi in REST-09; firewall step and QR code on the hub screens (REST-11) |
| REST-08 | Cloud ingest (`order_doc`), `Sale` on close, restaurant reports | gss, gssdesktop | gss `restaurant` (PR #12, then catch-up PR #15: merged) | TESTING | REST-06 | A closed order arrives once with matching totals; Z-report and reports include it | Sync tests + E2E | gss 307/307 (13 ingest tests, mutation-checked). Real-server E2E 23/23: an order closed offline reaches the cloud once, as an order and a Sale of the same ₦11,162.50; restaurant summary and audit in the cloud; nothing failed. Supermarket till-e2e 15/15, till-outage 11/11 on the protocol-6 server; real-till upgrade SAFE | Food has no cost price yet (REST-10), so it shows its whole price as profit | Final check in REST-09 |
| REST-09 | Restaurant verification and release readiness (brief stages 9–10) | all | — | NOT_STARTED | REST-07, REST-08, REST-11, REST-12 | Acceptance criteria 1–10 in REQUIREMENTS §5 | Full workflow online, offline, with restarts; physical kitchen printer | — | — | — |
| REST-11 | Hub restaurant screens: tables, order pad with modifiers, send, bill, payment, kitchen printers | gssdesktop | `restaurant-screens` (PR #9 → `restaurant`) | REVIEW_REQUIRED | REST-06 | A waiter/cashier runs table service and counter orders on the hub without training notes | Browser-driven E2E | Till 180/180. The built renderer in real Chromium against the real engine: 27/27 (table, choices, send, serve rules, READY on the floor from a kitchen screen, bill ₦8,225.00 printed, cash change, receipt, unprinted tickets, station printer, pairing, sold out, waiter permissions, not-the-hub) | Electron itself not launched here (no display); QR code for pairing not built | Owner reviews PR #9; try it on the shop's PC in REST-09 |
| REST-12 | Web set-up screens for the restaurant (menu, modifiers, tables, stations, taxes, service charge, hub) | gssfrontend, gssandriod | gssfrontend `restaurant-web` (PR #6 → `restaurant`) | REVIEW_REQUIRED | REST-04 | Owner sets a restaurant up without the API | Web E2E | Web done: per-branch modules in Settings; Restaurant page (menu, choices, tables, kitchen stations, service charge, taxes with the activation safeguards, hub till, restaurant reports) following the branch being viewed. `e2e/restaurant.e2e.cjs`, real Chromium against a real server: 23/23, twice (the CEO sets up a second branch through the forms, the database holds exactly that; the first branch's report shows the hub's ₦11,162.50 order). Found and fixed: a double settings load reset the service charge to 0; lists now follow pages; devices list ignored the branch switcher (gss #16) | Phone app (gssandriod) has no restaurant set-up yet | Owner reviews gssfrontend #6 and gss #16; phone set-up later |
| REST-13 | Restaurant as a shop type (decision 13): "Restaurant & food" next to Gadgets, Clothing, Pharmacy, General at signup and when adding a branch; the branch's type sets its modules and categories; Settings shows the type instead of loose module switches | gss, gssfrontend, gssandriod, gssdesktop | gss `restaurant-shoptype` (PR #17 → `restaurant`); gssfrontend `restaurant-web` (PR #6); gssandriod `restaurant-shoptype` (PR #6 → new `restaurant`); gssdesktop `restaurant-screens` (PR #9) | REVIEW_REQUIRED | REST-12 | A new restaurant owner signs up as a restaurant and lands on the restaurant set-up; a business can add a restaurant branch beside a shop; existing businesses unchanged | Signup and add-branch tests on web and phone; migration on a production copy | `Shop.shop_type` per branch (ADR-025); kind decides modules (gadgets retail+service, restaurant retail+restaurant, rest retail); CEO changes a branch's kind (audited, package-checked, nothing deleted); `/me` follows the branch. gss 322/322 (15 new). Real server, two fresh DBs, twice: hub 23/23, web restaurant 29/29, restaurant sign-up 9/9, phone 10/10, supermarket till 15/15. Migration checked on a database with every kind: modules identical before/after, reversible | Not tried on a copy of production data (no access from here); phone has no restaurant screens (points to the web) | Owner reviews gss #17, gssfrontend #6, gssandriod #6, gssdesktop #9 |
| QA-01 | Run web E2E in CI | gssfrontend | — | NOT_STARTED | — | Every PR runs the browser E2E against a throwaway Postgres | CI | — | — | — |
| QA-02 | Tests for Paystack payments and repairs | gss | — | NOT_STARTED | — | Paystack success/failure/duplicate webhook and repair flows covered | Django tests | `repairs/tests.py` has 0 tests | — | — |
| QA-03 | CI check that shared JS files are identical in every repo | all | — | NOT_STARTED | — | A PR that changes `promotions.js` (later `tax.js`) in one repo only fails | CI | Decision 2 | — | — |
| UI-01 | Show each till's app version on the web Devices page | gssfrontend, gssandriod | — | NOT_STARTED | — | Owner can see which version each till runs | Browser check | API already returns `app_version` | — | — |

### Completed

All evidence is reproducible: `python manage.py test` in `gss`, `npm test` in `gssdesktop`, the
scripts in `gssdesktop/e2e/` and `gssfrontend/e2e/`.

| ID | Task | Repos (PRs) | Status | Evidence | Released |
| --- | --- | --- | --- | --- | --- |
| CORE-00 | Land Update 3; repair 16 broken tests; upgrade check on a real till copy | all 4 (#1) | COMPLETED | Backend 104/104, till 64/64 (18 Oct); upgrade check SAFE | 18 Oct |
| CORE-01 | Sync hardening: branch from device, overlap cursor, `payload_hash`, device attribution | gss, gssdesktop (#1) | COMPLETED | `sync/test_stage1.py`, till `test/stage1.test.js` | 18 Oct |
| CORE-02 | One-piece sale upload (`sale_doc`), append-only payments, protocol negotiation | gss, gssdesktop (#1) | COMPLETED | `sync/test_stage2.py`, till `test/stage2.test.js` | 18 Oct |
| HOTFIX-03 | A CEO's web Devices page always listed the CEO's own branch's devices, whichever branch was being viewed | gss (#16) | REVIEW_REQUIRED | `devices/views.py` list and delete now follow `X-Branch-ID` like every other page; new multi-branch CEO tests fail on the old code; full suite 272/272 | Not yet |
| HOTFIX-02 | Sales-by-item and inventory-valuation reports crashed (Decimal × float, since stage 11) | gss (#13) | COMPLETED (merged; deploy waiting on Railway) | New tests with a weighed item, hand-computed, plus a sweep of every API GET page: fail on the old code, pass now; full suite 269/269 | Pending: Railway platform outage paused all builds 21–22 Oct |
| HOTFIX-01 | Allow `Idempotency-Key` through CORS (web sales were blocked) | gss (#2) | COMPLETED | PR #2 merged and deployed | 19 Oct |
| CORE-1b | Till status online/stale/offline, one event per batch, paged pull, recovery after 5xx | gss, gssdesktop, web, phone (#3/#2) | COMPLETED | `sync/test_stage1b.py`, `devices/test_stage1b.py`, till `test/stage1b.test.js` | 19 Oct |
| CORE-03 | Shifts, cash movements, Z-reports to the cloud; owner view | all 4 (#3/#2) | COMPLETED | `shifts/tests.py`, till `test/stage3.test.js` | 19 Oct |
| CORE-04 | Stock ledger, damage, counts, nightly drift check | all 4 (#3/#2) | COMPLETED | `inventory/test_ledger.py`, till `test/stage4.test.js` | 19 Oct |
| CORE-05 | Audit log; subscription gate with no grace period; retention | all 4 (#3/#2) | COMPLETED | `audit/tests.py` | 19 Oct |
| CORE-06 | Till tokens encrypted with the OS keychain | gssdesktop (#2) | COMPLETED | till `test/stage6.test.js` | Built; till release blocked (OPS-01) |
| FEAT-01 | Sellers see only their own sales | all 4 (#3/#2) | COMPLETED | `sales/test_visibility.py` | 19 Oct |
| FEAT-02 | Till day report printing | gssdesktop (#2) | COMPLETED | till `test/day-report.test.js` | Built; OPS-01 |
| FEAT-03 | Subscribe button beside Log out; any owner can pay | web, phone (#3) | COMPLETED | PR #3 in web and phone; `RequireCanPay` checks `is_ceo` or role `owner` | 20 Oct |
| CORE-07 | Full audit trail; mobile money | all 4 (#4/#3) | COMPLETED | `audit/test_stage7.py`, till `test/stage7.test.js` | 20 Oct |
| CORE-08 | Customer returns and refunds | all 4 (#4/#3) | COMPLETED | `sales/test_returns.py`, till `test/stage8.test.js` | 20 Oct |
| CORE-09 | Move stock between branches | gss, web, phone (#4) | COMPLETED | `inventory/test_transfers.py`, till `test/stage9.test.js` | 20 Oct |
| CORE-10 | Promotions as data, identical on all four apps | all 4 (#4/#3) | COMPLETED | `promotions/tests.py`, till `test/promotions.test.js`, 14 shared vectors | 20 Oct |
| OPS-00 | Nightly maintenance job (retention + drift check) | Railway `gss-nightly` | COMPLETED | 21 Oct 02:01 UTC log: "Stock ledger matches every batch" | 20 Oct |
| CORE-11 | Sell by weight (kg to the gram) | all 4 (#5/#4) | COMPLETED | `inventory/test_weight.py`, till `test/stage11.test.js`, two-till E2E 4/4, web E2E 8/8 (20 Oct); production migrations applied 21 Oct 06:06 UTC | 21 Oct (till: OPS-01) |
| DOC-01 | Phase 0 baseline: tracker, decisions, architecture, gap analysis | gss (#6) | COMPLETED | Merged 21 Oct after owner review | — |
| REST-01 | FloCafe analysis (brief stage 4) | gss (#6) | COMPLETED | `docs/restaurant/FLOCAFE_ANALYSIS.md`; owner answered its questions 21 Oct | — |
| ARCH-01 | Decide how web and phone share code | — | COMPLETED | Decision 2 (section 7); follow-up QA-03 | — |
| FIX-01 | Till stock rebuilt from cloud history could lose changes | gssdesktop (#5) | COMPLETED | 720-order replay test; 137/137; two-till E2E incl. outage; merged 21 Oct | v1.0.23 (awaiting publish) |
| DOC-03 | Restaurant design reviewed and merged | gss (#7) | COMPLETED | 14 review fixes | — |

## 4. Bugs and blockers

| ID | Problem | Impact | Status |
| --- | --- | --- | --- |
| B1 | Till installers are draft releases | Tills cannot auto-update | **By design now:** the draft is the release gate (decision 1); publish after the checklist → OPS-01 |
| B2 | A till rebuilding stock from cloud history could lose stock changes (clamped at zero during an out-of-order replay) | A fresh or upgrading till could show more stock than the cloud (test: 14 vs 6) | Fixed (gssdesktop #5, merged); ships in v1.0.23 |
| R1 | Realtime event row written after commit | Rare missed notification; must be fixed before kitchen events | Open → ADR-017 |
| C1 | Web/phone drift | Double work, fixes land in one app only | Accepted (decision 2): keep two apps; shared contracts + shared pure JS with a CI identity check (QA-03) |

## 5. Change log by layer

### Database (Postgres, `gss` migrations)

| When | Change |
| --- | --- |
| Update 3 (11 Oct) | `Sale.receipt_no`, `IdempotencyRecord` (`stock_shortfall` and `RealtimeEvent` predate it) |
| Stage 1 / 1b | `SyncOperation` + `device`, `user`, `payload_hash`; `Device` status fields (`last_ws_seen_at`, `pending_ops`, `failed_ops`, `current_user`) |
| Stage 2 | `SalePayment` |
| Stage 3 | `shifts` app: `Shift`, `CashMovement` |
| Stage 4 | `StockMovement` (+ opening backfill) |
| Stage 5 | `AuditLog`; `Subscription.grace_period_days` default 0 |
| Stage 8–9 | `SaleReturn`, `SaleReturnLine`; `StockTransfer`, `StockTransferLine` |
| Stage 10 | `Promotion` |
| Stage 11 | `InventoryItem.sold_by_weight`; quantity columns → `Decimal(12,3)` (`inventory 0010`, `repairs 0003`, `sales 0012`) |
| `restaurant` branch only (not on `main`, not deployed) | `core 0010–0011` `BranchModule` (+ data: every branch gets retail, gadgets shops also service); `subscriptions 0003–0004` `Package`, `Subscription.package`; `staff 0004` cashier/waiter/kitchen roles; `restaurant 0001` configuration models; `restaurant 0002` drops `RestaurantSettings.service_charge_taxable` (never released; replaced by the per-rule flag); `restaurant 0003` creates the finished-order tables (Order, OrderItem, OrderItemModifier, KitchenTicket, KitchenTicketItem, Bill, BillCharge, BillPayment, MenuAvailability) |

No migration has dropped data. Backups before releases are manual `pg_dump` (Railway plan has none).

### Till (SQLite, `migrate()` in `db.js`)

`sync_queue` outbox columns (stage 1); `sale_payments` (stage 2); `shifts` upload state (stage 3);
`stock_movements`, `stock_docs` (stage 4); `sale_returns`, `sale_return_lines` (stage 8);
`promotions` (stage 10); `products.sold_by_weight` (stage 11). Each is checked on a copy of the
shop's real till database before release (`tools/check-upgrade-on-copy.js`, SAFE on 20 Oct).

Restaurant (`restaurant` branch only): its own `r_*` tables from `src/main/restaurant/schema.sql`,
created with `CREATE TABLE IF NOT EXISTS` on every open and empty on supermarket tills. No existing
table is changed; the upgrade check on the real till copy was SAFE with them in place (21 Oct).

### API

New since Update 3: `/api/shifts/`, `/api/inventory/items/{id}/damage/`, `/api/inventory/items/{id}/stock-count/`,
`/api/inventory/movements/`, `/api/audit/`, `/api/sales/{id}/return/`, `/api/inventory/transfers/`, `/api/promotions/`,
`/api/subscription/status/` (402 code for expired). Sale and stock quantities accept 3 decimals for
weighed items (stage 11).

### WebSocket

One `sync.applied` event per push batch (stage 1b); `stock.shortfall`; device presence feeds
ONLINE/STALE/OFFLINE.

### Sync protocol

Version 5. Op types: `product`, `stock_batch`, `customer`, `sale`, `sale_item`, `sale_doc`,
`sale_payment`, `shift`, `cash_movement`, `stock_movement`, `audit_event`, `sale_return`.

Version 6 on the `restaurant-ingest` branch (gss PR #12, not deployed). It adds `order_doc` (a
finished restaurant order in one piece), `menu_availability`, and restaurant audit actions, all only
from the branch's hub. The hub holds finished orders and its audit entries until the cloud reports 6,
then queues them (`reconcileOrders`). Restaurant configuration comes down as opt-in pull types, asked
for by name, only on restaurant branches; every pull also says which modules the branch uses.

## 6. Roadmap (owner's order, 21 Oct 2026)

1. **Till release** — merge FIX-01 (PR #5), run the checklist on v1.0.23, publish (OPS-01).
2. **Tracker and audit** — done (this file, DOC-01).
3. **Business, branch and module model** — designed (`TARGET_ARCHITECTURE.md` §1) → build CORE-12.
4. **Restaurant order and kitchen domain** — designed (REST-03) → build REST-04, REST-05, REST-06.
5. **Offline LAN architecture and cloud sync** — designed (`OFFLINE_SYNCHRONIZATION.md`) → build
   REST-07, REST-08.
6. **Table-service orders with basic counter service** — REST-06.
7. **Kitchen printing, then the kitchen screen** — REST-06 (printing), REST-07 (screen).
8. **Taxes, service charges, payments** — REST-05, REST-06.
9. **Full workflow test incl. outages and restarts** — REST-09.

**Approval checkpoint:** steps 3–9 start only after the owner approves REST-02 and REST-03. Restaurant
work stays on its own branches and is never bundled into a till release before REST-09 passes.

## 7. Owner decisions (21 Oct 2026)

| # | Question | Decision | Recorded in |
| --- | --- | --- | --- |
| 1 | Publish till v1.0.22? | Not until it passes a release readiness check (build, POS functions, upgrade on real data, login, offline, sync recovery, data preserved, installer upgrade, no critical bugs); then publish as stable. No unfinished restaurant work in the release | OPS-01, `RELEASE_CHECKLIST.md` |
| 2 | Web and Android code sharing | Share business contracts and pure logic where it truly removes duplication; backend enforces rules; no forced single frontend | ADR-018, QA-03 |
| 3 | Restaurant offline model | Local restaurant server on the shop's Wi-Fi + kitchen printing as an extra fallback; audit the desktop first and prefer the simplest reliable LAN design | ADR-016 (accepted), `TARGET_ARCHITECTURE.md` §2 |
| 4 | Supermarket and restaurant in one business | Yes; per-branch modules and configuration; strict isolation | ADR-019, `TARGET_ARCHITECTURE.md` §1 |
| 5 | First workflow | Table service, with takeaway/counter on the same order engine; service type, payment status and kitchen status kept separate | `REQUIREMENTS.md` §1 |
| 6 | Kitchen output | Both, printing first if the existing printing can be reused safely; printer-only, screen-only or both per restaurant | `KITCHEN_WORKFLOW.md` |
| 7 | VAT and service charge | Configurable taxes and service charge, snapshots on every bill, verified Nigerian rules, tips separate | `REQUIREMENTS.md` §4 |

Second round, 21 Oct 2026 (evening):

| # | Question | Decision | Recorded in |
| --- | --- | --- | --- |
| 8 | Merge gssdesktop PR #5? | Yes, after checking checks, conflicts, dependencies and backend compatibility | OPS-01, FIX-01 |
| 9 | Review gss PR #7 | Review the full diff (migrations, API compatibility, workflows, isolation, permissions, inventory, offline sync), fix issues, then merge | DOC-02 |
| 10 | Restaurant branch price | Same base branch price; restaurant features controlled by the subscription **package** | ADR-024, `TARGET_ARCHITECTURE.md` §1 |
| 11 | Taxes for the first restaurant | Configurable; VAT 7.5% where it applies; state consumption taxes where legally applicable; no automatic stacking; customer taxes apart from business taxes; confirm state, registration and law before activating | ADR-022, `REQUIREMENTS.md` §4 |
| 12 | First restaurant hardware | One POS PC, one thermal kitchen printer, Wi-Fi router, cash drawer, UPS; KDS supported as an alternative; receipts and kitchen tickets configured separately | `REQUIREMENTS.md` §5 |

Third round, 22 Oct 2026 (evening):

| # | Question | Decision | Recorded in |
| --- | --- | --- | --- |
| 13 | How a restaurant is chosen | **Restaurant is a shop type of its own**, picked the same way as Gadgets, Clothing, Pharmacy and General retail, not only a feature switched on in Settings. Read with decision 4 (mixed branches): the type is picked at signup for the first branch and when adding a branch | REST-13 |

Still open (billing only): the add-on price of the restaurant package(s).

**Working rule for restaurant code:** it lives on a `restaurant` integration branch in each repo.
Feature PRs merge into that branch; it merges to `main` (and deploys) only after the complete
workflow passes (REST-09).

## 8. Development history

| Date | What happened |
| --- | --- |
| Aug–Sep 2026 | Benchline split into four repos (`gss`, `gssdesktop`, `gssfrontend`, `gssandriod`); multi-branch tenancy, Paystack, real-time sync built |
| 11 Oct 2026 | "Update 3" prepared on the `benchline-*-update-2026-10-11` branches (idempotency keys, per-till receipt numbers) |
| 18 Oct 2026 | Core engine plan written; Update 3 + stages 1–2 released (PR #1 everywhere) |
| 19 Oct 2026 | CORS hotfix; stages 1b, 3–6, seller-only sales, day report released |
| 20 Oct 2026 | Owner-pay fix; stages 7–10 released; nightly job created on Railway |
| 21 Oct 2026 | Stage 11 (sell by weight) released; Phase 0 baseline and FloCafe analysis merged; owner decided the seven questions; v1.0.22 release check failed on a stock bug (fixed, PR #5); restaurant requirements and design written |

## 9. Session log

### 21 Oct 2026 — Phase 0 baseline and FloCafe study

- **Done:** verified repo inventory; checked production after the stage 11 merge (migrations
  applied, no 5xx); ran tests on `main`; studied FloCafe; checked reference licences; wrote this
  tracker, `ARCHITECTURE_DECISIONS.md`, `CURRENT_ARCHITECTURE.md`, `CODEBASE_GAP_ANALYSIS.md`,
  `FLOCAFE_ANALYSIS.md`.
- **Files changed:** the five documents above (gss, branch `docs/project-foundation`). No code changed.
- **Tests run:** `gss` 266/266 on Postgres 16 (`manage.py test`, 171 s); `gssdesktop` 133/133 (`npm test`).
- **Found:** B1 (draft releases), R1 (event written after commit), C1 (web/phone drift), T2
  (no Paystack/repairs tests); SaleFlex is AGPL-3.0.
- **Decisions:** ADR-002 accepted on licence grounds; ADR-014–017 proposed.
- **Next:** owner review and the seven decisions in section 7.

### 21 Oct 2026 — Owner decisions, release check, restaurant design

- **Done:** ran the v1.0.22 release checklist; found and fixed B2 (stock replay); wrote the release
  checklist and an outage E2E; researched current Nigerian VAT and Lagos consumption tax; wrote
  `TARGET_ARCHITECTURE.md` and the six restaurant design documents plus `REQUIREMENTS.md`; recorded
  the owner's decisions.
- **Files changed:** gssdesktop `src/main/db.js`, `test/stock-replay.test.js`, `e2e/till-outage.cjs`,
  `docs/RELEASE_CHECKLIST.md` (PR #5); gss docs on `design/restaurant-foundation`.
- **Tests run (actual):** till 137/137; real-till upgrade check SAFE and every row/value unchanged;
  till-e2e 15/15 (fresh DB), till-e2e2 13/13, till-weight 4/4, till-outage 11/11, fresh till =
  cloud per product. v1.0.22 without the fix: fresh till 14 vs cloud 6.
- **Not verified:** installer upgrade on a real Windows PC (checklist item 7).
- **Decisions:** ADR-016 accepted; ADR-018, ADR-019 added; release gate stays manual (drafts).
- **Next:** owner merges gssdesktop PR #5 → publish v1.0.23 after the checklist; owner reviews the
  restaurant design (approval checkpoint).

### 21 Oct 2026 (evening) — PR reviews, decisions 8–12

- **Done:** merged gssdesktop PR #5 after checking mergeability (clean, 0 behind main), dependencies
  (none) and backend compatibility (the fix relies on `stock_shortfall` in pulled sale lines, which
  the server already sends, `sync/views.py` `_serialize_sale_item`). Reviewed gss PR #7 and fixed 14
  issues before merging (see DOC-02).
- **Main review fixes:** the cloud re-adds a closed bill from its own frozen lines instead of
  re-pricing it from current settings (would have rejected valid orders after a rate change);
  station printers moved to the hub PC and kept apart from the receipt printer; packages; tax
  activation safeguards; `cashier` role; minimum hardware and cash drawer; business-day start time;
  tip outside the bill; restaurant `Sale` never touches stock; restaurant pull types only for
  protocol-6 clients of restaurant branches; hub sign-in limited to the branch's staff; snapshot
  coalescing in the outbox; event sequence wording for filtered screens.

### 21 Oct 2026 (evening, continued) — release v1.0.23, restaurant foundation

- **Release:** v1.0.23 built from the merged fix and checked on the actual installer (checksum,
  version, fix present, production URL, data folder, N-API SQLite module, dependencies unchanged
  since 11 Oct). Publishing is not permitted from this session (GitHub 403): **owner publishes**.
- **Built (restaurant branch, not deployed):** branch modules and their endpoint, packages, cashier
  / waiter / kitchen roles and ten capabilities, the restaurant configuration API with tax activation
  safeguards, opt-in restaurant pull types, kitchen routing in Python and JavaScript with shared cases.
- **Tests run (actual):** gss restaurant 26/26, full suite 292/292 (Postgres 16); migrations
  forward → back → forward on a copy of a used database; gssdesktop 140/140 incl. routing 3/3.
- **Bug caught by the tests:** a duplicate name returned a 500 (database constraint); now a clear 400.
- **Next:** owner publishes v1.0.23 and reviews gss PR #8 and gssdesktop PR #6; then the hub order
  engine (T1–T13) on the `restaurant` branch, then kitchen printing.

### 21 Oct 2026 (night) — bill engine, hub order engine, kitchen printing

- **Built (not deployed, `restaurant` branches):**
  - The bill engine in Python and JavaScript (gss PR #10, gssdesktop PR #7).
  - The hub's order engine T1–T13.
  - Kitchen ticket printing, with a printer per station on the hub PC.
  - The hub's IPC channel.
  - The restaurant part of the till's pull.
- **Tests run (actual):**
  - Unit tests: gss `restaurant` 28/28, full suite 294/294; gssdesktop 173/173.
  - New end-to-end script `gssdesktop/e2e/restaurant-hub.cjs`, against a real server on freshly seeded Postgres: 18/18. The owner sets up a restaurant through the API. The hub pulls it. With the server stopped, the hub runs a full table-service order: two stations, modifiers, a retried send, the kitchen screen, serving, the bill, cash payment and close. The bill equals the cloud's Python engine charge for charge (₦11,162.50). A cloud price change during service leaves the open order alone. The Z-report counts the cash. The till learns about the restaurant from the pull without a new sign-in, refuses work until the owner picks a hub, and a second till in the branch is refused.
  - Supermarket regression on the same server and code: `till-e2e` 15/15, `till-outage` all passed. The real-till upgrade check was SAFE.
- **Bugs caught before merge:**
  1. The print queue's "already running" guard could stick and silently stop kitchen printing. A unit test caught it; the queue is now single-flight and runs again when a job arrives mid-run.
  2. Restaurant rows that would not apply were not counted in the sync status. Found in review; they are now counted, with a new test.
  3. A global "service charge is taxable" flag conflicted with the per-tax-rule setting. Removed.
  4. Nothing stopped a second till in a restaurant branch from taking orders, which would have split the open tables. Only the hub the owner picked runs the restaurant now.
  5. The hub uploaded restaurant audit entries that today's cloud refuses. On the real server, a sold-out switch and a reprint left 2 failed uploads for ever. They are now kept on the hub until the cloud speaks protocol 6. The end-to-end check fails on the old code and passes on the new.
  6. A till only learned that the restaurant was switched on at its next sign-in. Every pull now carries the branch's modules.
- **Not verified:** printing on a physical thermal printer (needs the shop's hardware; part of REST-09).
- **Next:**
  - The owner publishes v1.0.23 and reviews gss PR #10 and gssdesktop PRs #7 and #8.
  - Then REST-08 (cloud receives closed orders) and the screens (REST-11, REST-12).
- **Also built: REST-07, kitchen screens on the Wi-Fi** (gssdesktop PR #8, stacked on #7).
  - Pairing with a one-time 6-digit code; only hashes are stored.
  - The page at `http://<hub>:7700/kds` runs in any browser.
  - Live events are filtered by station on the hub, and resume after a drop.
  - Tests: till 178/178, and real Chromium as the kitchen screen 18/18, including a Wi-Fi drop and catching up without a reload.
  - Not yet tried on two physical devices over Wi-Fi.
- **Test fault caught:** a restaurant test read the outbox in random-id order and passed or failed by chance. It now reads in queue order. The engine was not affected.

### 22 Oct 2026 — the cloud receives finished orders (REST-08)

- **Built (not deployed):** gss PR #12, stacked on #10.
  - The cloud takes a finished order from the hub once, in one transaction.
  - It checks the sender is the branch's hub, that everything named belongs to the branch, and that
    the bill adds up from the document's own frozen pricing, never from today's settings.
  - A closed order also becomes a Sale. Its lines add up to the bill, and it moves no stock.
  - Sold-out switches and restaurant audit entries are accepted from the hub.
  - New restaurant orders list, order detail and summary report.
  - Sync protocol 5 → 6.
- **Found and fixed on the hub first (gssdesktop PR #7):** an order's service charge and tax rates
  were not frozen. Any bill refresh used the latest settings, so a change in the cloud during service
  would have changed a printed bill. Each order now keeps the pricing it opened with. Real-server
  E2E: a service charge changed mid-service leaves the open table at 10%; the next table gets 15%.
- **Reports kept honest:**
  - A restaurant tax line counts as zero profit.
  - Item and category reports leave the bill's charges out.
  - The tax report takes restaurant taxes from the frozen bill lines, instead of calling restaurant
    food zero-rated.
- **Tests run (actual):**
  - gss full suite 307/307. The 13 ingest tests fail when the bill check or the hub check is
    removed.
  - gssdesktop 179/179 (`restaurant-lan`).
  - Restaurant E2E against the protocol-6 server 23/23.
  - Supermarket till-e2e 15/15 and till-outage 11/11 against the same server.
  - Real-till upgrade check SAFE.
  - Migration `restaurant 0003` forward, back and forward on a used database.
- **Known limits:** food has no cost price until REST-10; the cloud's live view of open orders
  (`order_snapshot`) is not built.
- **Review order for the owner:** gss #10 → #12; gssdesktop #7 → #8. Then the screens (REST-11,
  REST-12), then REST-09 on the shop's hardware.

### 22 Oct 2026 (afternoon) — production report crash, v1.0.23 live, hub screens (REST-11)

- **Production bug (HOTFIX-02, gss #13):** the Sales by item and Inventory valuation reports answered
  500 for every shop with sales or stock. Since stage 11, quantities are Decimals; these two reports
  still mixed them with floats. Fixed, with hand-computed tests on a weighed item and a sweep that
  calls every API page and fails on any crash. The sweep also found a second crash in the same report
  that the error page had not shown. All three tests fail on the old code. Merged with the owner's
  approval. **Not live yet:** Railway paused all deploys during a platform outage (status page:
  major outage since 21 Oct 07:24 UTC; builds coming back plan by plan). A retry is scheduled.
- **Security (OPS-06):** the crash pages showed production runs with `DEBUG=True` (the code default),
  exposing settings and request details on any error. With the owner's approval, `DEBUG=False` is
  saved on Railway and goes live with the fix.
- **Release (OPS-01):** the owner published v1.0.23 (latest, not a pre-release, all update files
  attached). Remaining: see the shop's till report 1.0.23.
- **Built (REST-11, gssdesktop #9):** the restaurant screens on the hub till. Floor with live table
  states, order pad (choices, notes, send, serve, void, discount, move, cancel), payment with change,
  bill and receipt printing, kitchen tab with unprinted tickets, sold out, owner set-up (station
  printers, kitchen screen pairing). Tested in real Chromium against the real engine (27/27).
- **Workflow gap fixed:** in a printer-only kitchen nobody could mark food "ready", so waiters could
  never serve and orders never closed. Items from a station without a kitchen screen are now served
  straight from the pass; stations with a screen still wait for "ready". ORDER_LIFECYCLE T5 updated.
- **Tests run (actual):** gss 269/269 (main); gssdesktop 180/180; hub screens 27/27; kitchen screen
  18/18.

### 22 Oct 2026 (evening) — restaurant set-up on the web (REST-12)

- **Merged by the owner:** gss #10 and gssdesktop #7. The stacked PRs (gss #12, gssdesktop #8) had
  landed on their intermediate branches, so catch-up PRs gss #15 and gssdesktop #10 brought them onto
  `restaurant` (both merged). The `restaurant` branches now hold REST-04 to REST-08; gssdesktop #9
  (REST-11) now targets `restaurant`.
- **Built (REST-12, gssfrontend #6 → new `restaurant` branch made from `main`):** owners switch the
  restaurant on per branch in Settings, then set the restaurant up on its own page (menu, choices,
  tables, kitchen stations, service charge, taxes with the activation safeguards, hub till) and read
  its reports. The page follows the branch the CEO is viewing.
- **Bugs found by the browser test:** the service charge could save as 0 (a second settings load in
  development overwrote what was typed); lists over 50 rows were cut; and, on `main`, the web Devices
  page ignored a CEO's branch switcher (HOTFIX-03, gss #16).
- **Tests run (actual):** web restaurant E2E 23/23 twice (real Chromium, real server, fresh
  Postgres, after the hub E2E uploaded an order); gss 272/272 on the #16 branch; web build and lint
  clean.
- **Still waiting:** the production deploy of HOTFIX-02 and `DEBUG=False` (Railway outage; the owner
  clicks Deploy once builds return). Phone (gssandriod) restaurant set-up not started.

### 22 Oct 2026 (night) — restaurant is a kind of shop (REST-13)

- **Owner's decision 13:** a restaurant is picked like gadgets or pharmacy. Built: each branch has its
  own kind (`Shop.shop_type`, ADR-025); sign-up offers "Restaurant & food"; adding a branch asks for
  its kind; the CEO can change a branch's kind in Settings (with a warning). The kind decides the
  branch's modules and stock categories. Existing branches keep their business's kind: nothing changes
  for them.
- **New restaurant owners:** after sign-up the dashboard lists the four first steps (menu, tables,
  kitchen, hub till). The phone app says the set-up is on the web.
- **PRs:** gss #17, gssfrontend #6 (extended), gssandriod #6 (new `restaurant` branch), gssdesktop #9
  (extended: restaurant stock categories on the till).
- **Tests run (actual):** gss 322/322; till 182/182; on a real server, two fresh databases, twice: hub
  23/23, web restaurant 29/29, restaurant sign-up 9/9, phone 10/10, supermarket till 15/15.
