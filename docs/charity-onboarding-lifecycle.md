# Charity onboarding lifecycle (end-to-end runbook)

This is the single narrative that ties together the individual workflows used to bring a new charity
fully online: **domain → Cloudflare zone → M365 email → website → WHMCS account → ongoing support.**
Each phase links to its deep-dive doc; this page is the order, the hand-offs, and the "is this step
done?" checks.

Workflow numbers below are the display numbers shown in the Actions UI (and in
[.github/workflows/README.md](../.github/workflows/README.md)). For how safe each run is and which
ones pause for approval, see [workflow-safety-and-approvals.md](workflow-safety-and-approvals.md).

## How work is requested

Admins rarely dispatch workflows by hand — the **issue templates** under `.github/ISSUE_TEMPLATE/`
are the front door. Filing (and, for provisioning, assigning) an issue triggers the matching
workflow. The relevant intake templates:

- **01 — Purchase & Add New .org Domain** → registrar purchase path.
- **03 — [ADMIN ONLY] Add Existing Domain to Cloudflare** → bring an existing domain under FFC.
- **02 — Website Request** / **07 — [ADMIN ONLY] Provision Website (Minimal)** → website repo + DNS.
- **08 — Support Request** / **09 — Break/Fix** → opens a WHMCS ticket (ongoing support).

## The lifecycle at a glance

```
(0) Status check ──► (1) Domain in Cloudflare ──► (2) Standard DNS + M365 ──►
(3) Website repo + Pages ──► (4) WHMCS account + order ──► (5) Ongoing support
                                                   └──► (R) Reconcile / audit (anytime)
```

---

## Phase 0 — Status check (always start here)

- **Run:** **01. Domain - Status (All Sources)** — read-only across Cloudflare + M365 (+ WHMCS).
- **Why:** establishes the starting state so you don't re-create a zone or double-onboard.
- **Done when:** you know whether the domain already has a Cloudflare zone, M365 presence, and a
  WHMCS client.

## Phase 1 — Domain under FFC Cloudflare

Pick the path that matches the domain's origin:

- **New domain (FFC buys it):** file template **01**; the registrar workflows (**12 / 20 - Registrar
  Search / Register**) handle availability, purchase, and zone creation. Live registration needs
  `mode=execute-register` **and** a typed `confirm_domain` match. See
  [cloudflare-domain-registration.md](cloudflare-domain-registration.md).
- **Existing domain (transfer in / point NS):** file template **03**, then **02. Domain - Add to FFC
  Cloudflare + WHMCS Nameservers** creates the zone and (optionally) repoints WHMCS nameservers. For
  full registrar transfers, see [domain-transfer-automation.md](domain-transfer-automation.md)
  (preflight **14** → EPP probe **16** → post-transfer verify **25**).
- **Done when:** the Cloudflare zone exists and is active (nameservers delegated). Re-run **01** to
  confirm.

## Phase 2 — Standard DNS + Microsoft 365 email

- **Run (dry-run first):** **03. Domain - Enforce Standard (GitHub Apex + M365)**. This applies the
  FFC-standard records — GitHub Pages apex A/AAAA + `www` CNAME, and M365 MX/SPF/DMARC — and can
  enable DKIM. It **defaults to `dry_run=true`**; read the preview, then re-run with `dry_run=false`
  and approve the `cloudflare-prod-write` / `m365-prod` gate. See
  [enforce-standard-workflow.md](enforce-standard-workflow.md).
- **Add the domain to the M365 tenant** if it isn't already: **24. M365 - Add Tenant Domain**, then
  enable mail auth with **23. M365 - Enable DKIM**. Verify with **20. M365 - Domain Preflight** /
  **22. M365 - Domain Status + DKIM**. See [m365-domain-and-dkim.md](m365-domain-and-dkim.md) and
  the combined runbook
  [end-to-end-testing-m365-cloudflare.md](end-to-end-testing-m365-cloudflare.md).
- **Done when:** **07. DNS - Audit Compliance** reports the domain compliant, and DKIM validates.

## Phase 3 — Website repo + GitHub Pages

