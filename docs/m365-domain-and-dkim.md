# Microsoft 365 domain status + DKIM (PowerShell)

This repo primarily enforces DNS in Cloudflare. These scripts are the start of **M365-side** checks so we can align Cloudflare DNS with what Microsoft 365 expects.

## Prereqs
- PowerShell 7+ recommended
- Ability to authenticate to:
  - Microsoft Graph (for domain status)
  - Exchange Online (for DKIM)

The scripts will install required modules to the current user:
- `Microsoft.Graph`
- `ExchangeOnlineManagement`

## Tenant discovery (interactive)
If you want the repo to tell you exactly what values to use for `EXO_TENANT` / `EXO_ORGANIZATION`
and confirm Graph connectivity, run:

- `pwsh -File scripts/m365-tenant-discovery.ps1`

This uses **interactive browser sign-in** by default.

If you are running **Windows PowerShell 5.1**, the script defaults to using **Azure CLI** for the
login popup (more reliable than the Graph module on first-run).

If you prefer device code login:

- `pwsh -File scripts/m365-tenant-discovery.ps1 -Auth DeviceCode`

You can force which login method is used:
- Azure CLI (recommended on Windows PowerShell):
  - `powershell -File scripts/m365-tenant-discovery.ps1 -LoginProvider AzureCli`
- Graph PowerShell module:
  - `pwsh -File scripts/m365-tenant-discovery.ps1 -LoginProvider GraphModule`

To also check Exchange Online DKIM cmdlets interactively:

- `pwsh -File scripts/m365-tenant-discovery.ps1 -AlsoCheckExchangeOnline`

## Domain status (Graph)
Script: `scripts/m365-domain-status.ps1`

Examples:
- Interactive login:
  - `pwsh -File scripts/m365-domain-status.ps1 -Domain example.org`
- Device code login (useful on servers/terminals):
  - `pwsh -File scripts/m365-domain-status.ps1 -Domain example.org -Auth DeviceCode`
- Show Microsoft-provided DNS records (verification + service config):
  - `pwsh -File scripts/m365-domain-status.ps1 -Domain example.org -ShowDnsRecords`

Notes:
- Interactive mode uses Graph delegated scope `Domain.Read.All`.

### GitHub Actions (non-interactive)
This repo includes a manual workflow: `.github/workflows/5-m365-domain-and-dkim.yml`.

It is designed to run with GitHub Actions OIDC via `azure/login@v2` and then uses the Azure CLI
to fetch a Graph token.

Required secrets (environment: `m365-prod`):
- `AZURE_CLIENT_ID`
- `AZURE_TENANT_ID`
- `AZURE_SUBSCRIPTION_ID`

For DKIM (Exchange Online) app-only auth, you also need:
- `EXO_TENANT` (your tenant / org, e.g. `freeforcharity.onmicrosoft.com`)
- `EXO_ORGANIZATION` (same as `EXO_TENANT` unless you use a different org string)
- `EXO_CERT_PFX_BASE64` (base64-encoded PFX)
- `EXO_CERT_PFX_PASSWORD` (optional; empty if no password)

Notes:
- This workflow uses **one Entra app**: `AZURE_CLIENT_ID` is also used as the Exchange Online
  `AppId` (no separate `EXO_APP_ID` secret).

Notes:
- The workflow imports the PFX to `Cert:\CurrentUser\My` and uses its thumbprint.
- This is intentionally manual (`workflow_dispatch`) so changes like DKIM enablement aren’t
  automated without an operator.

## DKIM management (Exchange Online)
Script: `scripts/m365-dkim.ps1`

Examples:
- Check DKIM status and print selector CNAME targets:
  - `pwsh -File scripts/m365-dkim.ps1 -Domain example.org`
- Create DKIM config if missing (still disabled by default):
  - `pwsh -File scripts/m365-dkim.ps1 -Domain example.org -CreateIfMissing`
- Enable DKIM (will prompt via `ShouldProcess` unless you pass `-Confirm:$false`):
  - `pwsh -File scripts/m365-dkim.ps1 -Domain example.org -Enable -CreateIfMissing`

Notes:
- Expected DNS records are:
  - `selector1._domainkey.<domain>` CNAME `<tenant-specific target>`
  - `selector2._domainkey.<domain>` CNAME `<tenant-specific target>`

### Non-interactive (app-only) auth
`scripts/m365-dkim.ps1` supports app-only auth if you provide:
- `EXO_APP_ID` (in GitHub Actions, this is set to `AZURE_CLIENT_ID`)
- `EXO_TENANT` (used as `-Organization`)
- `EXO_CERT_THUMBPRINT` (certificate must already be present in the current user cert store)

In GitHub Actions, `EXO_CERT_THUMBPRINT` is set automatically after importing the PFX.

## Safety
- Scripts do not print secrets.
- `scripts/m365-dkim.ps1` supports `WhatIf` / `Confirm` for changes:
  - Example: `pwsh -File scripts/m365-dkim.ps1 -Domain example.org -Enable -WhatIf`
