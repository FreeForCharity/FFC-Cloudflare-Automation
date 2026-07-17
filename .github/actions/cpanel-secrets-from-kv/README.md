# cpanel-secrets-from-kv

Composite action that authenticates to Azure via OIDC and loads the FFC cPanel (InterServer) API
credentials + the APIM `cpanel`-ops subscription key from Key Vault, masks them, and exports them to
later steps. Mirrors [`whmcs-secrets-from-kv`](../whmcs-secrets-from-kv/).

Exports (all masked, via `GITHUB_ENV`):

| Env var                        | KV secret (`{scope}` = `read-all` / `wr-all`)                                                |
| ------------------------------ | -------------------------------------------------------------------------------------------- |
| `CPANEL_API_USER`              | `{scope}-cbm-cpanel-ffc-interserver-user`                                                    |
| `CPANEL_API_TOKEN`             | `{scope}-cbm-cpanel-ffc-interserver-api-token`                                               |
| `CPANEL_SERVER`                | `{scope}-cbm-cpanel-ffc-interserver-server` (e.g. `webhosting1900.is.cc`)                    |
| `CPANEL_APIM_SUBSCRIPTION_KEY` | `apim-subscription-secret-name` input (default `read-all-ffc-apim-gateway-subscription-key`) |

## Why via APIM

The cPanel host enforces an IP restriction. Calls are routed through the APIM `cpanel` API
(`https://apim-ffc-gateway-prod.azure-api.net/cpanel/execute/{module}/{function}`), which egresses
from the gateway's static IP (`20.231.116.111`) — allowlisted on the cPanel host — so a dynamic
GitHub-runner IP is never blocked. Auth to cPanel UAPI is
`Authorization: cpanel <CPANEL_API_USER>:<CPANEL_API_TOKEN>`; APIM requires the
`Ocp-Apim-Subscription-Key` header (the cPanel-ops subscription).

## Usage

```yaml
permissions:
  id-token: write
  contents: read
steps:
  - uses: ./.github/actions/cpanel-secrets-from-kv
    with:
      azure-client-id: ${{ vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}
      azure-tenant-id: ${{ vars.WR_ALL_FFC_AZURE_TENANT_ID }}
```

Consumed by [`227-whmcs-hooks-deploy.yml`](../../workflows/227-whmcs-hooks-deploy.yml).
