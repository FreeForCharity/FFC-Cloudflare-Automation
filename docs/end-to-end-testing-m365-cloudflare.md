# End-to-end testing: Microsoft 365 + Cloudflare alignment

This document describes the **end-to-end** process we use to prove a domain is aligned in both:

- **Cloudflare DNS** (records exist and match repo standards + Microsoft requirements)
- **Microsoft 365 / Exchange Online** (domain present/verified + DKIM enabled)
- **Microsoft Defender DKIM v2 UI** (shows **Valid** and toggle **Enabled**)

The example domain used throughout is `ffcadmin.org`, but the process is the same for any domain.

## What “end-to-end” means (acceptance criteria)

After completing this run for a domain:

### Cloudflare side

- The zone passes the repo DNS audit (`Update-CloudflareDns.ps1 -Audit`) with no `[MISSING]` /
  `[DIFFERS]` items related to Microsoft 365.
- The zone includes the two Exchange Online DKIM selector CNAME records:
  - `selector1._domainkey.<domain>`
  - `selector2._domainkey.<domain>`
- Public DNS resolves those selector CNAMEs correctly.

### Microsoft 365 side

- In Microsoft Graph domain status, the domain exists in the tenant and is verified.
- In Exchange Online, DKIM signing is enabled for the domain.

### Microsoft Defender side

- In `https://security.microsoft.com/dkimv2`, the domain shows:
  - **Status**: `Valid`
  - Toggle: `Enabled`

## Required prerequisites

### Access / permissions

- Cloudflare API token with DNS edit permissions for the zone (GitHub Actions environment:
  `cloudflare-prod` / secret: `CLOUDFLARE_API_KEY_DNS_ONLY`).
- Microsoft Entra app configured for app-only Exchange Online:
  - Graph application permission: `Domain.Read.All` (or `Directory.Read.All`) admin-consented
  - Exchange Online application role: `Exchange.ManageAsApp` admin-consented
  - Certificate credential configured for the app

### GitHub Actions environments/secrets

This repo’s “protected environments” are expected to hold the real credentials:

- Environment: `m365-prod`
  - `FFC_AZURE_CLIENT_ID`
  - `FFC_AZURE_TENANT_ID`
  - `FFC_EXO_CERT_PFX_BASE64`
  - `FFC_EXO_CERT_PASSWORD`
- Environment: `cloudflare-prod`
  - `CLOUDFLARE_API_KEY_DNS_ONLY`

## Workflow run order (recommended)

This order is designed to (1) establish Cloudflare baselines, (2) confirm M365 status, (3) apply
DKIM, and (4) validate.

### 1) Cloudflare baseline audit (read-only)

- Workflow: `[DNS] 1. Report - Check Compliance` (`.github/workflows/1-audit-compliance.yml`)
- Input: `domain = ffcadmin.org`

Expected outcome:

- You get a full audit report, including the M365/Teams/Intune record checks.

### 2) Enforce Cloudflare standard DNS (dry-run, then live)

- Workflow: `[DNS] 2. Fix - Enforce Standard` (`.github/workflows/2-enforce-standard.yml`)
- Inputs:
  - `domain = ffcadmin.org`
  - `dry_run = true` (preview)
  - then re-run with `dry_run = false` (apply)

Important:

- This step **does not** create DKIM selector records. DKIM is handled by the DKIM workflow (step
  5).

### 3) Add Microsoft domain verification TXT (only if needed)

If the domain is not verified in the tenant yet, Microsoft will provide a verification DNS record
(typically a TXT).

To discover the exact TXT record Microsoft expects:

- Workflow: `[M365] Domain Status + DKIM` (`.github/workflows/5-m365-domain-and-dkim.yml`)
- Inputs:
  - `domain = ffcadmin.org`
  - `action = status`

Then apply the verification TXT record in Cloudflare using:

- Workflow: `[DNS] 3. Manual - Manage Record` (`.github/workflows/3-manage-record.yml`)
- Inputs (example — use the exact values shown by the status workflow):
  - `domain = ffcadmin.org`
  - `dry_run = false`
  - `record_type = TXT`
  - `record_name = @`
  - `record_content = <value from Microsoft>`

