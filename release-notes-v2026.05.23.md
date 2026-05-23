# v2026.05.23

## Version description

This release delivers a new redirect-rules workflow, tightens the Cloudflare write
environment to a least-privilege split, adds a one-shot CF-only DNS cutover path, and lands a
broader governance bundle (CODEOWNERS, drift audit, phantom-revert guard).

### Headline changes

- **New workflow: `10. DNS - Create Redirect Rule (Admin) [CF]`** — declaratively set up a
  Cloudflare Single Redirect rule from a source zone (apex + optional `www`) to a target
  domain. Idempotent, dry-run-by-default, supports 301/302/307/308 with query-string
  forwarding. First production use: `ffcsites.org → ffcadmin.org`. See
  [`docs/redirect-rules.md`](docs/redirect-rules.md).
- **`cloudflare-prod` environment split** into `cloudflare-prod-read` and
  `cloudflare-prod-write` for least-privilege approval gating. Read-only operations
  (status, audit, export, dry-runs) no longer trigger write-environment approval prompts.
- **`01. Domain - Status` gains a `skip_m365` input** for CF-only domains, paired with
  `github_pages_only=true` for one-shot DNS cutover that also removes stale non-HostPapa
  origin IPs.
- **Governance hardening**: phantom-revert guard workflow on every PR, repo rulesets +
  settings drift-audit workflow (`95`), and `CODEOWNERS` for sensitive paths.
- **F1 refactor**: workflows `14-domain-add-ffc-cloudflare-and-whmcs` and
  `15-website-provision` now load Cloudflare tokens at runtime from Azure Key Vault via
  the `cloudflare-tokens-from-kv` composite action instead of consuming Environment Secret
  copies.

### First production redirect

`ffcsites.org → ffcadmin.org` (apex + `www`, 301, path + query string preserved). Verified
via run `26324483759` and live `curl -sI` probes.

## Contributors

- @clarkemoyer
- GitHub Copilot

## Included changes

### Feature

- **`10. DNS - Create Redirect Rule (Admin) [CF]`** (#394) — `scripts/Set-CloudflareRedirectRule.ps1`
  + `.github/workflows/16-dns-create-redirect-rule.yml`. Idempotent (matches existing rule by
  description), dry-run-by-default, two-job split (`preview` on `cloudflare-prod-read`,
  `apply` on `cloudflare-prod-write`).
- **`skip_m365` input on workflow 01** (#389) — pairs with `github_pages_only=true` for
  one-shot CF-only DNS cutover that also deletes non-HostPapa origin IPs.
- **Phantom-revert guard** (#390) — workflow runs on every PR, blocks merges when a PR
  branch is sufficiently behind `main` that it might silently revert recent changes to
  critical paths.

### Refactor

- **F1 split: `cloudflare-prod` → `cloudflare-prod-read` + `cloudflare-prod-write`** (#388).
  Read-only flows (status, audit, export, dry-runs) now run with read-scoped tokens and no
  write-env approval gate.
- **F1: workflow 14 (domain-add) uses KV via composite action** (#384) — 3 CF jobs
  migrated off environment-secret token copies.
- **F1: workflow 15 (website-provision) uses KV via composite action** (#385) — 2 CF
  jobs migrated.

### Fix

- **redirect-rule POST vs PUT** (#396) — Cloudflare rejects PUT on a phase entrypoint that
  doesn't exist. Script now POSTs to `/zones/{id}/rulesets` when no entrypoint exists, PUTs
  to update an existing one. Dry-run output labels the verb.
- **redirect-rule bool args** (#395) — workflow now passes inputs through `env:` and builds
  the pwsh argument array in-script so booleans don't cross a shell stringification
  boundary. `[bool]` params replaced with idiomatic `[switch]` inversions (`-ApexOnly`,
  `-NoPreserveQueryString`).
- **redirect-rule smoke test** (#397) — replaces `Invoke-WebRequest -MaximumRedirection 0`
  (which throws on 3xx in PS7) with `curl.exe -w` to capture status + Location reliably.
- **Drift-audit workflow numbering** — renamed from `20.` to `95.` to avoid collision with
  the existing `20. M365 - Domain Preflight` and bring it into the `89–95 Repo` block.

### Documentation

- New: [`docs/redirect-rules.md`](docs/redirect-rules.md) — usage, idempotency model, token
  scope requirements, POST-vs-PUT explanation, gotchas.
- Updated: [`docs/github-actions-environments-and-secrets.md`](docs/github-actions-environments-and-secrets.md)
  — additional token permissions needed by workflow 10 (Account Rulesets: Write, Zone WAF:
  Write, Dynamic URL Redirects: Write).
- Updated: `README.md` — workflow 10 listed under "Additional operational workflows".
- Updated: `.github/workflows/README.md` — workflow 10 entry under the 05–09 DNS section.

### Operational notes

- **Token scope upgrade required for redirect rules.** The `WR_ALL_FFC` /
  `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` token must include **Account Rulesets: Write**,
  **Zone WAF: Write**, and **Dynamic URL Redirects: Write** for the apply job. The
  dry-run / preview job still works with the previous scope. See
  [`docs/github-actions-environments-and-secrets.md`](docs/github-actions-environments-and-secrets.md).
- **Workflow 10 first run uses POST, subsequent runs use PUT** — when a zone has no
  existing redirect entrypoint, the first apply creates one; later runs against the same
  zone update in place.

### Verified runs

- Redirect apply (POST): https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/26324483759
- Dry-run preview (the same plan, no writes): https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/26324102302
