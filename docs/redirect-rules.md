# Cloudflare Redirect Rules (workflow 111)

This document describes the **`111. DNS - Create Redirect Rule (Admin) [CF]`** workflow and the
`scripts/Set-CloudflareRedirectRule.ps1` script that backs it. Use this workflow to point a source
domain (apex + optional `www`) at a target domain via a Cloudflare Single Redirect rule.

## When to use it

- Collapsing legacy/duplicate FFC domains onto a canonical one (e.g. `ffcsites.org` →
  `ffcadmin.org`).
- Sunsetting a charity's previous URL after they choose a new name.
- Pointing a marketing alias at a primary site without standing up a separate origin.

The source zone must already exist in the Free For Charity Cloudflare account with proxied A/AAAA
records (run `101. Domain - Status` first if unsure).

## What the workflow does

The workflow has two jobs:

1. **`preview`** (always runs, environment `cloudflare-prod-read`) — fetches the current state of
   the source zone's `http_request_dynamic_redirect` phase ruleset and prints the planned rule. When
   `dry_run=true` this is the only job that runs, and no Cloudflare writes happen.

2. **`apply`** (runs only when `dry_run=false`, environment `cloudflare-prod-write`) — creates or
   updates the redirect rule, then smoke-tests the source domain.

### Rule structure

For a request to `source_domain=ffcsites.org`, `target_domain=ffcadmin.org`, `include_www=true`, the
script generates:

| Field                     | Value                                                                |
| ------------------------- | -------------------------------------------------------------------- |
| **Match expression**      | `(http.host eq "ffcsites.org") or (http.host eq "www.ffcsites.org")` |
| **Action**                | `redirect`                                                           |
| **Target URL expression** | `concat("https://ffcadmin.org", http.request.uri.path)`              |
| **Status code**           | `301` (configurable: 301, 302, 307, 308)                             |
| **Preserve query string** | `true` (configurable)                                                |
| **Description**           | `Repoint ffcsites.org to ffcadmin.org` (used as the idempotency key) |

## Dispatching the workflow

### From the CLI

```bash
gh workflow run "111. DNS - Create Redirect Rule (Admin) [CF]" \
  --repo FreeForCharity/FFC-Cloudflare-Automation \
  -f source_domain=ffcsites.org \
  -f target_domain=ffcadmin.org \
  -f dry_run=true
```

Inputs:

| Input                   | Required | Default | Notes                                                  |
| ----------------------- | -------- | ------- | ------------------------------------------------------ |
| `source_domain`         | yes      | —       | Zone that will redirect. Must exist in FFC Cloudflare. |
| `target_domain`         | yes      | —       | Destination domain (always HTTPS).                     |
| `status_code`           | no       | `301`   | One of 301 / 302 / 307 / 308.                          |
| `include_www`           | no       | `true`  | Match `www.<source>` in addition to apex.              |
| `preserve_query_string` | no       | `true`  | Forward query string through to target.                |
| `dry_run`               | no       | `true`  | When true, only the preview job runs.                  |

### From the GitHub UI

Actions → **111. DNS - Create Redirect Rule (Admin) [CF]** → **Run workflow** → fill in
`source_domain` and `target_domain`. Leave `dry_run=true` for the first pass, review the preview
job's logs, then re-run with `dry_run=false`.

## Verification

After a successful apply the workflow's smoke step probes the source domain with `curl` and checks
for a `Location:` header pointing at the target. You can also verify manually:

```bash
curl -sI https://<source_domain>/foo?a=1
# expect:
#   HTTP/1.1 301 Moved Permanently
#   Location: https://<target_domain>/foo?a=1
```

## Idempotency

Re-running the workflow with the same `source_domain` + `target_domain` updates the rule in place
rather than appending duplicates. The script matches on the rule's `description` field. If you
change the description (or pass `-Description "..."` to the script directly), a new rule is added
alongside the old one.

## Token scope requirements

The `WR_ALL_FFC` token (loaded from Azure Key Vault by the
`.github/actions/cloudflare-tokens-from-kv` composite action) must include:

- **Account Rulesets: Write**
- **Zone WAF: Write**
- **Dynamic URL Redirects: Write**

scoped to **All zones in Free For Charity**. The standard DNS-write scope is **not** sufficient —
Cloudflare's Rulesets API is gated behind the WAF/Rulesets permissions even for plain redirect
rules.

If the token is missing these you'll see error code `10000 "Authentication error"` on the apply
step. The dry-run / preview step still works because GET on the entrypoint URL doesn't require write
permissions.

## Gotchas

### POST vs PUT for the first rule on a zone

Cloudflare doesn't allow `PUT` against a phase entrypoint that doesn't exist yet. The script handles
this by branching:

- **No existing entrypoint** (GET returns 404) → `POST /zones/{id}/rulesets` with
  `kind: "zone", phase: "http_request_dynamic_redirect", rules: [...]`.
- **Entrypoint exists** → `PUT /zones/{id}/rulesets/phases/http_request_dynamic_redirect/entrypoint`
  with the updated `rules` array.

You may see the misleading error `request is not authorized` if a PUT hits a non-existent entrypoint
— it actually means the resource doesn't exist for that verb.

### Source zone must be proxied

If the source zone's A/AAAA records aren't proxied (orange cloud), Cloudflare never sees the request
and can't apply the rule. Use `101. Domain - Status` or `106. DNS - Enforce Standard` first to
ensure the apex/www records are proxied.

### Running the script locally

Useful for testing or one-off redirects outside the workflow:

```powershell
# Dry-run from a developer machine
$env:CLOUDFLARE_API_TOKEN = "<your write token>"
pwsh scripts/Set-CloudflareRedirectRule.ps1 `
  -SourceDomain ffcsites.org `
  -TargetDomain ffcadmin.org `
  -DryRun

# Optional switches
#   -ApexOnly              skip the www.<source> match
#   -NoPreserveQueryString drop query string at the target
#   -StatusCode 302        use a temporary redirect
#   -Description "..."     override the idempotency-key description
```

The script reuses the same dual-token resolver as `Update-CloudflareDns.ps1`, so it'll also
auto-detect tokens in `CLOUDFLARE_API_TOKEN_FFC` / `CLOUDFLARE_API_TOKEN_CM` env vars.

## Related workflows

- **101. Domain - Status (All Sources)** — check the source zone is in Cloudflare and proxied.
- **105. DNS - Manage Record** — if you need to create/update the DNS records first.
- **106. DNS - Enforce Standard** — bulk apply the FFC standard (proxied apex + www) before setting
  up the redirect.
