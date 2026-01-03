# Microsoft 365 domain status + DKIM (PowerShell)

This repo primarily enforces DNS in Cloudflare. These scripts are the start of **M365-side** checks so we can align Cloudflare DNS with what Microsoft 365 expects.

## What this includes

- Tenant discovery: `scripts/m365-tenant-discovery.ps1`
- Domain status + (optional) Microsoft-provided DNS record guidance: `scripts/m365-domain-status.ps1`
- List all tenant domains: `scripts/m365-domain-list.ps1`
- DKIM status / create / enable (Exchange Online): `scripts/m365-dkim.ps1`

GitHub Actions workflows (manual `workflow_dispatch`):

- Domain status + DKIM: `.github/workflows/5-m365-domain-and-dkim.yml`
- List all tenant domains: `.github/workflows/6-m365-list-domains.yml`
- Enable DKIM (create selector CNAMEs in Cloudflare and enable signing): `.github/workflows/8-m365-dkim-enable.yml`

## Prerequisites
- PowerShell 7+ recommended
- Ability to authenticate to:
  - Microsoft Graph (for domain status)
  - Exchange Online (for DKIM)

Interactive scripts may install required modules to the current user (first run):

- `Microsoft.Graph` (only when using the Graph PowerShell login path)
- `ExchangeOnlineManagement` (only for Exchange Online / DKIM operations)

If you run with a pre-acquired Graph token (for example `GRAPH_ACCESS_TOKEN`), the scripts use
Graph REST and do not need the Graph PowerShell module.

## Authentication modes (summary)

- **Interactive (delegated) login**
  - Uses `Microsoft.Graph` PowerShell module (`Connect-MgGraph`) and delegated permissions.
  - Best for local operator use.
- **Token-based (REST)**
  - Uses a bearer token you provide as `-AccessToken` or `GRAPH_ACCESS_TOKEN`.
  - Best for automation, and avoids Graph module import quirks.
- **GitHub Actions (OIDC)**
  - Uses `azure/login@v2` (OIDC) and Azure CLI to fetch a Graph token.
  - Requires an Entra app with Graph *application* permissions.

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

## Domain onboarding preflight (read-only)

If your goal is to **add a new domain** to Microsoft 365, this repo currently focuses on
validation (not mutation). The script below is intended as a preflight checklist:

Script: `scripts/m365-domain-preflight.ps1`

What it does:

1. Identify the **active tenant** from your current auth context.
2. Check whether the domain already exists in that tenant (Graph).
3. Run the repo’s Cloudflare DNS **audit** for the domain (read-only).
4. Identify gaps (SPF/MX/DMARC/CNAME/SRV; plus DKIM selector presence).

GitHub Actions:

- Run the manual workflow `.github/workflows/7-m365-domain-preflight.yml`.
- Provide the `domain` input (for example: `example.org`).

This workflow runs as two jobs:

- **Graph checks** in `m365-prod` (uses `FFC_AZURE_CLIENT_ID` / `FFC_AZURE_TENANT_ID`)
- **Cloudflare checks** in `cloudflare-prod` (uses `CLOUDFLARE_API_KEY_DNS_ONLY`)

Notes:

- “Active tenant” means: whichever tenant your Graph token is for.
- DKIM target validation requires the domain to already exist in the tenant; otherwise we can only
  check whether the Cloudflare selector records exist.

## List tenant domains (Graph)
Script: `scripts/m365-domain-list.ps1`

Examples:
- Using an existing Graph token (recommended if you already ran `az login` + token acquisition):
  - `pwsh -File scripts/m365-domain-list.ps1 -AccessToken $env:GRAPH_ACCESS_TOKEN`
- Let the script fetch a token via Azure CLI (requires `az login`):
  - `pwsh -File scripts/m365-domain-list.ps1`

Notes:
- This uses Microsoft Graph `GET /v1.0/domains` and prints a tab-delimited list.

### GitHub Actions (non-interactive)

Setup (where/how to set Environment + secrets):

- See `docs/github-actions-environments-and-secrets.md` for the exact GitHub UI paths and required
  secrets for `m365-prod`.

Required secrets (environment: `m365-prod`):
- `FFC_AZURE_CLIENT_ID`
- `FFC_AZURE_TENANT_ID`

Optional:
- `AZURE_SUBSCRIPTION_ID` (not required for these M365 workflows; they use `allow-no-subscriptions: true`)

Graph permissions:
- The Entra app referenced by `FFC_AZURE_CLIENT_ID` must have Microsoft Graph permissions to read domains.
  In practice, grant and admin-consent either:
  - Application permission `Domain.Read.All` (recommended for GitHub Actions), or
  - Application permission `Directory.Read.All`.

For DKIM (Exchange Online) app-only auth, you also need (environment: `m365-prod`):
- `FFC_EXO_CERT_PFX_BASE64` (base64-encoded PFX containing the private key)
- `FFC_EXO_CERT_PASSWORD` (PFX password)

Notes:
- This workflow uses **one Entra app**: `FFC_AZURE_CLIENT_ID` is also used as the Exchange Online
  `AppId` (no separate `EXO_APP_ID` secret).
