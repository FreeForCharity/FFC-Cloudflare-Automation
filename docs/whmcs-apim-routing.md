# WHMCS API calls routed through Azure API Management (static egress IP)

## Why

The WHMCS API (`https://freeforcharity.org/hub/includes/api.php`) enforces an **IP access
restriction** — requests from a non-allowlisted source IP are rejected with
`result: error / message: Invalid IP <addr>` _before_ the identifier+secret are even evaluated.

GitHub-hosted runners egress from a **large, dynamic pool of IPs**, so they cannot be allowlisted
reliably. Every WHMCS workflow would intermittently (or always) fail with `Invalid IP`.

## How

WHMCS calls are routed through the existing Azure API Management instance
**`apim-ffc-gateway-prod`**, which has a **single static public IP: `20.231.116.111`**. For a
non-VNet APIM instance, that public IP is what the backend sees on outbound calls — so WHMCS sees
one stable IP regardless of which runner ran the job.

```
GitHub runner (dynamic IP)
  └─ POST https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php
       Ocp-Apim-Subscription-Key: <whmcs-ops key, from Key Vault>
       └─ APIM (egresses as 20.231.116.111) ──► https://freeforcharity.org/hub/includes/api.php
```

This mirrors the existing `cpanel` API in the same APIM instance (a proxy to an IP-restricted cPanel
host with a dedicated subscription key).

## APIM configuration

| Item          | Value                                                                       |
| ------------- | --------------------------------------------------------------------------- |
| Instance      | `apim-ffc-gateway-prod` (Developer SKU, East US, non-VNet)                  |
| Static IP     | `20.231.116.111`                                                            |
| API id / path | `whmcs` / `whmcs`                                                           |
| Backend       | `https://freeforcharity.org/hub/includes`                                   |
| Operation     | `POST /api.php`                                                             |
| Gateway URL   | `https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php`                 |
| Subscription  | **Required** — `Ocp-Apim-Subscription-Key` header, subscription `whmcs-ops` |

The workflows set `WHMCS_API_URL` to the gateway URL. The `whmcs-secrets-from-kv` action also
fetches the `whmcs-ops` subscription key from Key Vault and exports it as
`WHMCS_APIM_SUBSCRIPTION_KEY`; the WHMCS PowerShell helpers (`Invoke-WhmcsApi` in
`scripts/whmcs-api-common.ps1` and each self-contained export script) add it as the
`Ocp-Apim-Subscription-Key` request header when that env var is present. So the scripts stay
backend-agnostic: with the env var unset they call WHMCS directly; with it set they authenticate to
APIM.

## Required WHMCS configuration (one-time, manual)

In **WHMCS admin → Configuration → System Settings → General Settings → Security**:

1. **API IP Access Restriction** → add **`20.231.116.111`** (the APIM gateway IP). This is the only
   IP WHMCS needs to allow for the API.
2. **Proxy IP Header** → set to **`CF-Connecting-IP`** (or `CF_CONNECTING_IP` if the field requires
   the underscore form). **This step is essential.**

Why step 2 matters: `freeforcharity.org` sits behind **Cloudflare** (the Cloudflare ranges are in
WHMCS's _Trusted Proxies_), so the real path is `runner → APIM → Cloudflare → WHMCS`. By default
WHMCS reads the client IP from `X-Forwarded-For`, and APIM **appends the original caller's
(runner's) dynamic IP** to that header — so WHMCS would see the runner, not APIM, and reject it.
Setting the proxy header to `CF-Connecting-IP` makes WHMCS use the IP that actually connected to
Cloudflare — which is APIM's stable `20.231.116.111` — and the runner IP in `X-Forwarded-For` is
ignored. (This is also the generally-correct setting for a Cloudflare-fronted WHMCS: normal
visitors' IPs come from the same header.)

After both steps, the migrated workflows run cleanly: OIDC → Key Vault → WHMCS via APIM. Verified
with a live `GetProducts` call returning `result: success`.

## Security model

Three independent layers gate a WHMCS API call, all sourced from Key Vault at runtime:

1. **APIM subscription key** (`Ocp-Apim-Subscription-Key`) — the `whmcs` API has
   `subscriptionRequired: true`; a call without the `whmcs-ops` key gets `401` from APIM and never
   reaches WHMCS.
2. **WHMCS IP allowlist** — WHMCS only accepts requests it sees as coming from APIM's
   `20.231.116.111` (resolved via the `CF-Connecting-IP` proxy header).
3. **WHMCS identifier + secret** — the API credential itself, mastered in Key Vault.

The `whmcs-ops` subscription is **API-scoped** (it can only call the `whmcs` API, nothing else in
the gateway), mirroring the `cpanel-ops` subscription. Its key lives in Key Vault as
`read-all-ffc-apim-whmcs-subscription-key` / `wr-all-ffc-apim-whmcs-subscription-key` (identical
values; the scope only selects which identity reads it).

To rotate the subscription key: regenerate it on the `whmcs-ops` subscription in APIM, then update
both KV secret versions. The next workflow run picks it up.

## Caveats

- **Developer SKU has no SLA** and is a single unit; fine for batch automation, but if APIM is down
  WHMCS automation pauses.
- The static IP is stable for the instance lifetime but **can change** on stop/start, tier change,
  or delete/recreate — avoid stopping/starting the instance casually.
