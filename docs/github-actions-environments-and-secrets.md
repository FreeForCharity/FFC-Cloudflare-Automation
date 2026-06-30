# GitHub Actions Environments and Secrets

This repo uses **GitHub Actions Environments** to:

- Store sensitive values as **Environment secrets** (recommended)
- Add optional **required reviewers** before a job can run

This is used for:

- `cloudflare-prod` (Cloudflare DNS workflows)
- `m365-prod` (Microsoft 365 / Graph / Exchange Online workflows)
- `wpmudev-prod` (WPMUDEV domain/site inventory workflows)

> **Current required-reviewer config (audited 2026-06-30 by workflow
> `99. Repo - Audit Environment Approval Gates [Repo]`).** Gated (require reviewer `clarkemoyer`, so
> jobs pause for approval): `cloudflare-prod`, `cloudflare-prod-write`, `whmcs-prod`, `github-prod`,
> `m365-prod`, `wpmudev-prod`. Not gated (runs proceed): `cloudflare-prod-read`, `zeffy-prod`. See
> [workflow-safety-and-approvals.md](workflow-safety-and-approvals.md) for the per-workflow table;
> re-run workflow 99 to refresh after any change in _Settings → Environments_.

## Where to configure Environments

1. In GitHub, open the repo.
2. Go to **Settings** → **Environments**.
3. Click **New environment**.
4. Name it exactly (case-sensitive):
   - `cloudflare-prod` or
   - `m365-prod` or
   - `wpmudev-prod`
5. (Recommended) Add **Required reviewers** to gate any workflow jobs that reference that
   environment.
6. Add **Environment secrets** for that environment.

## How secrets/vars resolve

- If a job specifies `environment: <name>`, GitHub makes available:
  - **Environment secrets** for `<name>`
  - **Repository secrets**
- If the same secret name exists in both places, the **Environment secret wins**.
- `${{ vars.NAME }}` reads a variable (non-secret) from the environment or repository.

## `cloudflare-prod`

Used by the numbered DNS workflows in `.github/workflows/`.

Environment secrets:

- `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` (recommended)
- `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` (recommended)

Recommended Cloudflare API token permissions:

- Zone: **Read**
- DNS: **Edit**
- DMARC Management: **Edit** (optional; currently this repo does not have a routable Cloudflare API
  surface to enable/inspect DMARC Management, so enabling is done manually in the dashboard: Email >
  DMARC Management)
- Account Rulesets: **Write** (required by workflow 10 — DNS - Create Redirect Rule)
- Zone WAF: **Write** (required by workflow 10)
- Dynamic URL Redirects: **Write** (required by workflow 10)

Without the Rulesets/WAF/Dynamic-URL-Redirects permissions, workflow 10's apply step fails with
Cloudflare error code `10000 "Authentication error"`. The dry-run / preview job still works because
GET on the entrypoint URL doesn't require write scope.

These are injected into workflow jobs as environment variables:

- `CLOUDFLARE_API_TOKEN_FFC`
- `CLOUDFLARE_API_TOKEN_CM`

Repository variable (non-secret):

- `ALLOWED_ACTORS` (optional guardrail; comma-separated GitHub usernames)

Environment variables (non-secret):

- `FFC_CUSTOM_NAMESERVER_1` (example: `ns1.freeforcharity.org`)
- `FFC_CUSTOM_NAMESERVER_2` (example: `ns2.freeforcharity.org`)

These are used by the domain add workflow to update WHMCS nameservers to the correct Cloudflare
custom account nameservers.

## `whmcs-prod`

Used by the WHMCS export workflows and any automation that updates WHMCS domain settings.

### WHMCS credential now comes from Azure Key Vault (single source of truth)

As of the AZ KV refactor, WHMCS workflows fetch the WHMCS API identifier + secret from Azure Key
Vault at runtime via OIDC, using the `./.github/actions/whmcs-secrets-from-kv` composite action —
the same pattern as `cloudflare-tokens-from-kv`. The action exports `WHMCS_API_IDENTIFIER`,
`WHMCS_API_SECRET`, and `WHMCS_APIM_SUBSCRIPTION_KEY` to downstream steps, masked. Workflows no
longer carry a copy of the WHMCS secret or hard-code the identifier inline.

