---
name: charity-onboarding
description: >-
  End-to-end runbook for onboarding a new charity in the FFC-Cloudflare-Automation repo — from a
  domain name to a live, analytics-wired 501(c)(3) website. Use this when asked to "onboard",
  "provision", "set up", "establish the repo for", or "run the full chain" for a charity/domain, or
  when asked which workflow to run for any charity-onboarding step (WHMCS application lookup, domain
  purchase/DNS, M365 email, website repo, GA4/GTM analytics, WHMCS account/order). Names the exact
  workflows in order, the gates, and the gotchas that have burned prior sessions.
---

# Charity onboarding (full chain)

This skill is the map. It tells you **which workflow to run, in what order, and what will bite
you**. For the narrative and per-phase "done when" checks, read
[`docs/charity-onboarding-lifecycle.md`](../../../docs/charity-onboarding-lifecycle.md) — this skill
is the fast index into it.

**Workflow numbers here are the display numbers** (the `NN.` prefix in the Actions UI), _not_ the
file names — the two differ. Map display→file via
[`docs/workflow-catalog.json`](../../../docs/workflow-catalog.json).

## Read these first (don't rediscover the hard way)

- [`docs/charity-onboarding-lifecycle.md`](../../../docs/charity-onboarding-lifecycle.md) — the full
  runbook, per-phase, with unhappy paths and a worked example.
- [`docs/restored-radiance-first-fullchain-retro.md`](../../../docs/restored-radiance-first-fullchain-retro.md)
  — the first real run. **Read the "identify by domain, not masked name" lesson before Phase 0.**
- [`docs/workflow-safety-and-approvals.md`](../../../docs/workflow-safety-and-approvals.md) — which
  runs are read-only vs which pause for an approval gate.
- [`docs/azure-oidc-federated-credentials.md`](../../../docs/azure-oidc-federated-credentials.md) —
  identity/OIDC map; the `m365-prod` credential-typo repair; `az`-from-sandbox recipe.
- Service deep-dives when you touch that service:
  [`docs/whmcs-apim-routing.md`](../../../docs/whmcs-apim-routing.md),
  [`docs/whmcs-charity-onboarding.md`](../../../docs/whmcs-charity-onboarding.md),
  [`docs/google-api.md`](../../../docs/google-api.md),
  [`docs/m365-domain-and-dkim.md`](../../../docs/m365-domain-and-dkim.md),
  [`docs/cloudflare-domain-registration.md`](../../../docs/cloudflare-domain-registration.md).
- `CLAUDE.md` / `AGENTS.md` — environment, dispatch mechanics, and safety model.

## The chain at a glance

```
(0) Find the application   → 221 (search by domain) → confirm client id → 219 (read full app)
(1) Domain under CF        → 113 (buy) OR 102 (add existing)          ⏸ cloudflare-prod-write
(2) DNS + M365 email       → 103 (enforce standard) · 305/304/303/301  ⏸ cloudflare-prod-write, m365-prod
(3) Website repo + Pages   → 701 (assign the issue)                   ⏸ github-prod, cloudflare-prod-write
(4) Rebrand the site       → edit FFC-EX-<domain>/src/lib/site.config.ts from the application (PR)
(5) Analytics              → 505 (GA4 property) → 503 (GTM container) → wire analytics.config.ts ⏸ google-prod-write
(6) WHMCS account + order  → 204 (charity onboard) + status markers via 212/211 ⏸ whmcs-prod
(7) Ongoing support        → 206 (issue→ticket) · 209/210 triage · 207 respond
```

## Phase 0 — Find the application (ALWAYS start here)

1. **`221. WHMCS - Application Search`** with the **domain** (or org name) as the query. Returns the
   **client id** + readable application (org name, mission, desired domain, legal status). Ungated
   read? No — 221 currently runs on gated `whmcs-prod`. The read-only detail view
   **`219. WHMCS - Application Detail`** runs on the ungated `whmcs-prod-read` env; use it once you
   have the id.
2. Confirm the client id before doing anything else.

> **The #1 trap:** do **not** identify the application from the masked triage tables (`209`/`210`).
> Those show the **applicant's personal first name**, not the org. The org name lives **only in the
> mission text** (a product custom field) — there is no client-level "Organization Name" field, and
> `companyname` is always empty. Matching on a name-initial guessed from the org name finds the
> **wrong charity** (this exact mistake happened on the first full-chain run). If the charity texted
> an **order number**, that maps straight to the client id — fastest confirm of all.

## Phase 1 — Domain under FFC Cloudflare ⏸

- **FFC buys it:** **`113. Domain - Registrar Search / Check / Register`**. Live registration needs
  `mode=execute-register` **and** a typed `confirm_domain` exact match.
- **Existing domain:** **`102. Domain - Add to FFC Cloudflare + WHMCS Nameservers`**.
- After registrar purchase, the zone auto-creates; verify with `101`.

## Phase 2 — DNS + Microsoft 365 email ⏸

- **`103. Domain - Enforce Standard (GitHub Apex + M365)`** — GitHub Pages apex A/AAAA + `www`, plus
  M365 MX/SPF/DMARC. **Defaults to `dry_run=true`** — read the preview, then re-run `dry_run=false`
  and approve the gate.
