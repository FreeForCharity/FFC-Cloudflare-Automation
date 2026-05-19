# Composite action: `cloudflare-tokens-from-kv`

Fetch FFC and CM Cloudflare API tokens from Azure Key Vault at workflow runtime, via OIDC. Replaces the older pattern of holding parallel copies of the tokens as GitHub Environment Secrets.

## Why this action exists

Before this action, every workflow that needed a Cloudflare token consumed it from a GH Environment Secret:

```yaml
env:
  CLOUDFLARE_API_TOKEN_FFC: ${{ secrets.FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}
  CLOUDFLARE_API_TOKEN_CM:  ${{ secrets.CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}
```

That created the same secret in multiple places (KV + each consuming repo's env). Rotating a token meant updating N copies — drift was inevitable. On 2026-05-18 a CM Cloudflare token rotation in KV did not reach `FFC-Cloudflare-Automation`'s env secret for 4 months; any workflow_dispatch against CM zones returned 401.

This action makes KV the single source of truth. Workflows authenticate to Azure via OIDC (no Azure password ever stored in GitHub), pull the current token value from KV at runtime, and expose it to downstream steps with the same env-var names downstream scripts already expect.

See also: `docs/runbooks/github-actions-environments-and-secrets.md` and the [F1 refactor tracking issue](https://github.com/FreeForCharity/FFC-IN-ClarkeMoyerAdmin/issues/105).

## Inputs

| Name | Required | Default | Description |
|---|---|---|---|
| `scope` | yes | — | `read` loads `READ_ALL_*` tokens; `write` loads `WR_ALL_*` tokens |
| `vault-name` | no | `kv-ffc-admin-prod-cbm` | Azure Key Vault name |
| `azure-client-id` | yes | — | OIDC client ID for the managed identity with `Get` on the vault. Pass `${{ secrets.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}` from the caller. |
| `azure-tenant-id` | yes | — | Azure tenant ID. Pass `${{ secrets.WR_ALL_FFC_AZURE_TENANT_ID }}` from the caller. |

## Outputs

This action does not declare formal outputs. It exports two environment variables visible to all subsequent steps in the same job:

- `CLOUDFLARE_API_TOKEN_FFC` — FFC Cloudflare account token (zone + DNS)
- `CLOUDFLARE_API_TOKEN_CM`  — CM (Clarke Moyer) Cloudflare account token (zone + DNS)

Both are masked via `::add-mask::` before export, so they will not appear in workflow logs.

## Usage (cross-repo from FFC-Cloudflare-Automation)

```yaml
permissions:
  id-token: write   # required for OIDC
  contents: read

jobs:
  cloudflare:
    runs-on: ubuntu-latest
    environment: cloudflare-prod
    steps:
      - uses: actions/checkout@v4

      - uses: FreeForCharity/FFC-IN-ClarkeMoyerAdmin/.github/actions/cloudflare-tokens-from-kv@main
        with:
          scope: write
          azure-client-id: ${{ secrets.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}
          azure-tenant-id: ${{ secrets.WR_ALL_FFC_AZURE_TENANT_ID }}

      - name: Use the tokens
        shell: pwsh
        run: |
          # CLOUDFLARE_API_TOKEN_FFC and _CM are now available as env vars.
          Invoke-RestMethod `
            -Uri 'https://api.cloudflare.com/client/v4/zones?per_page=1' `
            -Headers @{ Authorization = "Bearer $env:CLOUDFLARE_API_TOKEN_CM" }
```

For production use, pin to a commit SHA rather than `@main`:

```yaml
- uses: FreeForCharity/FFC-IN-ClarkeMoyerAdmin/.github/actions/cloudflare-tokens-from-kv@<sha>
```

## Usage (same-repo, from CBMadmin)

```yaml
- uses: ./.github/actions/cloudflare-tokens-from-kv
  with:
    scope: read
    azure-client-id: ${{ secrets.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}
    azure-tenant-id: ${{ secrets.WR_ALL_FFC_AZURE_TENANT_ID }}
```

## Required prerequisites in the consuming repo

The calling workflow's `environment:` must hold two secrets:

| Secret name | Role |
|---|---|
| `WR_ALL_FFC_AZURE_KV_CLIENT_ID` | OIDC client ID of the managed identity that has `Get` permission on the KV |
| `WR_ALL_FFC_AZURE_TENANT_ID` | Azure tenant ID |

The managed identity must also have a federated credential trust on `repo:<owner>/<repo>:environment:<env-name>` for each consuming repo+env. CBMadmin already has this; `FFC-Cloudflare-Automation`'s `cloudflare-prod` env has been bound since 2026-02-22.

## Rotation flow

To rotate a Cloudflare token (either FFC or CM):

1. Generate a new token in the Cloudflare dashboard with the same scope set as the old one
2. Open KV: https://portal.azure.com/#@/asset/Microsoft_Azure_KeyVault/Secret/https://kv-ffc-admin-prod-cbm.vault.azure.net/secrets/wr-all-{ffc,cm}-cloudflare-api-token-zone-and-dns
3. `+ New Version` → paste new token → Create
4. (Optional) re-dispatch any consumer workflow to validate

No GitHub Environment Secret updates needed. No additional sync workflow needed.

## Local testing

The action cannot be unit-tested in isolation (OIDC + Azure are real dependencies). The integration test is workflow #7 `7-cloudflare-verify-token-from-keyvault.yml` in CBMadmin, which exercises the same KV read + Cloudflare API call path.

## Related

- Tracking issue: [#105](https://github.com/FreeForCharity/FFC-IN-ClarkeMoyerAdmin/issues/105)
- Architectural memory: KV is master; GH consumes via OIDC
- Pattern source: `.github/workflows/7-cloudflare-verify-token-from-keyvault.yml`
