# Cloudflare Domain Registration (DRAFT)

Automates **new domain registration** through the
[Cloudflare Registrar API](https://developers.cloudflare.com/registrar/registrar-api/) (public
**beta** as of 2026), so a domain can be purchased at wholesale cost directly from this repo's
automation instead of the Cloudflare dashboard.

> **Status: DRAFT / evaluation.** The Cloudflare Registrar API is in beta and currently supports
> **new registrations only**. **Renewals, transfers, and contact updates are NOT available via the
> API yet** (dashboard-only). Plan renewals manually until Cloudflare ships those endpoints.

## Why this exists

WHMCS has no Cloudflare _registrar_ module, and Cloudflare Registrar is an at-cost, single-account
offering (not a reseller program), so domains cannot be bought/renewed at Cloudflare from WHMCS
records. This automation sidesteps that by calling Cloudflare directly. Keep **WHMCS as the
billing/record-of-truth**: after registering here, create the matching WHMCS record (e.g.
`AddClient` / `AddOrder`) so inventory and billing stay accurate.

## Components

| File                                                         | Purpose                                                                              |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `scripts/cloudflare-domain-search.ps1`                       | Read-only: suggest available domains for a keyword/phrase.                           |
| `scripts/cloudflare-domain-register.ps1`                     | Check availability/pricing and (optionally) register a domain.                       |
| `.github/workflows/20-cloudflare-domain-register.yml`        | Workflow 12: search / check / register, plus a label-triggered check (safety-gated). |
| `scripts/cloudflare-registrar-access-check.ps1`              | Read-only probe: does the token have Registrar read/write?                           |
| `.github/workflows/21-cloudflare-registrar-access-check.yml` | Manual workflow to validate Registrar API rights.                                    |

## Safety model

Registration spends real money, so the script is gated:

- **Default = availability + pricing check only.** Nothing is purchased.
- `-Register` **without** `-Execute` = **dry run** (prints the request body).
- `-Register -Execute` = **live purchase** (charges the Cloudflare account).
- `-MaxRegistrationCost <n>` refuses to register if the quoted first-year cost exceeds the cap (`0`
  = no cap).
- The workflow additionally requires `confirm_domain` to exactly match `domain` for
  `mode=execute-register`.

## Prerequisites (before live registration works)

1. **A Cloudflare token carrying the `Registrar` permission group.** The OIDC loader action
   (`.github/actions/cloudflare-tokens-from-kv`) is **hardcoded** to fetch the `read-all-*` /
   `wr-all-*-cloudflare-api-token-zone-and-dns` Key Vault secrets into `CLOUDFLARE_API_TOKEN_FFC` /
   `_CM`. So simply adding a _new, separate_ Registrar-token secret to Key Vault would **not** be
   picked up by the workflow. To enable Registrar calls you must either: (a) **add the `Registrar`
   permission group to the existing token** behind the `*-cloudflare-api-token-zone-and-dns` secret
   (simplest — no workflow change), or (b) **extend the loader/workflow** to fetch a dedicated
   Registrar-token secret. The zone-and-dns scope alone is not sufficient; note that even the
   availability/`domain-check` call is documented to require Registrar **write** permission. Use
   `scripts/cloudflare-registrar-access-check.ps1` to confirm what the token actually has.
2. On the Cloudflare account: a **billing profile with a default payment method**, a **default
   registrant contact**, and **acceptance of the Domain Registration Agreement**.

To confirm whether the current token already has these rights, run **"13. Domain - Validate
Cloudflare Registrar API Access (Read-only) [CF]"** (or
`scripts/cloudflare-registrar-access-check.ps1 -Account FFC`). It reports `registrarRead` /
`registrarWrite` as `granted`, `denied`, or `inconclusive` without registering anything.
`canRegister` is `true` only when write is granted.

## Usage

### CLI

```powershell
# 0) Search for available names by keyword (read-only, never charges):
pwsh -File scripts/cloudflare-domain-search.ps1 -Query 'evergreen coffee' -Account FFC

# 1) Availability + pricing only (safe):
pwsh -File scripts/cloudflare-domain-register.ps1 -Domain example.org -Account FFC

# 2) Dry run of a registration (no charge):
pwsh -File scripts/cloudflare-domain-register.ps1 -Domain example.org -Account FFC -Register

# 3) Live registration, capped at $25, with auto-renew:
pwsh -File scripts/cloudflare-domain-register.ps1 -Domain example.org -Account FFC `
    -Register -Execute -AutoRenew -MaxRegistrationCost 25
```

Requires `CLOUDFLARE_API_TOKEN_FFC` or `CLOUDFLARE_API_TOKEN_CM` in the environment (same convention
as `cloudflare-zone-create.ps1`).

### Workflow

Run **"12. Domain - Registrar Search / Check / Register (Admin, DRAFT) [CF]"** from the Actions tab:

- `mode=search` — suggest available names for `search_query` (read-only)
- `mode=check` — availability/pricing only
- `mode=dry-run-register` — shows what would be purchased
- `mode=execute-register` — purchases (requires `confirm_domain` to match)

### Label-triggered availability check

Applying the **`domain-purchase-approved`** label to a
[domain purchase request issue](../.github/ISSUE_TEMPLATE/01-purchase-new-domain.yml) runs workflow
12 in **check-only** mode: it parses the domain from the issue, checks availability + price, and
comments the result back to the issue. **A label never purchases** — it cannot reach
`execute-register`. Buying remains a deliberate, separate `workflow_dispatch` with
`mode=execute-register` and a typed `confirm_domain`. This keeps a human approval gate (the label)
while removing the manual availability-lookup step.

## Output

The script emits a single JSON object on stdout, e.g.:

```json
{
  "domain": "example.org",
  "account": "FFC",
  "registrable": true,
  "currency": "USD",
  "registrationCost": "10.44",
  "renewalCost": "10.44",
  "action": "register",
  "registered": true,
  "dryRun": false,
  "state": "succeeded",
  "expiresAt": "2027-06-20T00:00:00Z"
}
```

## Suggested next steps

- Add a Registrar-scoped Cloudflare token to Key Vault to enable live runs.
- Chain registration into onboarding: register here, then create the WHMCS client/domain record
  (classic API) and run the existing _02. Domain - Add to FFC Cloudflare + WHMCS_ flow.
- Add a **Cloudflare-registrar expirations report** to inventory reconciliation so renewals are
  tracked while the renewal API remains unavailable.