- Add the domain to the tenant if new: **`305. M365 - Add Tenant Domain`**, then
  **`304. M365 - Enable DKIM`**; verify with **`301`/`303`**.
- **M365 was broken by a federated-credential typo** (`AADSTS700213`). If any m365 job fails Azure
  login, it's almost certainly that — see the repair in `docs/azure-oidc-federated-credentials.md`.

## Phase 3 — Website repo + Pages ⏸

- File template **07** (admin-minimal) or **02** (full metadata) and **assign** it — assignment
  fires **`701. Website - Provision`**, which creates `FFC-EX-<domain>` from the template, enables
  Pages, adds the Technical POC as `maintain`, and (if the zone is in FFC CF) enforces Pages DNS.
  The `repo` job is **chained behind** the DNS approval.
- **Gotcha:** in the issue body, keep all prose **above** the `###` field headings — trailing text
  is slurped into the last field and silently drops the maintainer login.

## Phase 4 — Rebrand the site from the application

- In `FFC-EX-<domain>`, the single customization point is **`src/lib/site.config.ts`** (name,
  tagline, description, `url`, `ein`, `nonprofitStatus`, contact). Fill it from the Phase 0
  application. Open a **PR** (never push the default branch); run `npm run check:drift` and the
  rebrand check before merging. Watch for em-dashes in card text (some renderers break) and
  Copilot's tagline nits.

## Phase 5 — Analytics ⏸ (google-prod-write)

- **`505. Google GA4 Property Provision`** — creates a GA4 property + web stream under the
  **`FFC Supported Sites`** account (that's the real account name — not "Charities"/"Supported
  Charities"). Idempotent by stream `defaultUri`. Returns `G-XXXXXXXXXX`.
- **`503. Google GTM Provision`** — creates the container, seeds the GA4 tag, publishes. Needs the
  `tagmanager.edit.containerversions` scope on the DWD grant (already added).
- Wire both ids into `FFC-EX-<domain>/src/lib/analytics.config.ts` (PR).
- Both run via **domain-wide delegation**: the `ffc-workspace-admin` SA impersonates
  `clarkemoyer@freeforcharity.org`. Key comes from KV
  (`wr-all-cbm-google-workspace-service-account-key`).

## Phase 6 — WHMCS account + onboarding order ⏸

- **`204. WHMCS - Charity Onboard`** — creates the client, contacts, and onboarding order.
  **Defaults to `dry_run=true`**, and is **idempotent** (re-run reports `existing`/`skipped`). Read
  the redacted preview before flipping `dry_run=false`.
- Add **status-marker products** that describe what's now true (e.g. _Domain Registered in
  Cloudflare (Registrar)_, _Hosted by GitHub Pages_) — additive, alongside the onboarding product.
  Catalog adds via **`212. WHMCS - Product Add`**; cancel a mistaken order one at a time with
  **`211. WHMCS - Order Update`** (`action=cancel`).

## Phase 7 — Ongoing support

- Charities file template **08/09** → **`206. WHMCS - Issue → Ticket`**. Triage with **`209`**
  (tickets) / **`210`** (orders); reply with **`207. WHMCS - Ticket Respond`** (dry-run default).

## How to actually run a workflow (mechanics that trip agents)

- **New workflows must be merged to `main` before `workflow_dispatch` by filename works** — a fresh
  file on a branch dispatches `404`. Merge the PR first.
- **All dispatch inputs must be strings.** `issue_number: "609"`, never `609` — a numeric value
  fails `422 Invalid value for input`.
- **From the web sandbox:** MCP GitHub tools **can** dispatch `workflow_dispatch` workflows and
  trigger `issues`-event workflows (create+assign an issue via MCP). MCP **cannot** approve
  environment gates — a human reviewer (`clarkemoyer`) approves those (UI → _Review deployments_, or
  `gh api … pending_deployments`).
- **Gates:** `cloudflare-prod-write`, `whmcs-prod`, `m365-prod`, `github-prod`, `google-prod-write`
  pause at `status: waiting`. The `*-read` envs, `zeffy-prod`, and `whmcs-prod-read` are ungated.
  `status: waiting` is the gate, not a failure.
- **Direct WHMCS read from the sandbox:** `az` device-auth → fetch `read-all-ffc-whmcs-*` secrets
  from KV → POST to the APIM gateway with the `Ocp-Apim-Subscription-Key` header (see `CLAUDE.md`).
- **Azure AD IAM writes are blocked by the harness** — hand credential fixes to a human with the
  exact `az` command from `docs/azure-oidc-federated-credentials.md`.
- Never push to `main`; PRs merge via the merge queue. Resolve Copilot review threads before
  queuing.

## Merged-application → shipped-charity checklist

- [ ] `221` → confirmed client id (not a masked-name guess)
- [ ] Domain in CF zone (`113`/`102`), active
- [ ] `103` enforce-standard applied (apex + `www` + M365), DKIM valid
- [ ] `701` provisioned `FFC-EX-<domain>`, Pages live over HTTPS, maintainer added
- [ ] `site.config.ts` rebranded from the application (PR merged, drift clean)
- [ ] `505` GA4 property + `503` GTM container, ids wired in `analytics.config.ts`
- [ ] `204` client + onboarding order + correct status markers (in `210` triage)
- [ ] Support path live (template 08/09 → `206`)
