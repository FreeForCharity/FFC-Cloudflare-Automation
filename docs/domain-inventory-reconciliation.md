# Domain Inventory Reconciliation

## Overview

Free For Charity (FFC) manages domains across three primary sources of truth:

1. **Cloudflare** - DNS zones and records (the source of truth for DNS configuration)
2. **WHMCS** - Domain billing and ownership records
3. **WPMUDEV** - Hosted sites inventory

This guide explains how to reconcile these three sources to detect drift, identify misconfigurations,
and maintain a clean, consistent domain portfolio.

## Why Reconciliation Matters

### Common Drift Scenarios

Domains can fall out of sync across systems due to:

- **Manual changes**: DNS records updated in Cloudflare without updating WPMUDEV
- **Onboarding gaps**: New sites added to WPMUDEV but DNS not configured in Cloudflare
- **Billing lapses**: Domains renewed in WHMCS but not actively used in WPMUDEV or Cloudflare
- **Retirement delays**: Sites decommissioned in WPMUDEV but DNS records left active in Cloudflare
- **Migration artifacts**: Domains migrated between providers but old records not cleaned up

### Reconciliation Goals

By comparing inventories, administrators can:

- **Fix DNS issues**: Ensure all WPMUDEV sites have correct DNS in Cloudflare
- **Audit billing**: Verify WHMCS domains align with active usage in Cloudflare/WPMUDEV
- **Clean up orphans**: Identify and retire unused domains or zones
- **Plan capacity**: Understand the full scope of domains across all systems
- **Improve security**: Remove stale DNS records that could be hijacked

## Prerequisites

Before reconciling, export inventories from all three sources:

### 1. Cloudflare Export

**Workflow**: `06. DNS - Export All Domains (Report)` (`.github/workflows/4-export-summary.yml`)

**How to run:**

1. Actions → **06. DNS - Export All Domains (Report)**
2. Click **Run workflow**
3. Download artifact: `domain_summary`

**Output**: `domain_summary.csv`

**Key columns:**

- `zone` (domain name)
- `apex_a_ips` (semicolon-separated A record IPs)
- `www_cname_target` (CNAME target for www subdomain)
- `m365_compliant` (whether MX points to outlook.com)

### 2. WHMCS Export

**Workflow**: `07. WHMCS - Export Domains (Report)` (`.github/workflows/7-whmcs-export-domains.yml`)

**How to run:**

1. Actions → **07. WHMCS - Export Domains (Report)**
2. Click **Run workflow**
3. Download artifact: `whmcs_domains`

**Output**: `whmcs_domains.csv`

**Key columns:**

- `domain` (domain name)
- `status` (e.g., Active, Expired)
- `registrationdate`
- `expirydate`
- Additional billing/status fields

### 3. WPMUDEV Export

**Workflow**: `13. WPMUDEV - Export Sites/Domains (Read-only)`
(`.github/workflows/13-wpmudev-export-sites.yml`)

**How to run:**

1. Actions → **13. WPMUDEV - Export Sites/Domains (Read-only)**
2. Click **Run workflow**
3. Download artifact: `wpmudev-domain-inventory`

**Output**: `wpmudev_domains.csv`

**Key columns:**

- `domain` (domain name, already normalized to lowercase)
- `sitesCount` (number of sites on this domain)
- `siteNames` (semicolon+space separated site names)
- `homeUrls` (semicolon+space separated URLs)

## Domain Normalization Rules

To compare domains across systems, normalize them first:

### Rule 1: Lowercase Conversion

Convert all domains to lowercase:

- `Example.ORG` → `example.org`
- `STAGING.EXAMPLE.COM` → `staging.example.com`

**PowerShell example:**

```powershell
$normalized = $domain.ToLowerInvariant()
```

**Excel/Sheets:**

```
=LOWER(A2)
```

### Rule 2: Trim Trailing Dots

Remove trailing dots (common in DNS zone files):

- `example.org.` → `example.org`

**PowerShell example:**

```powershell
$normalized = $domain.TrimEnd('.')
```

**Excel/Sheets:**

```
=SUBSTITUTE(A2,".","")
```

_(Note: This removes all dots, not just trailing. For trailing only, use:
`=IF(RIGHT(A2,1)=".", LEFT(A2, LEN(A2)-1), A2)`)_

### Rule 3: Handle www Subdomains (Optional)

For some comparisons, you may want to treat `www.example.org` and `example.org` as equivalent:

- Strip `www.` prefix before comparing
- Or: Keep as-is and compare both apex and www separately

**Recommendation**: Keep www as-is for most reconciliation; only strip for specific "is this domain
in the inventory at all?" checks.

### Rule 4: Ignore Path Fragments

WPMUDEV's `homeUrls` may include paths (e.g., `example.com/subsite`). For domain matching:

- Extract only the hostname: `example.com`
- Ignore `/subsite` paths

**PowerShell example:**

```powershell
$uri = [Uri]$homeUrl
$hostname = $uri.Host.ToLowerInvariant()
```

**Note**: The WPMUDEV export script already does this for the `domain` column.

## Comparison Table Schema

