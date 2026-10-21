# Architecture decisions

Each record: the situation, the options, what was chosen and why, what it costs, and its status.
**Accepted** = approved by the owner and in production. **Proposed** = written for review, not
built. Newest proposals are at the end.

Context for all of them: [`docs/architecture/CURRENT_ARCHITECTURE.md`](docs/architecture/CURRENT_ARCHITECTURE.md).

---

## ADR-001 Keep the existing stacks; offline is a platform capability

- **Context.** Benchline already runs on Electron + React + SQLite (till), Django + DRF + Channels +
  PostgreSQL (cloud), React (web) and React + Capacitor (phone).
- **Options.** Rewrite on a new stack (e.g. Flutter, or a FloCafe/SaleFlex fork) · keep and strengthen.
- **Decision.** Keep the stacks. Offline operation is a capability of the till platform, not a
  feature of one screen.
- **Why.** The shop is live; a rewrite would stop delivery for months and lose tested behaviour.
- **Consequences.** New domains (restaurant) must fit Django apps + the till's SQLite engine.
- **Status.** Accepted (Oct 2026, owner's brief).

## ADR-002 Reference code: ideas only

- **Context.** SaleFlex.PyPOS and FloCafe are references.
- **Facts.** SaleFlex is **AGPL-3.0**: copying its code into a hosted product would oblige us to
  publish Benchline's source to its users. FloCafe is **MIT**: code may be reused with its copyright
  notice kept, but its stack differs from ours.
- **Decision.** Learn concepts from both; copy no code from SaleFlex at all; copy FloCafe code only
  with an explicit note and its notice. To date no code has been copied from either.
- **Status.** Accepted (21 Oct 2026, licenses checked from each repo's `LICENSE`).

## ADR-003 Durable outbox, one document per business action

- **Context.** A sale used to upload as a header op + one op per line; the cloud could hold half a
  sale (gap G1).
- **Options.** Keep per-row ops · upload one document per action.
- **Decision.** The till writes the business rows and one `sync_queue` row in the **same SQLite
  transaction**. A sale goes up as one `sale_doc` (header, lines, allocations, payments, movements),
  applied in **one** Postgres transaction. Shifts, returns, damage and counts follow the same idea.
- **Why.** Either the whole sale exists in the cloud or none of it does.
- **Consequences.** New document types need a handler in `sync/views.py` `_HANDLERS` and a protocol
  bump (now 5) so older tills are recognised.
- **Status.** Accepted (stage 2).

## ADR-004 Idempotency everywhere a client can retry

- **Decision.** Till ops: the outbox row's UUID becomes `SyncOperation.id`; inserting it inside the
  apply transaction is the duplicate lock; a `payload_hash` rejects an id reused with different
  content. Web/phone writes: `Idempotency-Key` header stored as `IdempotencyRecord` per user.
- **Why.** A lost response must never create a second sale or payment.
- **Status.** Accepted (Update 3, stage 1).

## ADR-005 The branch of a till's data comes from the device, not the user

- **Context.** A CEO with two branches selling on a till got 403 on every push (G3).
- **Decision.** Sync resolves the branch from `X-Device-ID` → `Device.shop`, then checks the user
  may use that device (`core.utils.user_may_use_branch_device`).
- **Status.** Accepted (stage 1).

## ADR-006 Stock is a ledger of movements, never an overwritten number

- **Context.** Stock was a batch quantity edited in place, with no reason or author (G7).
- **Options.** Last-write-wins quantities · version numbers · movement ledger.
- **Decision.** Every change is a signed `StockMovement` (opening, sale, return, damage, count
  adjustment, transfer) with author, reason and source. Batches keep `quantity_remaining` as a fast
  cache. Tills apply cloud movements as **deltas**. A nightly job reports any batch whose ledger does
  not add up. Quantities are `Decimal(12,3)`.
- **Consequences.** Every writer must go through `inventory/ledger.py`.
- **Status.** Accepted (stage 4; decimals stage 11).

## ADR-007 Money arrives as append-only payments

- **Context.** Installment payments overwrote each other (G2).
- **Decision.** Each payment is its own `SalePayment` row; `amount_paid` is derived as their sum.
- **Status.** Accepted (stage 2).

## ADR-008 WebSockets notify; HTTP and the database are the truth

- **Decision.** Events carry type, ids and hints only. They are stored (`RealtimeEvent`) with a
  per-shop sequence so a client can tell it missed something, then re-fetch over HTTP. One
  `sync.applied` event per push batch, not per op.
- **Known limit.** The event row is written after the business commit (gap R1). Acceptable for retail,
  not for kitchen tickets (see ADR-017).
- **Status.** Accepted (Update 3, stage 1b).

## ADR-009 Subscription gate with no grace period

- **Decision.** When a subscription ends, the API answers 402 to everything but sign-in, status and
  payment. The till keeps selling, shows "paused" and keeps its queue. No grace days.
- **Status.** Accepted (stage 5, owner's decision).

## ADR-010 Audit forever, sync history 90 days

- **Decision.** `AuditLog` is append-only (model refuses update and delete) and never pruned.
  `SyncOperation` and `RealtimeEvent` are pruned after 90 days, except rows a still-offline till may
  resend and each shop's newest event.
- **Status.** Accepted (stages 5–6; nightly job on Railway since 20 Oct 2026).

## ADR-011 Who may change stock and money

- **Decision.** Sellers may record damage, with a mandatory reason. Only owners/branch managers may
  record stock counts. Sellers see only their own sales. Any owner (not only the CEO) may pay the
  subscription. Every operation records author, time and, where relevant, reason.
- **Status.** Accepted (stages 4–5, owner's decisions).

## ADR-012 Promotions are data, evaluated identically everywhere

- **Decision.** A promotion is a row of rules. Python (server) and JavaScript (till, web, phone)
  evaluators are kept identical by 14 shared test vectors.
- **Status.** Accepted (stage 10).

## ADR-013 Offline overselling is detected, not prevented

- **Context.** Two offline tills can both sell the last unit.
- **Decision.** Never block a sale for this (the customer already has the goods). The cloud marks
  `stock_shortfall` on the later sale and alerts the owner.
- **Why.** No code can prevent it while tills cannot reach each other; pretending otherwise would
  lose real sales.
- **Status.** Accepted (Update 3).

---

## Proposed for the restaurant domain (need approval)

## ADR-014 Restaurant is its own Django app on the shared core

- **Context.** The brief separates supermarket and restaurant domains.
- **Options.** Add restaurant fields to `sales`/`inventory` · a new `restaurant` app that reuses the core.
- **Proposal.** A new app (`gss/restaurant/`) for menu, modifiers, tables, orders, bills, kitchen
  stations and tickets. It **reuses** tenancy (`Organization`/`Shop`, `ShopScopedMixin`), staff and
  capabilities (new roles: waiter, cook, cashier), shifts and cash, the append-only payment rule,
  `AuditLog`, devices and the realtime layer. It does **not** reuse `Sale` as the order: a sale is
  final when created, an order changes for an hour. A closed order may *also* write a `Sale` for
  existing reports (to be decided in the design stage).
- **Status.** Proposed (21 Oct 2026); detailed in `docs/restaurant/DOMAIN_MODEL.md`. Awaiting review.

## ADR-015 Order items and kitchen ticket items are separate

- **Context.** FloCafe drives its kitchen straight off order lines. The brief asks to keep them apart.
- **Proposal.** `OrderItem` is what the customer pays for; `KitchenTicketItem` is the work a station
  must do, pointing at the order item. Status vocabulary borrowed from FloCafe (`pending, preparing,
  ready, served` + void states), edit rules borrowed too (more = new line, less = waste, no swap once
  cooking).
- **Why.** Kitchen actions (fire, recall, re-send, void as waste) must not rewrite the bill.
- **Status.** Proposed (21 Oct 2026). Detail in `docs/restaurant/FLOCAFE_ANALYSIS.md` §3.

## ADR-016 Restaurant offline: a hub till on the local network

- **Options.** A cloud only · B hub till on the LAN · C peer-to-peer · D cloud + printer fallback
  (compared in `docs/restaurant/FLOCAFE_ANALYSIS.md` §5).
- **Decision (owner, 21 Oct 2026).** B: a local restaurant server on the shop's Wi-Fi, with kitchen
  printing as an extra fallback (printing alone is not an offline strategy). Audit the desktop first
  and prefer the simplest reliable LAN design.
- **Design after the audit.** The existing till is the hub: Node's built-in HTTP server inside the
  Electron main process, **Server-Sent Events** for live updates (no WebSocket library), existing
  silent printing for tickets, one hub per branch enforced by the cloud. No separate server, database
  or Redis on site. Steps: hub + printers + kitchen screens first; waiter phones second.
- **Cost.** The hub is a single point of failure (UPS, restart-safe SQLite, open-order mirror to the
  cloud); firewall rule and a fixed IP per restaurant; a local server to secure (pairing tokens).
  Estimate in `docs/architecture/TARGET_ARCHITECTURE.md` §2.
- **Status.** Accepted (decision); detailed design awaiting review.

## ADR-017 Kitchen events use a transactional outbox

- **Context.** Gap R1: today's event row is written after the commit.
- **Proposal.** For restaurant events (order sent, ticket created, item status changed), write the
  event row **inside** the business transaction and send it after commit; clients resume from the
  last sequence they saw.
- **Status.** Proposed (21 Oct 2026).

## ADR-018 Code sharing: consistent behaviour, not identical code

- **Decision (owner, 21 Oct 2026).** Keep web, Android and desktop as separate apps. The Django API is
  the contract and enforces every money and stock rule. Share code only where it truly removes
  duplication: documented API schemas and pure business calculations that must run offline (the
  promotions engine today, the restaurant tax engine next), kept identical to Python by shared test
  vectors and, proposed, a CI identity check (QA-03).
- **Rejected.** Forcing Android onto the web app's codebase to maximise reuse.
- **Status.** Accepted.

## ADR-019 Branch modules

- **Decision (owner, 21 Oct 2026).** One business may run supermarket branches, restaurant branches or
  both; each branch has its own configuration; tenant isolation stays strict.
- **Proposal.** A `BranchModule` row per (branch, module) for `retail`, `restaurant`, `service`;
  `Organization.business_type` becomes only the retail catalogue flavour; `RequiresModule` on
  endpoints and sync handlers; existing branches migrated to `retail` (+ `service` where on today).
  Options compared in `docs/architecture/TARGET_ARCHITECTURE.md` §1.
- **Status.** Decision accepted; design awaiting review.

## ADR-020 The draft release is the till release gate

- **Context.** CI creates every till installer as a GitHub draft; tills only see published releases.
- **Options.** Make CI publish automatically · keep drafts and publish by hand after checks.
- **Decision (owner, 21 Oct 2026).** Keep drafts. Publish only after `gssdesktop/docs/RELEASE_CHECKLIST.md`
  passes. Automatic publishing would send every merge straight to the shop.
- **Status.** Accepted.

## ADR-021 Hub for open orders, cloud for everything else

- **Proposal.** The hub till is the system of record for **open** orders, tickets, bills and payments
  in progress; the cloud is the record for configuration, **closed** orders, reports and audit. Every
  row keeps the hub's UUID. Only the branch's hub may send restaurant ops.
- **Why.** One writer means no conflicting copies of an order, and the restaurant keeps running
  offline.
- **Status.** Proposed (21 Oct 2026); `docs/restaurant/OFFLINE_SYNCHRONIZATION.md`.

## ADR-022 Taxes are configurable rules, frozen on every bill

- **Decision (owner, 21 Oct 2026).** No hard-coded rate. Any number of tax rules per branch (rate,
  inclusive/exclusive, categories covered, applies to service charge, validity dates), an optional
  service charge, tips separate. Each bill stores the rates and amounts it used.
- **Facts checked (21 Oct 2026).** VAT 7.5% under the Nigeria Tax Act 2025 (from 1 Jan 2026); small
  businesses (≤ ₦100m turnover) exempt from charging VAT; Lagos restaurant consumption tax 5% on the
  bill excluding VAT. Sources in `docs/restaurant/REQUIREMENTS.md` §4. Not legal advice.
- **Safeguards (owner, 21 Oct 2026, decision 4).** New rules start inactive; activating needs the
  branch's state, VAT registration and a confirmed treatment; two rules hitting the same item need
  explicit confirmation; customer taxes are kept apart from the business's own taxes; receipts and a
  taxes-collected report show each tax.
- **Status.** Accepted.

## ADR-023 A closed restaurant order also becomes a `Sale`

- **Proposal.** When the cloud applies an `order_doc`, it also writes a `Sale` (`source='restaurant'`,
  linked to the order, lines by name, taxes and payments), so existing reports, payment summaries and
  Z-reports include restaurant takings without rewriting them. Restaurant-only detail (tickets,
  voids, waste, kitchen times) stays in the restaurant tables.
- **Alternative.** Teach every report to read two sources: more work, and easy to miss one.
- **Status.** Proposed (21 Oct 2026).

## ADR-024 Packages decide features; branches cost the same

- **Decision (owner, 21 Oct 2026, decision 3).** Base subscription and per-branch price are the same
  for a restaurant and a supermarket branch. Restaurant capabilities (tables, kitchen, ticket
  printing, waiters, recipe costing) are controlled by the business's **package**.
- **Design.** A `Package` (code, name, feature codes, optional add-on price) referenced by
  `Subscription.package`; every existing subscription gets "Retail" (+ `service` where on today).
  Switching a module on or saving restaurant configuration needs the feature (403 `not_in_package`).
  Billing never stops a hub mid-service, in line with the existing rule that subscriptions control
  cloud services only.
- **Open.** Add-on price of the restaurant package(s).
- **Status.** Accepted (decision); design in `docs/architecture/TARGET_ARCHITECTURE.md` §1.
