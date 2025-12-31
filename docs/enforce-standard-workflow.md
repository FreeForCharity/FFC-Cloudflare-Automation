# Enforce Standard Workflow

This repository includes a GitHub Actions workflow that checks (and optionally enforces) a baseline DNS configuration for a zone in Cloudflare.

- Workflow: `.github/workflows/2-enforce-standard.yml`
- Script: `Update-CloudflareDns.ps1`

## How it runs

The workflow is triggered manually (`workflow_dispatch`) with two inputs:

- `domain` (required): the Cloudflare zone name, e.g. `example.org`
- `dry_run` (boolean): when `true`, the workflow prints proposed changes but does not write to Cloudflare

The workflow performs two steps:

1. **Enforce FFC Standard Configuration**
2. **Post-Enforce Compliance Audit**

## What “standard” means

The standard set is enforced by `Update-CloudflareDns.ps1 -EnforceStandard`.

### Microsoft 365

- Apex MX (`@`) for M365, priority `0`
- Apex TXT (`@`) SPF including `include:spf.protection.outlook.com`
- DMARC TXT (`_dmarc`) starting with `v=DMARC1`

Additional required records:

- CNAME `autodiscover` → `autodiscover.outlook.com` (DNS only)
- CNAME `enterpriseenrollment` → `enterpriseenrollment-s.manage.microsoft.com` (DNS only)
- CNAME `enterpriseregistration` → `enterpriseregistration.windows.net` (DNS only)
- CNAME `lyncdiscover` → `webdir.online.lync.com` (DNS only)
- CNAME `sip` → `sipdir.online.lync.com` (DNS only)
- SRV `_sip._tls` → `100 1 443 sipdir.online.lync.com`
- SRV `_sipfederationtls._tcp` → `100 1 5061 sipfed.online.lync.com`

Note: Cloudflare recommends TXT record content be wrapped in quotation marks. The script enforces quoted TXT content (and for SPF, it preserves existing mechanisms while ensuring quoting) to avoid Cloudflare UI warnings.

DMARC monitoring note:

- If Cloudflare DMARC Management is enabled for a zone, Cloudflare may add a per-zone `rua` recipient at `@dmarc-reports.cloudflare.net`.
- The script preserves any existing Cloudflare `rua` recipients and always ensures the internal recipient `mailto:dmarc-rua@freeforcharity.org` is also present.
Note: for M365 MX, the expected target is computed as `<zone-with-dashes>.mail.protection.outlook.com`.

### GitHub Pages (apex)

The workflow requires the official GitHub Pages IPs.

IPv4 (A):

- `185.199.108.153`
- `185.199.109.153`
- `185.199.110.153`
- `185.199.111.153`

IPv6 (AAAA):

- `2606:50c0:8000::153`
- `2606:50c0:8001::153`
- `2606:50c0:8002::153`
- `2606:50c0:8003::153`

### GitHub Pages (www)

- `www.<zone>` CNAME to `freeforcharity.github.io`

## Audit vs Enforce

- **Audit** (`-Audit`) only reports: it prints `[OK]` or `[MISSING]`/`[MISSING/PARTIAL]` messages.
- **Enforce** (`-EnforceStandard`) proposes or applies changes:
  - `dry_run: true` prints `[DRY-RUN] POST ...` / `[DRY-RUN] PUT ...`
  - `dry_run: false` writes changes to Cloudflare

## Recent behavior changes

The Enforce logic relies on a complete inventory of the zone’s DNS records so it can correctly decide whether a record already exists.

It now loads the full record set before checking the standard list, preventing false "[MISSING]" results caused by comparing against an empty record list.

## Change process (issue → PR)

When changing enforcement requirements, prefer creating an issue first and then opening a PR that references it.

Example: the GitHub Pages AAAA requirement should have been tracked as an issue before implementation (see https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues/40).
