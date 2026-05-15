# Cloudflare Registrar API — Assessment and Operational Guide

## Overview

Cloudflare launched its **Registrar API** in April 2026 (public beta). For the first time, FFC can
programmatically search, check, and purchase domain names directly through the Cloudflare API —
entirely within existing GitHub repository tooling — without touching the Cloudflare dashboard or
using a separate agent platform.

This document covers:

1. [What the new API does](#what-the-new-api-does)
2. [How it fits the FFC value stream](#how-it-fits-the-ffc-value-stream)
3. [Stripe Projects integration note](#stripe-projects-integration-note)
4. [Architecture and workflow design](#architecture-and-workflow-design)
5. [New workflow: 10. Domain - Purchase via Cloudflare Registrar API](#new-workflow-10-domain---purchase-via-cloudflare-registrar-api)
6. [Setup requirements](#setup-requirements)
7. [Supported TLDs and pricing](#supported-tlds-and-pricing)
8. [Limitations and risk controls](#limitations-and-risk-controls)
9. [End-to-end onboarding with the new API](#end-to-end-onboarding-with-the-new-api)
10. [Future enhancements](#future-enhancements)

---

## What the new API does

The Cloudflare Registrar API exposes three core operations:

| Operation    | Endpoint                                           | Description                                             |
| ------------ | -------------------------------------------------- | ------------------------------------------------------- |
| **Search**   | `GET /accounts/{id}/registrar/domain-search?q=...` | Suggest domains from a keyword (cached, fast).          |
| **Check**    | `POST /accounts/{id}/registrar/domain-check`       | Real-time availability + current price (up to 20).      |
| **Register** | `POST /accounts/{id}/registrar/registrations`      | Purchase the domain, charge the default payment method. |

Key behaviors:

- Authentication is a **bearer API token** with `Registrar:Write` permission.
- The account must have a **default billing payment method** and a **default registrant contact**
  configured at `https://dash.cloudflare.com/<ACCOUNT_ID>/domains/registrations`.
- Inline per-registration contacts can override the default.
- `auto_renew` defaults to `false`; WHOIS privacy defaults to `redaction` when supported.
- Registrations are **non-refundable** once complete — always use dry-run mode first.
- The API is accessible via the standard Cloudflare MCP tool, enabling agent-driven workflows.

---

## How it fits the FFC value stream

Before this API, the domain purchase step was a **manual bottleneck**:

```
Charity submits ticket
    → Admin reviews and approves
    → Admin purchases domain manually (dashboard / external registrar)  ← BOTTLENECK
    → Admin adds zone to Cloudflare (Workflow 02 / 09)
    → DNS standard applied (Workflow 03 / 06)
    → M365 tenant domain added (Workflow 24)
    → DKIM enabled (Workflow 23)
    → Website provisioned (Workflow 15)
```

With the Registrar API, the purchase step can be automated:

```
Charity submits ticket (Issue Template 01)
    → Admin reviews and applies label: domain-purchase-approved
    → ★ Workflow 10 triggers automatically: availability check + purchase + Cloudflare zone creation
    → DNS standard applied (Workflow 02/03)
    → M365 + DKIM (Workflows 24/23)
    → Website provisioned (Workflow 15)
```

The label-gated trigger keeps human approval in the loop while eliminating manual dashboard work.

---

## Stripe Projects integration note

On April 30, 2026, Cloudflare and Stripe jointly announced **Stripe Projects** — a protocol that
lets AI agents (using the Stripe CLI + Projects plugin) autonomously create Cloudflare accounts,
purchase domain names, and deploy applications without human dashboard interaction. The protocol
uses Stripe as the identity/payment provider and enforces a default `$100/month` spending cap.

**Relevance for FFC:**

| Approach             | Stripe Projects | Cloudflare Registrar API (direct) |
| -------------------- | --------------- | --------------------------------- |
| Needs Stripe account | Yes             | No (uses existing CF billing)     |
| Requires new agent   | Yes (CLI-based) | No (GitHub Actions)               |
| Visibility           | Low (external)  | High (GitHub repo, issues, PRs)   |
| Human approval gate  | Initial only    | Per-label (customizable)          |
| FFC fit              | Lower           | **Higher** — stays in this repo   |

**Recommendation:** Use the **direct Cloudflare Registrar API** within GitHub Actions. This provides
equivalent automation without requiring a Stripe account, a new agent platform, or migration away
from the existing repository-based workflow. Stripe Projects is worth revisiting if FFC ever needs
fully autonomous account provisioning for new Cloudflare accounts, but that is explicitly out of
scope per the issue.

---

## Architecture and workflow design

### Design principles followed

1. **Human approval gate**: the purchase workflow only fires when an administrator explicitly
   applies the `domain-purchase-approved` label to an issue. No automation runs without a human
   decision.
2. **Dry-run first**: the `dry_run` input defaults to `true` so every first run is non-destructive.
   Administrators preview availability and price before purchasing.
3. **Least-privilege token**: the Registrar API token (`CLOUDFLARE_REGISTRAR_API_TOKEN`) requires
   only `Registrar:Write` permission — separate from the DNS tokens already in use.
4. **Separate environment**: the `cloudflare-registrar` GitHub environment gates the secret behind
   an optional protection rule (recommended: require manual approval before purchase).
5. **Audit trail**: every purchase is commented back to the originating issue and uploaded as a
   workflow artifact.
6. **Idempotency**: if a domain is already registered (check returns `registrable: false` with
   `reason: domain_unavailable`), the workflow exits cleanly without error.

### Component inventory

| Component                                             | Purpose                                                         |
| ----------------------------------------------------- | --------------------------------------------------------------- |
| `scripts/cloudflare-domain-register.ps1`              | PowerShell wrapper: search, check, register via Registrar API.  |
| `.github/workflows/10-cloudflare-domain-purchase.yml` | GitHub Actions workflow: issue-label trigger + manual dispatch. |
| `.github/ISSUE_TEMPLATE/01-purchase-new-domain.yml`   | Issue template (updated admin checklist).                       |
| `docs/cloudflare-domain-registration-api.md`          | This document: assessment + operational guide.                  |

---

## New workflow: 10. Domain - Purchase via Cloudflare Registrar API

**File:** `.github/workflows/10-cloudflare-domain-purchase.yml`

### Triggers

| Trigger             | Gate                                     | Description                                  |
| ------------------- | ---------------------------------------- | -------------------------------------------- |
| `issues: labeled`   | Label must be `domain-purchase-approved` | Fires when admin applies the approval label. |
| `workflow_dispatch` | N/A                                      | Manual run with explicit inputs.             |

### Jobs

1. **resolve** — parses the domain from the issue body or manual inputs; skips non-matching labels.
2. **check_and_purchase** — runs in `cloudflare-registrar` environment; validates secrets, calls
   `cloudflare-domain-register.ps1`, uploads result JSON as artifact.
3. **post_back** — comments results (availability, price, registration status) back to the issue.

### Inputs (workflow_dispatch)

| Input                    | Required | Default | Description                                               |
| ------------------------ | -------- | ------- | --------------------------------------------------------- |
| `domain`                 | Yes      | —       | Domain to purchase (e.g., `example.org`).                 |
| `registrant_name`        | No       | —       | Override registrant name (uses account default if blank). |
| `registrant_org`         | No       | —       | Override registrant organization.                         |
| `registrant_email`       | No       | —       | Override registrant email.                                |
| `registrant_phone`       | No       | —       | Override phone (format: `+1.5205551234`).                 |
| `registrant_address`     | No       | —       | Override street address.                                  |
| `registrant_city`        | No       | —       | Override city.                                            |
| `registrant_state`       | No       | —       | Override state/province.                                  |
| `registrant_postal_code` | No       | —       | Override postal/ZIP code.                                 |
| `registrant_country`     | No       | `US`    | Override country code.                                    |
| `dry_run`                | No       | `true`  | Check availability only; do not purchase.                 |
| `issue_number`           | No       | —       | Post results back to this issue number.                   |

---

## Setup requirements

### 1. Create the Cloudflare Registrar API token

1. Go to `https://dash.cloudflare.com/<ACCOUNT_ID>/api-tokens`.
2. Create a new custom token with **Registrar: Edit** permission scoped to the FFC account.
3. Copy the token.

### 2. Configure the Cloudflare account default contact and billing

1. Go to `https://dash.cloudflare.com/<ACCOUNT_ID>/domains/registrations`.
2. Accept the Domain Registration Agreement.
3. Set a **default registrant contact** (name, org, email, phone, address).
4. Go to `https://dash.cloudflare.com/<ACCOUNT_ID>/billing/payment-info`.
5. Confirm a default payment method is set.

### 3. Create the `cloudflare-registrar` GitHub environment

1. In the repository, go to **Settings → Environments → New environment**.
2. Name it `cloudflare-registrar`.
3. (**Recommended**) Add a protection rule: **Required reviewers** — add FFC administrators. This
   means the purchase job requires a second human approval click before it runs.
4. Add the following secrets and variables:

| Type     | Name                             | Value                                          |
| -------- | -------------------------------- | ---------------------------------------------- |
| Secret   | `CLOUDFLARE_REGISTRAR_API_TOKEN` | The API token from step 1.                     |
| Variable | `CLOUDFLARE_ACCOUNT_ID`          | The Cloudflare account ID (e.g., `abc123...`). |

### 4. Add the `domain-purchase-approved` label

Run workflow **93. Repo - Initialize Labels** or add the label manually:

- **Name**: `domain-purchase-approved`
- **Color**: `#0075ca` (blue)
- **Description**: `Admin has approved this domain purchase request`

The label is already defined in `.github/labels.yml` (add it if not present).

---

## Supported TLDs and pricing

The Cloudflare Registrar API beta supports a subset of TLDs. Common examples:

| TLD    | Approx. price/year |
| ------ | ------------------ |
| `.com` | ~$8.57 USD         |
| `.org` | ~$9.18 USD         |
| `.net` | ~$9.18 USD         |
| `.dev` | ~$10.11 USD        |
| `.app` | ~$11.00 USD        |
| `.io`  | ~$32.00 USD        |

Not all TLDs are supported. If a TLD is unsupported, the check endpoint returns
`reason: extension_not_supported_via_api`. In that case, fall back to a manual purchase via the
Cloudflare dashboard or an external registrar, then continue with the existing zone-creation
workflows.

---

## Limitations and risk controls

| Limitation                                | Mitigation                                                    |
| ----------------------------------------- | ------------------------------------------------------------- |
| Registrations are non-refundable          | Always dry-run first; environment protection rules add a gate |
| Not all TLDs supported                    | Script exits cleanly with `registrable: false` + reason       |
| Default contact must be pre-configured    | Documented in setup; inline contact override available        |
| Single account only (FFC account)         | Separate token per account if CM account is needed            |
| Billing is charged immediately on success | Dry-run default; human-approval environment gate              |
| Stripe Projects protocol not used         | Direct API is simpler and stays in this repo                  |

---

## End-to-end onboarding with the new API

After a charity submits a domain purchase request (Issue Template 01):

### Step 1 — Admin reviews and approves

1. Open the GitHub issue.
2. Confirm domain availability (optional: manually run Workflow 10 with `dry_run=true`).
3. Apply label `domain-purchase-approved`.
4. If the `cloudflare-registrar` environment has a required-reviewer rule, approve the pending
   workflow run in the Actions UI.

### Step 2 — Workflow 10 runs automatically

- Parses the domain from the issue.
- Calls the Cloudflare Registrar API to check availability and price.
- If available and not a dry run: purchases the domain.
- Comments results back to the issue.

### Step 3 — Continue with existing workflows

After the domain is purchased, continue the standard onboarding:

| Step | Workflow                                       | Action                                   |
| ---- | ---------------------------------------------- | ---------------------------------------- |
| 3a   | **02. Domain - Add to FFC Cloudflare + WHMCS** | Create zone + update WHMCS nameservers   |
| 3b   | **03. Domain - Enforce Standard**              | Apply DNS standard (GitHub Pages + M365) |
| 3c   | **24. M365 - Add Tenant Domain**               | Add domain to M365 tenant                |
| 3d   | **23. M365 - Enable DKIM**                     | Enable DKIM signing                      |
| 3e   | **15. Website - Provision**                    | Create GitHub repo + enable Pages        |

> **Note:** The domain purchase workflow does not currently chain these steps automatically. A
> future enhancement could create a composite "full onboarding" workflow that calls them in sequence
> after a successful purchase.

---

## Future enhancements

1. **Chained full-onboarding workflow**: after a successful purchase, automatically trigger Workflow
   02 (zone create) → 03 (enforce standard) → 24 (M365) → 23 (DKIM) → 15 (website).
2. **Auto-renew opt-in**: expose `auto_renew` as a workflow input (currently defaults to `false`).
3. **Domain inventory integration**: after purchase, automatically append the domain to the WHMCS
   inventory or an internal CSV so Workflow 04 (Export Inventory) stays accurate.
4. **Multi-account support**: if CM account also needs to purchase domains, add a second token and
   account ID environment variable pattern matching the existing FFC/CM pattern in other workflows.
5. **Batch purchases**: extend the script to accept a list of domains and register them in a single
   workflow run (useful for onboarding batches).