Create a comparison table (in Excel, Google Sheets, or PowerShell) with these columns:

| Column             | Description                                                |
| ------------------ | ---------------------------------------------------------- |
| `domain`           | Normalized domain name (lowercase, no trailing dot)       |
| `in_cloudflare`    | `TRUE` if domain exists in Cloudflare export, else `FALSE` |
| `in_whmcs`         | `TRUE` if domain exists in WHMCS export, else `FALSE`      |
| `in_wpmudev`       | `TRUE` if domain exists in WPMUDEV export, else `FALSE`    |
| `cloudflare_ips`   | Apex A IPs from Cloudflare (if present)                   |
| `whmcs_status`     | Status from WHMCS (e.g., Active, Expired)                 |
| `wpmudev_sites`    | Number of sites from WPMUDEV (if present)                 |
| `mismatch_pattern` | Short code describing the mismatch (see below)            |
| `action_needed`    | Suggested next step                                       |

## Triage Playbook: Mismatch Patterns

### Pattern 1: In WPMUDEV, Missing in Cloudflare

**Symptoms:**

- `in_wpmudev=TRUE`
- `in_cloudflare=FALSE`

**Likely Cause:**

- Site hosted on WPMUDEV but DNS not configured
- Domain recently added to WPMUDEV, onboarding incomplete

**Action:**

1. Run workflow: **01. Domain - Status (Check)** with the domain
2. Review the status output to confirm DNS is missing
3. Run workflow: **02. Domain - Enforce Standard (Fix)** in dry-run mode to see what would change
4. If output looks correct, re-run with `dry_run=false` to apply DNS configuration
5. Verify the site is accessible via the domain

**Outcome:** Domain added to Cloudflare with standard DNS records.

### Pattern 2: In WPMUDEV, Incorrect in Cloudflare

**Symptoms:**

- `in_wpmudev=TRUE`
- `in_cloudflare=TRUE`
- `cloudflare_ips` does not match expected GitHub Pages IPs or WPMUDEV hosting IPs

**Likely Cause:**

- DNS records point to old hosting provider
- Manual DNS changes not aligned with WPMUDEV hosting

**Action:**

1. Run workflow: **01. Domain - Status (Check)** to see current DNS
2. Compare `apex_a_ips` with expected values:
   - **GitHub Pages**: `185.199.108.153`, `185.199.109.153`, `185.199.110.153`,
     `185.199.111.153`
   - **WPMUDEV hosting**: Check WPMUDEV documentation for current IPs
3. Run workflow: **02. Domain - Enforce Standard (Fix)** to correct DNS
4. Verify the site is accessible after DNS propagation

**Outcome:** Cloudflare DNS updated to match WPMUDEV hosting requirements.

### Pattern 3: In WHMCS, Not in Cloudflare or WPMUDEV

**Symptoms:**

- `in_whmcs=TRUE`
- `in_cloudflare=FALSE`
- `in_wpmudev=FALSE`

**Likely Cause:**

- Domain purchased but not yet onboarded
- Domain used for email-only (no website)
- Domain parked or reserved for future use

**Action:**

1. Check WHMCS for domain purpose/notes
2. If domain should be used, create a GitHub issue to onboard it:
   - Use issue template: **Add Existing Domain to Cloudflare**
   - Follow onboarding workflow
3. If domain is not needed, consider:
   - **Letting it expire** (update WHMCS notes)
   - **Retiring it** (transfer or delete)

**Outcome:** Domain either onboarded or marked for retirement.

### Pattern 4: In Cloudflare, Not in WHMCS or WPMUDEV

**Symptoms:**

- `in_cloudflare=TRUE`
- `in_whmcs=FALSE`
- `in_wpmudev=FALSE`

**Likely Cause:**

- **Orphaned zone**: Domain previously used, now abandoned
- **External ownership**: Domain registered outside WHMCS (e.g., donated, third-party)
- **Subdomain confusion**: Entry is a subdomain, not a root domain (check for `.`)

**Action:**

1. Investigate domain ownership:
   - Check who created the Cloudflare zone (audit logs)
   - Search WHMCS for similar domains (e.g., different TLD)
2. If domain is truly orphaned:
   - Create a GitHub issue to **Remove Domain from Cloudflare**
   - Document the decision
3. If domain is owned externally but still needed:
   - Add to WHMCS with status **External** (if possible)
   - Document in repository notes

**Outcome:** Zone removed from Cloudflare or ownership clarified.

### Pattern 5: In Cloudflare and WHMCS, Not in WPMUDEV

**Symptoms:**

- `in_cloudflare=TRUE`
- `in_whmcs=TRUE`
- `in_wpmudev=FALSE`

**Likely Cause:**

- Domain registered and DNS configured, but no site hosted yet
- Domain used for **email-only** (M365)
- Domain for **external services** (e.g., third-party app)

**Action:**

1. Check Cloudflare DNS records for clues:
   - **MX records** → Email-only domain (expected)
   - **A/CNAME to external IPs** → External service (document purpose)
   - **No A/AAAA records** → Incomplete setup
