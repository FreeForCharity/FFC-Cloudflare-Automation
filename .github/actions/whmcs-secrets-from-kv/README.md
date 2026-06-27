# Composite action: `whmcs-secrets-from-kv`

Fetch the FFC WHMCS API credential (identifier + secret, and optionally an access key) from Azure
Key Vault at workflow runtime, via OIDC. Replaces the older pattern of holding the WHMCS secret as a
GitHub Environment Secret in `whmcs-prod`.

## Why this action exists

Before this action, every workflow that called the WHMCS API consumed the secret from a GH
Environment Secret and hard-coded the identifier inline:

```yaml
env:
  WHMCS_API_IDENTIFIER: 'zbBEpfq5W7RCSImE0NOqoYrqIDGTkBPu'
  WHMCS_API_SECRET: ${{ secrets.ZBBEPFQ5W7RCSIME0NOQOYRQIDGTKBPU }}
  WHMCS_API_ACCESS_KEY: ${{ secrets.WHMCS_API_ACCESS_KEY }}
```

That kept a copy of the WHMCS secret in GitHub (the `whmcs-prod` environment) in addition to
wherever it is mastered. Rotating the credential meant updating that copy by hand — the exact drift
that bit the Cloudflare tokens (a CM token rotation in KV did not reach this repo's env secret for 4
months; see `../cloudflare-tokens-from-kv/README.md`).

This action makes Key Vault the single source of truth for the WHMCS credential, matching the
`cloudflare-tokens-from-kv` pattern (same scoped secret naming, same OIDC identities). Workflows
authenticate to Azure via OIDC (no Azure password stored in GitHub), pull the current values from KV
at runtime, and expose them to downstream steps using the same env-var names the WHMCS PowerShell
scripts already read (`scripts/whmcs-api-common.ps1` → `Resolve-WhmcsCredentials`).

## Key Vault secrets

The credential lives in `kv-ffc-admin-prod-cbm` under the repo's scoped naming convention. WHMCS is
a single API credential, so the `read-all-*` and `wr-all-*` copies hold identical values — `scope`
only selects which copy/identity is used:

| Secret name                         | Scope | Holds                |
| ----------------------------------- | ----- | -------------------- |
| `wr-all-ffc-whmcs-api-identifier`   | write | WHMCS API identifier |
| `wr-all-ffc-whmcs-api-secret`       | write | WHMCS API secret     |
| `read-all-ffc-whmcs-api-identifier` | read  | WHMCS API identifier |
| `read-all-ffc-whmcs-api-secret`     | read  | WHMCS API secret     |

(There are also `*-ffc-whmcs-api-url` secrets in the vault; this action does not use them — the API
URL is non-secret and passed inline by the workflows.)

## Inputs

| Name                     | Required | Default                 | Description                                                                                                  |
| ------------------------ | -------- | ----------------------- | ------------------------------------------------------------------------------------------------------------ |
| `scope`                  | no       | `write`                 | `write` loads `wr-all-*` (via `ffc-admin-kv-writer`); `read` loads `read-all-*` (via `ffc-admin-kv-reader`). |
| `vault-name`             | no       | `kv-ffc-admin-prod-cbm` | Azure Key Vault name                                                                                         |
| `azure-client-id`        | yes      | —                       | OIDC client ID of the identity with access to the vault. Pass `${{ vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}`.   |
| `azure-tenant-id`        | yes      | —                       | Azure tenant ID. Pass `${{ vars.WR_ALL_FFC_AZURE_TENANT_ID }}`.                                              |
| `access-key-secret-name` | no       | `''` (skip)             | KV secret name for an optional WHMCS access key. The WHMCS API does not currently use one, so leave empty.   |

## Outputs

This action declares no formal outputs. It exports environment variables visible to all subsequent
steps in the same job, written in GitHub's heredoc-delimited `$GITHUB_ENV` format (so a value can
never inject extra variables):

- `WHMCS_API_IDENTIFIER` — WHMCS API identifier
- `WHMCS_API_SECRET` — WHMCS API secret
- `WHMCS_API_ACCESS_KEY` — only when `access-key-secret-name` is set

All exported values are masked via `::add-mask::` before export, so they will not appear in logs.

## Usage

```yaml
permissions:
  id-token: write # required for OIDC
  contents: read

jobs:
  whmcs:
    runs-on: windows-latest
    environment: whmcs-prod
    steps:
      - uses: actions/checkout@v5

      - uses: ./.github/actions/whmcs-secrets-from-kv
        with:
          azure-client-id: ${{ vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}
          azure-tenant-id: ${{ vars.WR_ALL_FFC_AZURE_TENANT_ID }}

      - name: Call WHMCS
        shell: pwsh
        env:
          WHMCS_API_URL: 'https://freeforcharity.org/hub/includes/api.php'
        run: |
          # WHMCS_API_IDENTIFIER / WHMCS_API_SECRET are now available as env vars.
          pwsh -NoProfile -File .\scripts\whmcs-products-export.ps1
```

