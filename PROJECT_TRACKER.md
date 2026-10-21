# Benchline project tracker

The single source of truth for where Benchline stands. A new session should be able to resume from
this file alone. Last updated **21 Oct 2026**.

| Detail | Where |
| --- | --- |
| How the system works today | [`docs/architecture/CURRENT_ARCHITECTURE.md`](docs/architecture/CURRENT_ARCHITECTURE.md) |
| What is missing or broken | [`docs/architecture/CODEBASE_GAP_ANALYSIS.md`](docs/architecture/CODEBASE_GAP_ANALYSIS.md) |
| Why things are built the way they are | [`ARCHITECTURE_DECISIONS.md`](ARCHITECTURE_DECISIONS.md) |
| Restaurant reference study | [`docs/restaurant/FLOCAFE_ANALYSIS.md`](docs/restaurant/FLOCAFE_ANALYSIS.md) |
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

**Core POS engine (Parts 5–6 of the brief): complete.** Stages 0–11 are merged and the backend,
web and phone builds are released. **One release blocker:** till installers are published as GitHub
*drafts*, so tills cannot auto-update (OPS-01).

**Restaurant (Parts 7–14): stage 4 of the brief's governance (FloCafe analysis) is done and waiting
for review.** No restaurant code exists. Five questions for the owner block stage 5.

Milestones, as the brief defines them:

| Area | Designed | Implemented | Tested | Integrated | Released |
| --- | --- | --- | --- | --- | --- |
| Core engine stages 0–11, backend | Yes | Yes | Yes (266/266, 21 Oct) | Yes | **Yes**, Railway (latest deploy 21 Oct 07:06 Lagos) |
| Core engine stages 0–11, web | Yes | Yes | E2E scripts only | Yes | Deployed by Cloudflare on merge; **stage 11 deploy not checked** |
| Core engine stages 0–11, phone | Yes | Yes | E2E scripts only (shared with web) | Yes | APK built 21 Oct; **install on devices not verified** |
| Core engine stages 0–11, till | Yes | Yes | Yes (133/133, 21 Oct; two-till E2E) | Yes | **No.** Installer v1.0.22 is a draft (OPS-01) |
| Restaurant | Analysis only | No | No | No | No |

## 3. Task board