Variables (required for OIDC → Key Vault — these are **identifiers, not passwords**). **Repository**
Variables are recommended (so `whmcs-prod` holds no Azure creds at all), but because the workflows
read them via the `vars.` context, environment-level Variables on `whmcs-prod` resolve too if you'd
rather scope them there:

- `WR_ALL_FFC_AZURE_KV_CLIENT_ID` (OIDC client id of the `ffc-admin-kv-writer` identity)
- `WR_ALL_FFC_AZURE_TENANT_ID` (Azure tenant id)

Create them with those exact names; workflows read them as
`${{ vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}` and `${{ vars.WR_ALL_FFC_AZURE_TENANT_ID }}` (the
`vars.` prefix is the expression context, not part of the variable name).

The `whmcs-prod` environment itself holds **no** secrets after this migration (the WHMCS credential
moved to Key Vault); it remains only to provide the deployment approval gate. The per-environment
**federated credential** on `ffc-admin-kv-writer` is what actually authorizes the OIDC exchange.

Key Vault secrets (in `kv-ffc-admin-prod-cbm`, scoped naming like the Cloudflare tokens — the action
defaults to `write` scope / `wr-all-*`):

- `wr-all-ffc-whmcs-api-identifier` / `read-all-ffc-whmcs-api-identifier` → the WHMCS API identifier
- `wr-all-ffc-whmcs-api-secret` / `read-all-ffc-whmcs-api-secret` → the WHMCS API secret
- `wr-all-ffc-apim-whmcs-subscription-key` / `read-all-ffc-apim-whmcs-subscription-key` → the APIM
  `whmcs-ops` subscription key (WHMCS calls route through APIM, which requires it)
- `wr-all-ffc-whmcs-api-url` / `read-all-ffc-whmcs-api-url` → the API endpoint (non-secret; the
  workflows pass it inline, the action does not read it)

WHMCS is a single credential, so the `read-all-*` and `wr-all-*` copies hold identical values.

WHMCS API calls do not hit `freeforcharity.org` directly — they route through Azure API Management
(`apim-ffc-gateway-prod`, static IP `20.231.116.111`) so WHMCS can allowlist one stable IP. See
[docs/whmcs-apim-routing.md](whmcs-apim-routing.md) for that pattern (gateway URL, subscription key,
and the required `CF-Connecting-IP` proxy-header setting in WHMCS).

The OIDC identity (`ffc-admin-kv-writer`) holds **Key Vault Secrets Officer** vault-wide and a
**federated credential** for `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:whmcs-prod`.
Each WHMCS job sets `permissions: id-token: write`. See
`.github/actions/whmcs-secrets-from-kv/README.md` for the full setup checklist (and the two
remaining steps: setting the real secret value in KV and creating the two repository Variables
above).

To rotate the WHMCS credential, add a new version of the `*-ffc-whmcs-api-secret` KV secret — no
GitHub secret update needed.

#### Legacy (pre-refactor)

Before the refactor the secret was held as a GH Environment Secret named
`ZBBEPFQ5W7RCSIME0NOQOYRQIDGTKBPU` (for identifier `zbBEpfq5W7RCSImE0NOqoYrqIDGTkBPu`), with an
optional `WHMCS_API_ACCESS_KEY`. Both are safe to remove from the `whmcs-prod` environment: nothing
reads them — the WHMCS API access key is not used (the action no longer fetches or exports it), and
the identifier/secret now come from Key Vault.

## `m365-prod`

Used by:

- `.github/workflows/5-m365-domain-and-dkim.yml`
- `.github/workflows/6-m365-list-domains.yml`

### Identifiers vs secrets

For Azure OIDC login, the values we store are **identifiers** (not passwords):

- `FFC_AZURE_CLIENT_ID` (App/Service Principal client id)
- `FFC_AZURE_TENANT_ID` (tenant id)
- `AZURE_SUBSCRIPTION_ID` (subscription id; only needed when you actually target Azure resources)

These are stored as **Environment secrets** in this repo so the names can remain stable.

Environment secrets (required for the M365 workflows in this repo):

- `FFC_AZURE_CLIENT_ID`
- `FFC_AZURE_TENANT_ID`

### Split-environment preflight

The preflight workflow `.github/workflows/7-m365-domain-preflight.yml` is intentionally split into
two jobs so secrets do not have to be duplicated across environments:

- **Graph job** runs in `m365-prod` and requires:
  - `FFC_AZURE_CLIENT_ID`
  - `FFC_AZURE_TENANT_ID`
