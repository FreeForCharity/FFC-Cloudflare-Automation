# Microsoft 365 domain status + DKIM (PowerShell)

This repo primarily enforces DNS in Cloudflare. These scripts are the start of **M365-side** checks
so we can align Cloudflare DNS with what Microsoft 365 expects.

## What this includes

- Tenant discovery: `scripts/m365-tenant-discovery.ps1`
- Domain status + (optional) Microsoft-provided DNS record guidance:
  `scripts/m365-domain-status.ps1`
- List all tenant domains: `scripts/m365-domain-list.ps1`
- DKIM status / create / enable (Exchange Online): `scripts/m365-dkim.ps1`

GitHub Actions workflows (manual `workflow_dispatch`):

- Domain status + DKIM: `.github/workflows/5-m365-domain-and-dkim.yml`
- List all tenant domains: `.github/workflows/6-m365-list-domains.yml`
- Enable DKIM (create selector CNAMEs in Cloudflare and enable signing):
  `.github/workflows/8-m365-dkim-enable.yml`

## End-to-end testing

For a complete, protected-environment runbook (Cloudflare baseline + M365 verification + DKIM
end-to-end + Defender DKIM v2 validation), see:

- `docs/end-to-end-testing-m365-cloudflare.md`

## Prerequisites

- PowerShell 7+ recommended
- Ability to authenticate to:
  - Microsoft Graph (for domain status)
  - Exchange Online (for DKIM)

Interactive scripts may install required modules to the current user (first run):

- `Microsoft.Graph` (only when using the Graph PowerShell login path)
- `ExchangeOnlineManagement` (only for Exchange Online / DKIM operations)

If you run with a pre-acquired Graph token (for example `GRAPH_ACCESS_TOKEN`), the scripts use Graph
REST and do not need the Graph PowerShell module.

## GitHub Actions prerequisites

Required secrets (environment: `m365-prod`):

- `FFC_AZURE_CLIENT_ID`
- `FFC_AZURE_TENANT_ID`
- `FFC_EXO_CERT_PFX_BASE64` (base64-encoded PFX containing the private key)
- `FFC_EXO_CERT_PASSWORD` (PFX password)

Required secrets (environment: `cloudflare-prod`):

- `CLOUDFLARE_API_KEY_DNS_ONLY`

Permissions:

- Graph application permissions (admin-consented): `Domain.Read.All` (or `Directory.Read.All`)
- Exchange Online application role (admin-consented): `Exchange.ManageAsApp`

## DKIM management (Exchange Online)

Script: `scripts/m365-dkim.ps1`

Examples:

- Check DKIM status and print selector CNAME targets:
  - `pwsh -File scripts/m365-dkim.ps1 -Domain example.org`
- Create DKIM config if missing (still disabled by default):
  - `pwsh -File scripts/m365-dkim.ps1 -Domain example.org -CreateIfMissing`
- Enable DKIM (will prompt via `ShouldProcess` unless you pass `-Confirm:$false`):
  - `pwsh -File scripts/m365-dkim.ps1 -Domain example.org -Enable -CreateIfMissing`

### Non-interactive (app-only) auth

`scripts/m365-dkim.ps1` supports app-only auth if you provide:

- `EXO_APP_ID` (optional; in GitHub Actions, this can be set to `FFC_AZURE_CLIENT_ID`)
- `EXO_ORGANIZATION` (used as `-Organization`; in workflows we resolve this via Graph as the
  tenant's `*.onmicrosoft.com` domain)
- Either:
  - `EXO_CERT_THUMBPRINT` (Windows/local only, certificate already in cert store), OR
  - `EXO_CERT_PFX_BASE64` + `EXO_CERT_PFX_PASSWORD` (cross-platform)

In GitHub Actions, we use environment secrets `FFC_EXO_CERT_PFX_BASE64` and `FFC_EXO_CERT_PASSWORD`
and the script reads them automatically.

## Safety

- Scripts do not print secrets.
- `scripts/m365-dkim.ps1` supports `WhatIf` / `Confirm` for changes:
  - Example: `pwsh -File scripts/m365-dkim.ps1 -Domain example.org -Enable -WhatIf`