Statuses: `NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `REVIEW_REQUIRED`, `TESTING`, `COMPLETED`, `DEFERRED`.
A task is `COMPLETED` only with evidence recorded.

### Open

| ID | Task | Repo | Branch | Status | Depends on | Acceptance criteria | Tests | Evidence | Blocker | Next action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OPS-01 | Make till updates reach the shop | gssdesktop | `main` | BLOCKED | — | The shop's till offers v1.0.22 and installs it; `Device.app_version` shows it | Check the till's version after update | v1.0.17–v1.0.22 are `draft=true` (`gh api …/releases`, 21 Oct) | Publishing a release is a production action: **owner's approval** | Owner approves → publish v1.0.22 |
| OPS-02 | Future till builds publish themselves | gssdesktop | new branch | NOT_STARTED | OPS-01 | A push to `main` creates a published (not draft) release | Next CI run produces `draft=false` | — | — | Set `releaseType: "release"` in electron-builder config; PR |
| OPS-03 | Rotate the Postgres password shared in chat | Railway | — | NOT_STARTED | — | New password set; services redeployed and healthy | Health check + one till sync | — | Owner action | Owner |
| OPS-04 | Delete the production test sale | prod data | — | NOT_STARTED | — | Test sale gone, stock restored, audited | Audit log entry | — | Owner action | Owner |
| OPS-05 | Confirm stage 11 is live on web | gssfrontend | `main` | NOT_STARTED | — | `pos.gssolu.com` shows the "Sold by weight" option in Inventory | Manual check | — | — | Open Inventory → Add item |
| DOC-01 | Phase 0 baseline: tracker, decisions, architecture, gap analysis | gss | `docs/project-foundation` | REVIEW_REQUIRED | — | Owner has reviewed; findings cite files; no invented results | Fact check against code (done 21 Oct) | This PR | — | Owner review |
| REST-01 | FloCafe analysis (brief stage 4) | gss | `docs/project-foundation` | REVIEW_REQUIRED | DOC-01 | Feature inventory, licence, offline options compared, MVP workflow proposed | — | `docs/restaurant/FLOCAFE_ANALYSIS.md` | — | Owner review |
| REST-02 | Restaurant requirements (brief stage 5) | gss | — | BLOCKED | REST-01 | `docs/restaurant/REQUIREMENTS.md` with workflows, rules, acceptance criteria | — | — | 5 owner questions (FLOCAFE_ANALYSIS §7) | Owner answers |
| REST-03 | Restaurant architecture (brief stage 6) | gss | — | NOT_STARTED | REST-02, ADR-016 | `DOMAIN_MODEL.md`, `ORDER_LIFECYCLE.md`, `KITCHEN_WORKFLOW.md`, `API_SPECIFICATION.md`, `WEBSOCKET_EVENTS.md`, `OFFLINE_SYNCHRONIZATION.md` reviewed | — | — | — | — |
| REST-04 | Restaurant backend: menu, tables, orders, bills, tickets (online) | gss | — | NOT_STARTED | REST-03 | Full order → kitchen → payment → close workflow through the API, tenant-isolated, audited, idempotent | Django tests for every transition, permission and duplicate | — | — | — |
| REST-05 | Hub till: local server, KDS screen, kitchen printer | gssdesktop | — | NOT_STARTED | REST-04, ADR-016 | Same workflow with the internet unplugged | Till tests + two-device LAN E2E | — | — | — |
| REST-06 | Restaurant orders sync to the cloud | gss, gssdesktop | — | NOT_STARTED | REST-05 | A closed order reaches the cloud once, as one document; reports include it | Sync tests + E2E | — | — | — |
| ARCH-01 | Stop web/phone copy drift | gssfrontend, gssandriod | — | NOT_STARTED | — | One source for shared screens, or a written rule for keeping them in step | Builds of both | `diff` shows drift (gap C1) | Owner decision | Owner decides before REST UI work |
| QA-01 | Run web E2E in CI | gssfrontend | — | NOT_STARTED | — | Every PR runs the browser E2E against a throwaway Postgres | CI | — | — | — |
| QA-02 | Tests for Paystack payments and repairs | gss | — | NOT_STARTED | — | Paystack success/failure/duplicate webhook and repair flows covered | Django tests | `repairs/tests.py` has 0 tests | — | — |

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

## 4. Bugs and blockers

| ID | Problem | Impact | Status |
| --- | --- | --- | --- |
| B1 | Till installers are draft releases | Tills cannot auto-update | Open → OPS-01, OPS-02 |
| R1 | Realtime event row written after commit | Rare missed notification; must be fixed before kitchen events | Open → ADR-017 |
| C1 | Web/phone drift | Double work, fixes land in one app only | Open → ARCH-01 |

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

## 6. Roadmap (prioritised)

1. **OPS-01/02** — till updates. Nothing built for the till since v1.0.16 can reach the shop until
   releases are published.
2. **Owner review** of this baseline (DOC-01, REST-01) and answers to the five restaurant questions.
3. **ARCH-01** — decide how web and phone share code, before any restaurant screen is written.
4. **REST-02 → REST-03** — requirements, then the six design documents. Approval checkpoint.
5. **REST-04** — restaurant backend, online first, fully tested.
6. **REST-05 → REST-06** — hub till, KDS, kitchen printer, offline; then cloud sync.
7. **Verification and release readiness** for the restaurant (brief stages 9–10).
8. In parallel when there is room: **QA-01**, **QA-02**.

## 7. Decisions waiting for the owner

1. Publish till release v1.0.22 (OPS-01)?
2. How should web and phone share code (ARCH-01)?
3. Restaurant offline model: hub till (recommended) or printer fallback only (ADR-016)?
4. Restaurant per organization or per branch?
5. Counter service or table service first?
6. Kitchen output: screen, printer, or both?
7. VAT and/or service charge on restaurant bills?

## 8. Development history

| Date | What happened |
| --- | --- |
| Aug–Sep 2026 | Benchline split into four repos (`gss`, `gssdesktop`, `gssfrontend`, `gssandriod`); multi-branch tenancy, Paystack, real-time sync built |
| 11 Oct 2026 | "Update 3" prepared on the `benchline-*-update-2026-10-11` branches (idempotency keys, per-till receipt numbers) |
| 18 Oct 2026 | Core engine plan written; Update 3 + stages 1–2 released (PR #1 everywhere) |
| 19 Oct 2026 | CORS hotfix; stages 1b, 3–6, seller-only sales, day report released |
| 20 Oct 2026 | Owner-pay fix; stages 7–10 released; nightly job created on Railway |
| 21 Oct 2026 | Stage 11 (sell by weight) released; Phase 0 baseline and FloCafe analysis written (this file) |

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