- **Trigger:** file template **02** (full metadata: org name, footer contact, board, socials) or
  **07** (admin-minimal, domain only) and **assign** the issue — assignment fires **15. Website -
  Provision**.
- **What it does:** creates `FFC-EX-<domain>` from the FFC template, enables GitHub Pages, adds the
  Technical POC as a `maintain` collaborator, and — only if the zone is in FFC Cloudflare — enforces
  apex + `www` Pages DNS. The `repo` job is **chained behind** the `dns` approval, so the repo is
  created only after the DNS repoint is approved (`cloudflare-prod-write` + `github-prod` gates).
- **Gotcha:** in issue-form bodies, keep all prose **above** the `###` field headings — trailing
  text is slurped into the last field and can silently drop the maintainer login.
- **Optional (migrating an existing WordPress site):** **27. FFC-EX - Clone Deploy** mirrors the
  live site into the repo and opens a **draft PR** (never pushes to the default branch). See
  [ffc-ex-static-clone-runbook.md](ffc-ex-static-clone-runbook.md) and the
  [fidelity audit](ffc-ex-clone-fidelity-audit.md) before cutover.
- **Done when:** the repo exists, Pages serves the apex over HTTPS, and the maintainer has access.

## Phase 4 — WHMCS account + onboarding order

- **Run (dry-run first):** **34. WHMCS - Charity Onboard** — creates one WHMCS client, adds the
  primary + any secondary contacts (with routing/sub-account flags), and places the onboarding order
  (pre-501c3 vs 501c3 product). It **defaults to `dry_run=true`** (redacted preview, no writes) and
  is **idempotent** — re-running dedupes and reports `existing`/`skipped` rather than creating
  duplicates. See [whmcs-charity-onboarding.md](whmcs-charity-onboarding.md).
- **Catalog note:** if a needed product/status-marker doesn't exist yet, add it with **43. WHMCS -
  Product Add** (`config/whmcs-catalog-products.json`, dry-run default). See
  [whmcs-product-catalog.md](whmcs-product-catalog.md).
- **Done when:** the dry-run preview is correct, the live run reports the client id + order id, and
  the order shows in **41. WHMCS - Orders Triage**.

## Phase 5 — Ongoing support

- Charities (or admins) file template **08 (Support Request)** / **09 (Break/Fix)**, which open a
  WHMCS ticket via **36. WHMCS - Issue → Ticket** (one-way GitHub → WHMCS).
- Operators surface the backlog with **38. WHMCS - Tickets Triage** (and **41** for orders), then
  reply with **39. WHMCS - Ticket Respond** (templated, dry-run default; a live client-visible reply
  needs a real WHMCS admin username). See [whmcs-support-tickets.md](whmcs-support-tickets.md) and
  [whmcs-orders.md](whmcs-orders.md).
- **Eligibility:** support/hosting is for US nonprofits; international orgs are referred to TechSoup
  (see the support-tickets doc).

## (R) Reconcile / audit — anytime

- **04. Domain - Export Inventory (All Sources)** cross-checks Cloudflare, M365, WHMCS, and WPMUDEV
  for drift (orphaned zones, DNS without an active site, billing mismatches). See
  [domain-inventory-reconciliation.md](domain-inventory-reconciliation.md).
- **Donations:** reconcile WHMCS transactions against Zeffy using **33. WHMCS → Zeffy Import Draft**
  and the read-only Zeffy exports **44–46** (see [zeffy-api.md](zeffy-api.md)).

## Rollback / recovery notes

- **DNS:** every DNS-changing workflow previews first (`dry_run=true`); to revert, re-run **05.
  DNS - Manage Record** (or **03**) with the prior values.
- **Website repo:** **27** and **15** never force the default branch — repo content lands via PR /
  template, so revert by closing the PR or deleting the repo before cutover.
- **WHMCS:** onboarding is idempotent; a mistaken order is cancelled one at a time with **42.
  WHMCS - Order Update** (`action=cancel`, dry-run default).
- **Domain registration (#20)** and **collaborator add (#98)** are **not** reversible by a workflow
  — treat their typed-confirm / live-default behavior accordingly.
