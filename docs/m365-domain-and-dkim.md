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
- Uses Graph scope `Domain.Read.All`.

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

## Safety
- Scripts do not print secrets.
- `scripts/m365-dkim.ps1` supports `WhatIf` / `Confirm` for changes:
  - Example: `pwsh -File scripts/m365-dkim.ps1 -Domain example.org -Enable -WhatIf`