2. If domain should have a WPMUDEV site:
   - Create site in WPMUDEV
   - Update DNS if needed
3. If domain is email-only or external:
   - **No action needed** (this is expected)
   - Document the purpose in repository notes or WHMCS

**Outcome:** Domain purpose clarified; no changes needed if intentional.

### Pattern 6: All Three Sources Match

**Symptoms:**

- `in_cloudflare=TRUE`
- `in_whmcs=TRUE`
- `in_wpmudev=TRUE`

**Action:**

- **No action needed** ✅
- This is the ideal state

**Outcome:** Domain is correctly configured across all systems.

## Reconciliation Workflow (Step-by-Step)

### Step 1: Export All Inventories

Run the three export workflows (see [Prerequisites](#prerequisites)) and download the CSV files.

### Step 2: Normalize Domains

In Excel, Google Sheets, or PowerShell:

1. Create a new sheet/table named `Reconciliation`
2. Import all three CSVs
3. Create a `normalized_domain` column that applies:
   - Lowercase conversion
   - Trailing dot trimming
   - (Optional) www stripping

**PowerShell example:**

```powershell
$cloudflare = Import-Csv 'domain_summary.csv'
$whmcs = Import-Csv 'whmcs_domains.csv'
$wpmudev = Import-Csv 'wpmudev_domains.csv'

# Normalize function
function Normalize-Domain($domain) {
    if ([string]::IsNullOrWhiteSpace($domain)) { return '' }
    $domain.Trim().TrimEnd('.').ToLowerInvariant()
}

# Get all unique domains
$allDomains = @(
    ($cloudflare | ForEach-Object { Normalize-Domain $_.zone })
    ($whmcs | ForEach-Object { Normalize-Domain $_.domain })
    ($wpmudev | ForEach-Object { Normalize-Domain $_.domain })
) | Where-Object { $_ } | Select-Object -Unique | Sort-Object
```

### Step 3: Build Comparison Table

For each unique domain, check presence in each source:

**PowerShell example:**

```powershell
$reconciliation = foreach ($domain in $allDomains) {
    $cfRow = $cloudflare | Where-Object { (Normalize-Domain $_.zone) -eq $domain } | Select-Object -First 1
    $whRow = $whmcs | Where-Object { (Normalize-Domain $_.domain) -eq $domain } | Select-Object -First 1
    $wpRow = $wpmudev | Where-Object { (Normalize-Domain $_.domain) -eq $domain } | Select-Object -First 1

    [PSCustomObject]@{
        domain           = $domain
        in_cloudflare    = ($null -ne $cfRow)
        in_whmcs         = ($null -ne $whRow)
        in_wpmudev       = ($null -ne $wpRow)
        cloudflare_ips   = if ($cfRow) { $cfRow.apex_a_ips } else { '' }
        whmcs_status     = if ($whRow) { $whRow.status } else { '' }
        wpmudev_sites    = if ($wpRow) { $wpRow.sitesCount } else { 0 }
    }
}

$reconciliation | Export-Csv -Path 'domain_reconciliation.csv' -NoTypeInformation
```

### Step 4: Identify Mismatches

Filter for rows where the three sources don't all match:

**PowerShell example:**

```powershell
$mismatches = $reconciliation | Where-Object {
    -not ($_.in_cloudflare -and $_.in_whmcs -and $_.in_wpmudev)
}
```

**Excel filter:**

- Add a helper column: `=IF(AND(B2,C2,D2), "Match", "Mismatch")`
- Filter to show only `"Mismatch"` rows

### Step 5: Classify and Triage

For each mismatch, determine the pattern and assign an action using the
[Triage Playbook](#triage-playbook-mismatch-patterns) above.

### Step 6: Create GitHub Issues

For each domain requiring action:

1. Create a GitHub issue using the appropriate template:
   - **Configure Apex Domain for GitHub Pages** (for missing DNS)
   - **Add Existing Domain to Cloudflare** (for WHMCS-only domains)
   - **Remove Domain from Cloudflare** (for orphaned zones)
2. Assign to the appropriate administrator
3. Link the issue to the reconciliation table (add issue number to a column)

### Step 7: Execute and Verify

1. Run the appropriate workflows to fix each issue
2. Re-export inventories after fixes
3. Re-run reconciliation to verify the mismatch is resolved

## Automation Ideas (Future Enhancements)

- **Scheduled reconciliation**: Run exports on a schedule (e.g., weekly) and auto-generate a summary
- **Slack/email alerts**: Notify administrators when new mismatches are detected
- **Auto-fix for common patterns**: For Pattern 1 (WPMUDEV missing Cloudflare), auto-run the
  domain-status workflow
- **Dashboard**: Build a web dashboard to visualize reconciliation status

## See Also

- [WPMUDEV Domain Inventory](wpmudev-domain-inventory.md) - How to run the WPMUDEV export
- [Enforce Standard Workflow](enforce-standard-workflow.md) - How to fix DNS configuration
- [GitHub Actions Environments and Secrets](github-actions-environments-and-secrets.md) - Required
  setup
- [Workflow README](../.github/workflows/README.md) - Full workflow catalog
