# Benchline project tracker

The single source of truth for where Benchline stands. A new session should be able to resume from
this file alone. Last updated **21 Oct 2026**.

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

**Restaurant (Parts 7–14):** requirements and design reviewed and merged (gss PR #7). Building has
started on the `restaurant` branches: configuration, packages and permissions first, then kitchen
routing. Nothing restaurant-related deploys until the complete workflow passes (REST-09).

Milestones, as the brief defines them:

| Area | Designed | Implemented | Tested | Integrated | Released |
| --- | --- | --- | --- | --- | --- |
| Core engine stages 0–11, backend | Yes | Yes | Yes (266/266, 21 Oct) | Yes | **Yes**, Railway (latest deploy 21 Oct 07:06 Lagos) |
| Core engine stages 0–11, web | Yes | Yes | E2E scripts only | Yes | Deployed by Cloudflare on merge; **stage 11 deploy not checked** |
| Core engine stages 0–11, phone | Yes | Yes | E2E scripts only (shared with web) | Yes | APK built 21 Oct; **install on devices not verified** |
| Core engine stages 0–11, till | Yes | Yes | Yes (137/137 with FIX-01; two-till E2E incl. outage) | Yes | **Pending:** v1.0.23 passed its checklist; the owner publishes it (OPS-01) |
| Branch modules and packages | Yes | Yes (`restaurant` branch, PR #8) | Yes (26 tests; full suite 292/292) | No | No |
| Restaurant | Yes | Configuration + routing (PR #8, gssdesktop PR #6) | Partly | No | No |

## 3. Task board

Statuses: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `REVIEW_REQUIRED`, `TESTING`, `COMPLETED`, `DEFERRED`.
A task is `COMPLETED` only with evidence recorded.

### Open

| ID | Task | Repo | Branch | Status | Depends on | Acceptance criteria | Tests | Evidence | Blocker | Next action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OPS-01 | Release the till (gate: `docs/RELEASE_CHECKLIST.md`) | gssdesktop | `main` | BLOCKED | — | The shop's till installs a published build and reports its version | Checklist items 1–8 | **v1.0.23 passed** (21 Oct): build green, checksum = `latest.yml`, packaged app is 1.0.23 with the fix and the production URL, same data folder; 137/137; real-till upgrade SAFE; till-e2e 15/15, till-e2e2 13/13, till-weight 4/4, till-outage 11/11. Item 7 (physical Windows upgrade) covered by unchanged installer setup and dependencies since 11 Oct | **This session cannot publish GitHub releases (403)**: the owner publishes v1.0.23 | Owner: Releases → v1.0.23 → Publish; then check the till's `app_version` |
| OPS-03 | Rotate the Postgres password shared in chat | Railway | — | NOT_STARTED | — | New password set; services redeployed and healthy | Health check + one till sync | — | Owner action | Owner |
| OPS-04 | Delete the production test sale | prod data | — | NOT_STARTED | — | Test sale gone, stock restored, audited | Audit log entry | — | Owner action | Owner |
| OPS-05 | Confirm stage 11 is live on web | gssfrontend | `main` | NOT_STARTED | — | `pos.gssolu.com` shows the "Sold by weight" option in Inventory | Manual check | — | — | Open Inventory → Add item |
| REST-02 | Restaurant requirements (brief stage 5) | gss | PR #7 | COMPLETED | Owner decisions 21 Oct | Workflows, rules, tax/charges with sources, acceptance criteria | — | `docs/restaurant/REQUIREMENTS.md`; owner instructed review + merge (decision 9) | — | — |
| DOC-02 | Review of the restaurant design (PR #7) | gss | `design/restaurant-foundation` | COMPLETED | REST-03 | Full diff checked against the owner's list; issues fixed | Link check of every document | 14 issues fixed (session log, 21 Oct evening) | — | Merge |
| CORE-13 | Packages (`Package`, `Subscription.package`, feature checks) | gss, clients | `restaurant-config` (PR #8 → `restaurant`) | REVIEW_REQUIRED | CORE-12 | Existing subscriptions unchanged (Retail); module switch-on needs the feature | Django tests | Package model, starter packages, trial = all features | Add-on price (billing only) | — |
| REST-10 | Link stocked menu items (bottled drinks) and recipes to stock | gss, gssdesktop | — | DEFERRED | REST-09 | Restaurant sales move stock for linked items | — | First release moves no stock (DOMAIN_MODEL §1) | — | — |
| REST-03 | Restaurant architecture (brief stage 6) | gss | PR #7 | COMPLETED | REST-02 | Domain model, state transitions T1–T13, kitchen workflow, offline sync, events, API | — | `TARGET_ARCHITECTURE.md` + six `docs/restaurant/` documents; reviewed (DOC-02) | — | — |
| CORE-12 | Branch modules (`BranchModule`, `RequiresModule`, `/api/me` modules) | gss, all clients | `restaurant-config` (PR #8 → `restaurant`) | REVIEW_REQUIRED | REST-03 | Existing branches unchanged (retail, + service where enabled); a branch without `restaurant` gets 403 `module_disabled` | Django tests incl. cross-business isolation | BranchModule + endpoint + `/api/me`; 26 restaurant tests; full suite 292/292 | — | — |
| REST-04 | Cloud: restaurant configuration models + API + pull types | gss | `restaurant-config` (PR #8 → `restaurant`) | REVIEW_REQUIRED | CORE-12, CORE-13 | Owner can set up menu, modifiers, tables, stations, taxes, service charge; tenant-isolated; audited | Django tests per endpoint, permission and isolation | Configuration API, tax activation safeguards, opt-in pull types | — | — |
| REST-05 | Shared tax / service-charge engine (JS + Python, shared vectors) | gss, gssdesktop | — | NOT_STARTED | REST-04 | Identical results in both languages, incl. the Lagos example in REQUIREMENTS §4 | Shared vectors | — | — | — |
| REST-06 | Hub order engine T1–T13 + kitchen printing | gssdesktop | `restaurant-routing` (gssdesktop PR #6 → `restaurant`) | IN_PROGRESS | REST-05 | Table-service workflow on the hub, tickets printed per station, everything offline | Till tests per transition, idempotency, restart | Kitchen routing done (JS = Python, shared cases); order engine next | — | — |
| REST-07 | Hub local server, screen pairing, kitchen screen (SSE) | gssdesktop | — | NOT_STARTED | REST-06 | Screen shows and updates tickets live; catches up after a Wi-Fi drop; station isolation | Two-device LAN E2E | — | — | — |
| REST-08 | Cloud ingest (`order_doc`), `Sale` on close, restaurant reports | gss, gssdesktop | — | NOT_STARTED | REST-06 | A closed order arrives once with matching totals; Z-report and reports include it | Sync tests + E2E | — | — | — |
| REST-09 | Restaurant verification and release readiness (brief stages 9–10) | all | — | NOT_STARTED | REST-07, REST-08 | Acceptance criteria 1–10 in REQUIREMENTS §5 | Full workflow online, offline, with restarts | — | — | — |
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

No migration has dropped data. Backups before releases are manual `pg_dump` (Railway plan has none).

### Till (SQLite, `migrate()` in `db.js`)

`sync_queue` outbox columns (stage 1); `sale_payments` (stage 2); `shifts` upload state (stage 3);
`stock_movements`, `stock_docs` (stage 4); `sale_returns`, `sale_return_lines` (stage 8);
`promotions` (stage 10); `products.sold_by_weight` (stage 11). Each is checked on a copy of the
shop's real till database before release (`tools/check-upgrade-on-copy.js`, SAFE on 20 Oct).

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
