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
       └─ APIM (egresses as 20.231.116.111) ──► https://freeforcharity.org/hub/includes/api.php
```

This mirrors the existing `cpanel` API in the same APIM instance (a proxy to an IP-restricted cPanel
host).

## APIM configuration

| Item          | Value                                                       |
| ------------- | ----------------------------------------------------------- |
| Instance      | `apim-ffc-gateway-prod` (Developer SKU, East US, non-VNet)  |
| Static IP     | `20.231.116.111`                                            |
| API id / path | `whmcs` / `whmcs`                                           |
| Backend       | `https://freeforcharity.org/hub/includes`                   |
| Operation     | `POST /api.php`                                             |
| Gateway URL   | `https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php` |
| Subscription  | **Not required** (see hardening note below)                 |

The workflows set `WHMCS_API_URL` to the gateway URL; the WHMCS PowerShell scripts are
backend-agnostic and need no change. No subscription key is sent, so the heterogeneous
self-contained export scripts did not have to be modified.

## Required WHMCS configuration (one-time, manual)

Allowlist the APIM IP on the WHMCS API credential so WHMCS accepts APIM's traffic:

- **WHMCS admin → System Settings → API Credentials** → edit the credential used by automation →
  **Allowed IPs** → add **`20.231.116.111`** → Save.
- (Legacy installs: **Setup → General Settings → Security → API IP Access Restriction**.)

After this, the migrated workflows run cleanly: OIDC → Key Vault → WHMCS via APIM.

## Security note

The `whmcs` API is currently **keyless** (`subscriptionRequired: false`). This keeps the change
minimal and is security-equivalent to the prior direct-to-WHMCS setup: every WHMCS action still
requires the identifier + secret (now mastered in Key Vault), and WHMCS only accepts the APIM IP.

A dedicated **API-scoped subscription `whmcs-ops`** is already provisioned, and its key is stored in
Key Vault as `read-all-ffc-apim-whmcs-subscription-key` / `wr-all-ffc-apim-whmcs-subscription-key`.
To harden (defense-in-depth) later, set the `whmcs` API to `subscriptionRequired: true`, have
`whmcs-secrets-from-kv` export the key, and send it as the `Ocp-Apim-Subscription-Key` header (via
`Invoke-WhmcsApi` in `scripts/whmcs-api-common.ps1` and the self-contained export scripts).

## Caveats

- **Developer SKU has no SLA** and is a single unit; fine for batch automation, but if APIM is down
  WHMCS automation pauses.
- The static IP is stable for the instance lifetime but **can change** on stop/start, tier change,
  or delete/recreate — avoid stopping/starting the instance casually.