Then complete the verification in the Microsoft 365 admin UI.

### 4) Read-only M365 + Cloudflare preflight

- Workflow: `M365: Domain preflight (read-only)` (`.github/workflows/7-m365-domain-preflight.yml`)
- Input: `domain = ffcadmin.org`

Expected outcome:

- Confirms the domain exists in the tenant and reports whether Cloudflare has the DKIM selector
  names present.

### 5) End-to-end DKIM enablement (creates DKIM, sets Cloudflare DKIM CNAMEs, enables EXO DKIM)

- Workflow: `M365: Enable DKIM (Exchange Online)` (`.github/workflows/8-m365-dkim-enable.yml`)
- Input: `domain = ffcadmin.org`

This workflow performs three protected steps:

1. Exchange Online: ensure DKIM config exists and fetch selector targets
2. Cloudflare: upsert selector CNAMEs to the targets from step 1
3. Exchange Online: enable DKIM signing

### 6) Validation (repeatable)

Run these after DKIM enablement:

- `[DNS] 1. Report - Check Compliance` (confirms baseline DNS)
- `M365: Domain preflight (read-only)` (confirms selector presence)

Then validate in the UI:

- `https://security.microsoft.com/dkimv2`

Note on propagation:

- Public DNS updates are usually quick, but the Defender DKIM UI can lag. Expect anywhere from
  minutes to a few hours.

## Expected records after the test

This section summarizes the _final state_ you should see for a domain after the complete run.

### Cloudflare DNS (expected)

From the repo’s standard enforcement (`Update-CloudflareDns.ps1 -EnforceStandard`):

- MX (apex): `@` → `<domain-with-dashes>.mail.protection.outlook.com` (priority `0`)
- TXT (apex): SPF contains `include:spf.protection.outlook.com` (quoted)
- TXT: `_dmarc` starts with `v=DMARC1` (quoted) and includes `mailto:dmarc-rua@freeforcharity.org`
- CNAME (DNS only):
  - `autodiscover` → `autodiscover.outlook.com`
  - `enterpriseenrollment` → `enterpriseenrollment-s.manage.microsoft.com`
  - `enterpriseregistration` → `enterpriseregistration.windows.net`
  - `lyncdiscover` → `webdir.online.lync.com`
  - `sip` → `sipdir.online.lync.com`
- SRV:
  - `_sip._tls` → `100 1 443 sipdir.online.lync.com`
  - `_sipfederationtls._tcp` → `100 1 5061 sipfed.online.lync.com`

From DKIM enablement (`.github/workflows/8-m365-dkim-enable.yml`):

- CNAME: `selector1._domainkey` → **Microsoft-provided target**
- CNAME: `selector2._domainkey` → **Microsoft-provided target**

Microsoft’s DKIM selector targets vary by tenant/region, but commonly look like:

- `selector1-<domain-with-dashes>._domainkey.<tenant>.<region>.dkim.mail.microsoft`
- `selector2-<domain-with-dashes>._domainkey.<tenant>.<region>.dkim.mail.microsoft`

Additionally (only if the domain was newly added):

- TXT (verification): `@` → value provided by Microsoft (for example `MS=ms########`)

### Microsoft 365 / Exchange Online (expected)

- The domain exists as an accepted domain.
- DKIM signing config exists for the domain.
- DKIM is enabled (authoritative check):
  - `Get-DkimSigningConfig -Identity <domain>` → `Enabled : True`

### Microsoft Defender DKIM v2 UI (expected)

In `https://security.microsoft.com/dkimv2`:

- Status: `Valid`
- Toggle: `Enabled`

## Troubleshooting notes

- If the DKIM enable workflow fails in the first job (`exo_check`), it’s almost always EXO
  auth/cert/app permissions.
- If Cloudflare shows the DKIM records but Defender still shows `CnameMissing`, it’s usually
  propagation/caching; confirm via a public DNS lookup of both selector records.
