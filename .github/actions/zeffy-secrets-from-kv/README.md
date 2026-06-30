# Composite action: `zeffy-secrets-from-kv`

Fetch the FFC **Zeffy organization API key** from Azure Key Vault at workflow runtime, via OIDC, and
expose it to later steps as `ZEFFY_API_KEY`. Mirrors `whmcs-secrets-from-kv` /
`cloudflare-tokens-from-kv`: Key Vault is the single source of truth, and no API key copy is stored
in GitHub.

## Why this action exists

The Zeffy public API (`https://api.zeffy.com/api/v1`) authenticates with an organization API key as
a `Bearer` token. Rather than store that key as a GitHub Environment Secret (which then drifts from
wherever it is mastered — the exact failure that bit the Cloudflare tokens), this action pulls the
current value from Key Vault at runtime via OIDC and masks it. Scripts read it from `ZEFFY_API_KEY`.

## Key Vault secret

| Secret name                  | Scope | Holds                             |
| ---------------------------- | ----- | --------------------------------- |
| `wr-all-ffc-zeffy-api-key`   | write | Zeffy organization API key        |
| `read-all-ffc-zeffy-api-key` | read  | Zeffy organization API key (copy) |

The Zeffy API is read-only, so the two copies hold identical values; `scope` only selects which
identity/copy is used. The action defaults to `write` (the identity that already has a federated
credential configured).

## Inputs

| Name              | Required | Default                 | Description                                                                                   |
| ----------------- | -------- | ----------------------- | --------------------------------------------------------------------------------------------- |
| `scope`           | no       | `write`                 | `write` loads `wr-all-*` (via `ffc-admin-kv-writer`); `read` loads `read-all-*` (via reader). |
| `vault-name`      | no       | `kv-ffc-admin-prod-cbm` | Azure Key Vault name.                                                                         |
| `azure-client-id` | yes      | —                       | OIDC client ID. Pass `${{ vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}`.                             |
| `azure-tenant-id` | yes      | —                       | Azure tenant ID. Pass `${{ vars.WR_ALL_FFC_AZURE_TENANT_ID }}`.                               |

## Output

Exports `ZEFFY_API_KEY` to `$GITHUB_ENV` (heredoc-delimited so a value can never inject extra
variables), masked via `::add-mask::` before export.

## Usage

```yaml
permissions:
  id-token: write
  contents: read

jobs:
  zeffy:
    runs-on: windows-latest
    environment: zeffy-prod
    steps:
      - uses: actions/checkout@v5
      - uses: ./.github/actions/zeffy-secrets-from-kv
        with:
          azure-client-id: ${{ vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}
          azure-tenant-id: ${{ vars.WR_ALL_FFC_AZURE_TENANT_ID }}
      - shell: pwsh
        run: pwsh -NoProfile -File .\scripts\zeffy-payments-export.ps1
```

## One-time setup

1. **Generate the key** — the Zeffy **organization owner** opens Zeffy → Settings → Integrations and
   generates an API key.
2. **Store it in Key Vault** (both scope copies):
   ```bash
   az keyvault secret set --vault-name kv-ffc-admin-prod-cbm --name wr-all-ffc-zeffy-api-key   --value '<zeffy api key>'
   az keyvault secret set --vault-name kv-ffc-admin-prod-cbm --name read-all-ffc-zeffy-api-key --value '<zeffy api key>'
   ```
3. **Federated credential** — `ffc-admin-kv-writer` must trust
   `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:zeffy-prod`:
   ```bash
   az ad app federated-credential create --id <ffc-admin-kv-writer app id> --parameters '{
     "name": "gha-FFC-Cloudflare-Automation-zeffy-prod",
     "issuer": "https://token.actions.githubusercontent.com",
     "subject": "repo:FreeForCharity/FFC-Cloudflare-Automation:environment:zeffy-prod",
     "audiences": ["api://AzureADTokenExchange"]
   }'
   ```

Until the key is set, the action fails fast (it throws if KV still holds the
`PLACEHOLDER-SET-VIA-AZURE-PORTAL` scaffold value).

## Related

- `docs/zeffy-api.md` — full Zeffy API reference, features, and how FFC uses it.
- Sibling actions: `../whmcs-secrets-from-kv/`, `../cloudflare-tokens-from-kv/`.