- **Cloudflare job** runs in `cloudflare-prod` and requires:
  - `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` and/or `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`

Environment secrets (optional):

- `AZURE_SUBSCRIPTION_ID` (only needed if you later add steps that require an Azure subscription
  context)

Environment secrets (optional, only needed for DKIM app-only mode):

- `EXO_TENANT` (example: `freeforcharity.onmicrosoft.com`)
- `EXO_ORGANIZATION` (often the same as `EXO_TENANT`)
- `EXO_CERT_PFX_BASE64` (base64-encoded PFX for certificate auth)
- `EXO_CERT_PFX_PASSWORD` (optional; empty if the PFX has no password)

## `m365-prod`: OIDC and app prerequisites (Azure/Entra)

The M365 workflows authenticate with GitHub OIDC via `azure/login@v3`, then use Azure CLI to fetch a
Microsoft Graph token.

This is what makes the workflow **headless**:

- No interactive browser/device-code prompts
- GitHub issues an OIDC token, and Entra validates it via the federated credential
- Azure CLI then obtains a Microsoft Graph access token for the app

You must configure the Entra application referenced by `FFC_AZURE_CLIENT_ID`:

- **Federated credential** (for GitHub OIDC)
  - Issuer: `https://token.actions.githubusercontent.com`
  - Audience: `api://AzureADTokenExchange`
  - Subject (recommended pattern when workflows use `environment: m365-prod`):
    - `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:m365-prod`
  - Note: if you scope the federated credential differently (branch/ref), the subject format
    changes.

- **Azure subscription access**
  - Not required for the current M365 workflows because they set `allow-no-subscriptions: true`.
  - Only required if you add steps that manage Azure resources.

- **Microsoft Graph permissions**
  - For the domain listing/status workflows, grant (and admin-consent) one of:
    - Application permission `Domain.Read.All` (recommended), or
    - Application permission `Directory.Read.All`

- **Exchange Online (DKIM) permissions (only if using app-only DKIM)**
  - Certificate-based auth is required.
  - The app must be permitted for Exchange Online app-only access and have the required EXO
    permissions.

## Troubleshooting quick hits

- `azure/login` fails:
  - Confirm the job has `permissions: id-token: write`.
  - Confirm `FFC_AZURE_CLIENT_ID` / `FFC_AZURE_TENANT_ID` are set on the `m365-prod` environment.
  - Confirm the Entra app federated credential subject matches the repo/environment.

- Graph calls return `403 Forbidden`:
  - Confirm the Entra app has Graph **application** permissions and **admin consent**.

- DKIM steps fail:
  - Confirm the PFX secrets exist and import works.
  - Confirm the Entra app is allowed for EXO app-only and has the right EXO permissions.

## `wpmudev-prod`

Used by the WPMUDEV domain/site inventory workflow in
`.github/workflows/13-wpmudev-export-sites.yml`.

### Environment secrets

- `FFC_WPMUDEV_GA_API_Token` (required)

This is the WPMUDEV Hub API token used to authenticate read-only API requests.

### How to obtain the WPMUDEV API token

1. Log in to [WPMUDEV](https://wpmudev.com/)
2. Navigate to **Hub** → **API**
3. Generate a new API key or use an existing one
4. The token must have at least **read access** to sites/domains

### Required permissions

The API token needs read access to:

- `GET /api/hub/v1/account` (diagnostics check)
- `GET /api/hub/v1/sites` (site listing, paginated)

### Security notes

- The workflow is **read-only**; no changes are made to WPMUDEV
- The token is **never logged** in workflow output (only success/failure status)
- The token is passed via environment variable `WPMUDEV_API_TOKEN` to the PowerShell script

### Workflow behavior

The workflow:

1. Performs a diagnostics check to validate the API token
2. Fetches all sites from WPMUDEV Hub API (paginated)
3. Aggregates sites by domain (one row per domain)
4. Exports to CSV: `wpmudev_domains.csv` (default)
5. Uploads artifact: `wpmudev-domain-inventory`

For full documentation, see [docs/wpmudev-domain-inventory.md](wpmudev-domain-inventory.md).

### Troubleshooting

- **401 Unauthorized**: Verify `FFC_WPMUDEV_GA_API_Token` is set and valid
- **403 Forbidden**: Verify the token has read access to Hub API
- **Incomplete results**: Check pagination logic in `scripts/wpmudev-sites-export.ps1`