All WHMCS workflows in this repo use the default `write` scope (one identity, one federated
credential). A purely read-only workflow may pass `scope: read` with the reader identity's secrets,
but that requires the reader-side setup below to be completed too.

## Azure / GitHub prerequisites

The OIDC identity is the Entra app **`ffc-admin-kv-writer`** (the same one the Cloudflare `wr-all`
flows use). It already holds **Key Vault Secrets Officer** on `kv-ffc-admin-prod-cbm`, so no extra
RBAC is needed.

Status of the one-time setup (most already done in the AZ KV migration):

- [x] **KV secrets exist** — `wr-all-ffc-whmcs-api-identifier` / `read-all-ffc-whmcs-api-identifier`
      and `wr-all-ffc-whmcs-api-secret` / `read-all-ffc-whmcs-api-secret` (plus the
      `wr-all-ffc-whmcs-api-url` / `read-all-ffc-whmcs-api-url` pair).
- [x] **Identifier + URL populated** — set to the FFC identifier and API endpoint.
- [x] **Federated credential** — `ffc-admin-kv-writer` trusts
      `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:whmcs-prod` (federated cred
      `gha-FFC-Cloudflare-Automation-whmcs-prod`). A previously malformed credential
      (`subject: repo:whmcs-prod-read`) was replaced.
- [x] **Vault RBAC** — `ffc-admin-kv-writer` has `Key Vault Secrets Officer` vault-wide.
- [ ] **Set the real WHMCS secret value** in KV — see the command below (it lives only in the legacy
      GH secret today).
- [ ] **GitHub repository Variables** — create `WR_ALL_FFC_AZURE_KV_CLIENT_ID` and
      `WR_ALL_FFC_AZURE_TENANT_ID` as repository-level **Variables** (not environment secrets — they
      are non-secret GUIDs). This keeps `whmcs-prod` (and every other environment) free of Azure
      creds; the per-environment federated credential is what actually gates access.

Set the real secret value (both scope copies):

```bash
az keyvault secret set --vault-name kv-ffc-admin-prod-cbm \
  --name wr-all-ffc-whmcs-api-secret --value '<WHMCS API secret>'
az keyvault secret set --vault-name kv-ffc-admin-prod-cbm \
  --name read-all-ffc-whmcs-api-secret --value '<WHMCS API secret>'
```

Create the repository Variables (values are the `ffc-admin-kv-writer` client id and the tenant id):

```bash
gh variable set WR_ALL_FFC_AZURE_KV_CLIENT_ID \
  --repo FreeForCharity/FFC-Cloudflare-Automation --body '<writer client id>'
gh variable set WR_ALL_FFC_AZURE_TENANT_ID \
  --repo FreeForCharity/FFC-Cloudflare-Automation --body '<tenant id>'
```

Until the last two boxes are checked, the migrated workflows fail fast: the action throws a clear
error if KV still returns the `PLACEHOLDER-SET-VIA-AZURE-PORTAL` scaffold value, and input
validation fails if the repository Variables are missing. The legacy
`ZBBEPFQ5W7RCSIME0NOQOYRQIDGTKBPU` secret can stay in `whmcs-prod` during cutover (nothing reads it
anymore).

## Rotation flow

To rotate the WHMCS credential, add a new version of `wr-all-ffc-whmcs-api-secret` (and the
`read-all-` copy) in Key Vault. The next workflow dispatch picks up the new value automatically — no
GitHub Environment Secret update and no sync workflow needed.

## Notes

- These workflows run on `windows-latest`. `azure/login@v3` and the Azure CLI (`az`) are
  preinstalled on GitHub's Windows runners, so OIDC + KV reads work the same as on Ubuntu (the
  `cloudflare-tokens-from-kv` action already runs on `windows-latest` in workflow 02).
- The action cannot be unit-tested in isolation (OIDC + Azure are real dependencies). Validate by
  dispatching any migrated WHMCS workflow (e.g. **31. WHMCS - Export Products**) once the real
  secret value and GitHub environment secrets are in place.

## Related

- Sibling action: `../cloudflare-tokens-from-kv/` — same pattern for Cloudflare API tokens
- `docs/github-actions-environments-and-secrets.md` — `whmcs-prod` environment reference