- The DKIM script also supports the non-FFC env var names (`EXO_CERT_PFX_BASE64` / `EXO_CERT_PFX_PASSWORD`)
  for local runs, but the GitHub Actions workflow uses the environment-scoped `FFC_...` secrets.

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

- `EXO_APP_ID` (optional; in GitHub Actions, this can be set to `FFC_AZURE_CLIENT_ID`)
- `EXO_ORGANIZATION` (used as `-Organization`; in workflows we resolve this via Graph as the tenant's `*.onmicrosoft.com` domain)
- Either:
  - `EXO_CERT_THUMBPRINT` (Windows/local only, certificate already in cert store), OR
  - `EXO_CERT_PFX_BASE64` + `EXO_CERT_PFX_PASSWORD` (cross-platform)

In GitHub Actions, we use environment secrets `FFC_EXO_CERT_PFX_BASE64` and `FFC_EXO_CERT_PASSWORD` and the script reads them automatically.

## Safety
- Scripts do not print secrets.
- `scripts/m365-dkim.ps1` supports `WhatIf` / `Confirm` for changes:
  - Example: `pwsh -File scripts/m365-dkim.ps1 -Domain example.org -Enable -WhatIf`

## Limitations (important)

- These helpers are **not** a full M365 admin tool; they focus on domain/DKIM visibility and a few
  safe, targeted operations.
- The Graph-based scripts are **read-only** (domain status, domain list, DNS record guidance).
- Graph REST results depend on permissions:
  - Delegated interactive runs require the signed-in user to have sufficient rights.
  - GitHub Actions runs require Graph *application* permissions + admin consent.
- On some systems (especially Windows PowerShell 5.1), importing `Microsoft.Graph` can be fragile.
  Use the token-based REST path (`GRAPH_ACCESS_TOKEN`) when you want maximum reliability.
- DKIM enablement is intentionally manual and gated (interactive confirmation or manual workflow
  dispatch). It should not be run unattended.
# Microsoft 365 domain status + DKIM (PowerShell)

This repo primarily enforces DNS in Cloudflare. These scripts are the start of **M365-side** checks so we can align Cloudflare DNS with what Microsoft 365 expects.

## What this includes

- Tenant discovery: `scripts/m365-tenant-discovery.ps1`
- Domain status + (optional) Microsoft-provided DNS record guidance: `scripts/m365-domain-status.ps1`
- List all tenant domains: `scripts/m365-domain-list.ps1`
- DKIM status / create / enable (Exchange Online): `scripts/m365-dkim.ps1`

GitHub Actions workflows (manual `workflow_dispatch`):

- Domain status + DKIM: `.github/workflows/5-m365-domain-and-dkim.yml`
- List all tenant domains: `.github/workflows/6-m365-list-domains.yml`
<<<<<<< HEAD
- Enable DKIM (creates selector CNAMEs in Cloudflare and enables signing): `.github/workflows/8-m365-dkim-enable.yml`
=======
>>>>>>> origin/main

## Prerequisites
- PowerShell 7+ recommended
- Ability to authenticate to:
  - Microsoft Graph (for domain status)
  - Exchange Online (for DKIM)

Interactive scripts may install required modules to the current user (first run):

- `Microsoft.Graph` (only when using the Graph PowerShell login path)
- `ExchangeOnlineManagement` (only for Exchange Online / DKIM operations)

If you run with a pre-acquired Graph token (for example `GRAPH_ACCESS_TOKEN`), the scripts use
Graph REST and do not need the Graph PowerShell module.

## Authentication modes (summary)

- **Interactive (delegated) login**
  - Uses `Microsoft.Graph` PowerShell module (`Connect-MgGraph`) and delegated permissions.
  - Best for local operator use.
- **Token-based (REST)**
  - Uses a bearer token you provide as `-AccessToken` or `GRAPH_ACCESS_TOKEN`.
  - Best for automation, and avoids Graph module import quirks.
- **GitHub Actions (OIDC)**
  - Uses `azure/login@v2` (OIDC) and Azure CLI to fetch a Graph token.
  - Requires an Entra app with Graph *application* permissions.

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

## Domain onboarding preflight (read-only)

If your goal is to **add a new domain** to Microsoft 365, this repo currently focuses on
validation (not mutation). The script below is intended as a preflight checklist:

Script: `scripts/m365-domain-preflight.ps1`

What it does:

1. Identify the **active tenant** from your current auth context.
2. Check whether the domain already exists in that tenant (Graph).
3. Run the repo’s Cloudflare DNS **audit** for the domain (read-only).
4. Identify gaps (SPF/MX/DMARC/CNAME/SRV; plus DKIM selector presence).

GitHub Actions:

- Run the manual workflow `.github/workflows/7-m365-domain-preflight.yml`.
- Provide the `domain` input (for example: `example.org`).

This workflow runs as two jobs:

- **Graph checks** in `m365-prod` (uses `FFC_AZURE_CLIENT_ID` / `FFC_AZURE_TENANT_ID`)
- **Cloudflare checks** in `cloudflare-prod` (uses `CLOUDFLARE_API_KEY_DNS_ONLY`)

Notes:

- “Active tenant” means: whichever tenant your Graph token is for.
- DKIM target validation requires the domain to already exist in the tenant; otherwise we can only
  check whether the Cloudflare selector records exist.

## List tenant domains (Graph)
Script: `scripts/m365-domain-list.ps1`

Examples:
- Using an existing Graph token (recommended if you already ran `az login` + token acquisition):
  - `pwsh -File scripts/m365-domain-list.ps1 -AccessToken $env:GRAPH_ACCESS_TOKEN`
- Let the script fetch a token via Azure CLI (requires `az login`):
  - `pwsh -File scripts/m365-domain-list.ps1`

Notes:
- This uses Microsoft Graph `GET /v1.0/domains` and prints a tab-delimited list.

### GitHub Actions (non-interactive)
This repo includes a manual workflow: `.github/workflows/5-m365-domain-and-dkim.yml`.

This repo also includes a manual workflow to list all tenant domains:
- `.github/workflows/6-m365-list-domains.yml`

It is designed to run with GitHub Actions OIDC via `azure/login@v2` and then uses the Azure CLI
to fetch a Graph token.

Setup (where/how to set Environment + secrets):

- See `docs/github-actions-environments-and-secrets.md` for the exact GitHub UI paths and required
  secrets for `m365-prod`.

Required secrets (environment: `m365-prod`):
- `FFC_AZURE_CLIENT_ID`
- `FFC_AZURE_TENANT_ID`

Optional:
- `AZURE_SUBSCRIPTION_ID` (not required for these M365 workflows; they use `allow-no-subscriptions: true`)

Graph permissions:
- The Entra app referenced by `FFC_AZURE_CLIENT_ID` must have Microsoft Graph permissions to read domains.
  In practice, grant and admin-consent either:
  - Application permission `Domain.Read.All` (recommended for GitHub Actions), or
  - Application permission `Directory.Read.All`.

For DKIM (Exchange Online) app-only auth, you also need:
<<<<<<< HEAD
- `FFC_EXO_CERT_PFX_BASE64` (base64-encoded PFX containing the private key)
- `FFC_EXO_CERT_PASSWORD` (PFX password)
=======
- `EXO_TENANT` (your tenant / org, e.g. `freeforcharity.onmicrosoft.com`)
- `EXO_ORGANIZATION` (same as `EXO_TENANT` unless you use a different org string)
- `EXO_CERT_PFX_BASE64` (base64-encoded PFX)
- `EXO_CERT_PFX_PASSWORD` (optional; empty if no password)
>>>>>>> origin/main

Notes:
- This workflow uses **one Entra app**: `FFC_AZURE_CLIENT_ID` is also used as the Exchange Online
  `AppId` (no separate `EXO_APP_ID` secret).

Headless note:
- GitHub Actions runs headless because it uses OIDC (`azure/login@v2`) and Graph *application*
  permissions. There is no interactive login step.

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
<<<<<<< HEAD
- `EXO_APP_ID` (optional; in GitHub Actions, this can be set to `FFC_AZURE_CLIENT_ID`)
- `EXO_ORGANIZATION` (used as `-Organization`; in workflows we resolve this via Graph as the tenant's `*.onmicrosoft.com` domain)
- Either:
  - `EXO_CERT_THUMBPRINT` (Windows/local only, certificate already in cert store), OR
  - `EXO_CERT_PFX_BASE64` + `EXO_CERT_PFX_PASSWORD` (cross-platform)

In GitHub Actions, we use environment secrets `FFC_EXO_CERT_PFX_BASE64` and `FFC_EXO_CERT_PASSWORD` and the script reads them automatically.
=======
- `EXO_APP_ID` (in GitHub Actions, this is set to `FFC_AZURE_CLIENT_ID`)
- `EXO_TENANT` (used as `-Organization`)
- `EXO_CERT_THUMBPRINT` (certificate must already be present in the current user cert store)

In GitHub Actions, `EXO_CERT_THUMBPRINT` is set automatically after importing the PFX.
>>>>>>> origin/main

## Safety
- Scripts do not print secrets.
- `scripts/m365-dkim.ps1` supports `WhatIf` / `Confirm` for changes:
  - Example: `pwsh -File scripts/m365-dkim.ps1 -Domain example.org -Enable -WhatIf`

## Limitations (important)

- These helpers are **not** a full M365 admin tool; they focus on domain/DKIM visibility and a few
  safe, targeted operations.
- The Graph-based scripts are **read-only** (domain status, domain list, DNS record guidance).
- Graph REST results depend on permissions:
  - Delegated interactive runs require the signed-in user to have sufficient rights.
  - GitHub Actions runs require Graph *application* permissions + admin consent.
- On some systems (especially Windows PowerShell 5.1), importing `Microsoft.Graph` can be fragile.
  Use the token-based REST path (`GRAPH_ACCESS_TOKEN`) when you want maximum reliability.
- DKIM enablement is intentionally manual and gated (interactive confirmation or manual workflow
  dispatch). It should not be run unattended.
