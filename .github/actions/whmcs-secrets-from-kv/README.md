# Composite action: `whmcs-secrets-from-kv`

Fetch the FFC WHMCS API credential (identifier + secret, and optionally the access key) from Azure
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
`cloudflare-tokens-from-kv` pattern already used across this repo's DNS workflows. Workflows
authenticate to Azure via OIDC (no Azure password stored in GitHub), pull the current values from KV
at runtime, and expose them to downstream steps using the same env-var names the WHMCS PowerShell
scripts already read (`scripts/whmcs-api-common.ps1` → `Resolve-WhmcsCredentials`).

## Inputs

| Name                     | Required | Default                    | Description                                                                                                                             |
| ------------------------ | -------- | -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `vault-name`             | no       | `kv-ffc-admin-prod-cbm`    | Azure Key Vault name                                                                                                                    |
| `azure-client-id`        | yes      | —                          | OIDC client ID for the managed identity with `Get` on the vault. Pass `${{ secrets.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}`.                   |
| `azure-tenant-id`        | yes      | —                          | Azure tenant ID. Pass `${{ secrets.WR_ALL_FFC_AZURE_TENANT_ID }}`.                                                                      |
| `identifier-secret-name` | no       | `ffc-whmcs-api-identifier` | Key Vault secret name holding the WHMCS API identifier                                                                                  |
| `secret-secret-name`     | no       | `ffc-whmcs-api-secret`     | Key Vault secret name holding the WHMCS API secret                                                                                      |
| `access-key-secret-name` | no       | `''` (skip)                | Key Vault secret name holding the optional WHMCS API access key. Leave empty to skip; set only if the WHMCS API requires an access key. |

## Outputs

This action does not declare formal outputs. It exports environment variables visible to all
subsequent steps in the same job:

- `WHMCS_API_IDENTIFIER` — WHMCS API identifier
- `WHMCS_API_SECRET` — WHMCS API secret
- `WHMCS_API_ACCESS_KEY` — WHMCS API access key (only when `access-key-secret-name` is set)

All exported values are masked via `::add-mask::` before export, so they will not appear in workflow
logs.

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
          azure-client-id: ${{ secrets.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}
          azure-tenant-id: ${{ secrets.WR_ALL_FFC_AZURE_TENANT_ID }}

      - name: Call WHMCS
        shell: pwsh
        env:
          WHMCS_API_URL: 'https://freeforcharity.org/hub/includes/api.php'
        run: |
          # WHMCS_API_IDENTIFIER / WHMCS_API_SECRET are now available as env vars.
          pwsh -NoProfile -File .\scripts\whmcs-products-export.ps1
```

If the WHMCS API is configured to require an access key, store it in KV and add:

```yaml
- uses: ./.github/actions/whmcs-secrets-from-kv
  with:
    azure-client-id: ${{ secrets.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}
    azure-tenant-id: ${{ secrets.WR_ALL_FFC_AZURE_TENANT_ID }}
    access-key-secret-name: ffc-whmcs-api-access-key
```

## Required prerequisites in the consuming environment

The calling job's `environment: whmcs-prod` must hold two secrets:

| Secret name                     | Role                                                                       |
| ------------------------------- | -------------------------------------------------------------------------- |
| `WR_ALL_FFC_AZURE_KV_CLIENT_ID` | OIDC client ID of the managed identity that has `Get` permission on the KV |
| `WR_ALL_FFC_AZURE_TENANT_ID`    | Azure tenant ID                                                            |

These are **identifiers, not passwords** — the same pair already used by the Cloudflare KV action.

### One-time Azure setup (cannot be done from the web sandbox)

A human with Azure access must, in the `kv-ffc-admin-prod-cbm` vault and the managed identity:

1. **Create the KV secrets** (Key Vault → Secrets → Generate/Import):
   - `ffc-whmcs-api-identifier` → the WHMCS API identifier (currently
     `zbBEpfq5W7RCSImE0NOqoYrqIDGTkBPu`)
   - `ffc-whmcs-api-secret` → the WHMCS API secret (currently mastered as the GH secret named
     `ZBBEPFQ5W7RCSIME0NOQOYRQIDGTKBPU`)
   - `ffc-whmcs-api-access-key` → only if the WHMCS API uses an access key
2. **Grant the managed identity `Get` on those secrets** (RBAC role `Key Vault Secrets User` on the
   vault, or an access policy with secret `Get`). The same identity used for `WR_ALL_*` Cloudflare
   reads can be reused.
3. **Add a federated credential** on that identity for the WHMCS environment, because federated
   credentials are scoped per environment and the existing ones only cover the Cloudflare envs:
   - Issuer: `https://token.actions.githubusercontent.com`
   - Audience: `api://AzureADTokenExchange`
   - Subject: `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:whmcs-prod`
4. **Add `WR_ALL_FFC_AZURE_KV_CLIENT_ID` and `WR_ALL_FFC_AZURE_TENANT_ID`** to the `whmcs-prod`
   GitHub environment (Settings → Environments → `whmcs-prod` → Environment secrets).

Until steps 1–4 are done, the migrated workflows' Azure login / KV fetch step fails fast with a
clear error (and the old GH `ZBBEPFQ5W7RCSIME0NOQOYRQIDGTKBPU` secret can stay in place as a
fallback during cutover).

## Rotation flow

To rotate the WHMCS credential, open its Key Vault entry, click **+ New Version**, paste the new
value, and save. The next workflow dispatch picks up the new value automatically — no GitHub
Environment Secret update and no sync workflow needed.

## Notes

- These workflows run on `windows-latest`. `azure/login@v3` and the Azure CLI (`az`) are
  preinstalled on GitHub's Windows runners, so OIDC + KV reads work the same as on Ubuntu (the
  `cloudflare-tokens-from-kv` action already runs on `windows-latest` in workflow 02).
- The action cannot be unit-tested in isolation (OIDC + Azure are real dependencies). Validate by
  dispatching any migrated WHMCS workflow (e.g. **31. WHMCS - Export Products**) after the Azure
  setup above is complete.

## Related

- Sibling action: `../cloudflare-tokens-from-kv/` — same pattern for Cloudflare API tokens
- `docs/github-actions-environments-and-secrets.md` — `whmcs-prod` environment reference
