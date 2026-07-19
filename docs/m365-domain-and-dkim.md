# Microsoft 365 domain status + DKIM (PowerShell)

This repo primarily enforces DNS in Cloudflare. These scripts are the start of **M365-side** checks
so we can align Cloudflare DNS with what Microsoft 365 expects.

## Internal (FFC tenant) vs external (charity's own tenant) — read this first

There are two very different kinds of "Microsoft" work in this repo, and mixing them up has real
consequences:

- **Internal / FFC-tenant workflows — `301`–`306` (named `M365 (FFC Tenant) - …`).** These
  authenticate to **FFC's own Microsoft 365 tenant** (Graph / Exchange Online) and act on it: `305`
  **adds a domain to the FFC tenant**, `304` enables DKIM in FFC's Exchange Online, `301`–`303` read
  FFC-tenant state. Only use these for domains whose mailboxes FFC itself hosts.
- **External-focused work — DNS records + delegated access.** For a charity that runs (or will run)
  **their own** Microsoft tenant, FFC's role is only:
  - put the DNS records they need into Cloudflare (`105` for individual records such as the
    `MS=msXXXXXXXX` verification TXT and DKIM selector CNAMEs the charity gets from _their_ admin
    center; `103` for the generic MX/SPF/DMARC standard), and/or
  - hand their contact **zone-scoped Cloudflare access** with
    `122. Cloudflare - Zone Member Add (Domain Admin)` so they can manage the records themselves.

> **The one-tenant rule (why `305` is INTERNAL ONLY):** a domain can be verified in **one**
> Microsoft 365 tenant at a time. If `305` adds a charity's domain to the FFC tenant, that charity
> can no longer verify it in their own tenant until it is removed from FFC's. For an external
> charity, **they add the domain in their own <https://admin.microsoft.com> first**; nothing
> tenant-side happens on FFC's side at all.

## What this includes

- Tenant discovery: `scripts/m365-tenant-discovery.ps1`
- Domain status + (optional) Microsoft-provided DNS record guidance:
  `scripts/m365-domain-status.ps1`
- List all tenant domains: `scripts/m365-domain-list.ps1`
- DKIM status / create / enable (Exchange Online): `scripts/m365-dkim.ps1`

GitHub Actions workflows (manual `workflow_dispatch`):

- Domain status + DKIM: `.github/workflows/303-m365-domain-and-dkim.yml`
- List all tenant domains: `.github/workflows/302-m365-list-domains.yml`
- Enable DKIM (create selector CNAMEs in Cloudflare and enable signing):
  `.github/workflows/304-m365-dkim-enable.yml`

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

- `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` and/or `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`

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
