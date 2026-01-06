# WPMUDEV Domain Inventory

## Overview

The WPMUDEV domain inventory workflow provides a read-only export of all sites and domains hosted on
WPMUDEV's managed hosting platform. This inventory is one of three primary domain management sources
for Free For Charity (FFC), alongside Cloudflare (DNS/zones) and WHMCS (billing/owned domains).

### What this workflow does

The WPMUDEV export workflow:

1. **Authenticates** with the WPMUDEV Hub API using a read-only API token
2. **Fetches** all sites from the WPMUDEV Hub via paginated API calls
3. **Aggregates** sites by domain (one row per domain, with multiple sites listed if applicable)
4. **Exports** to CSV with standardized schema for cross-platform comparison
5. **Uploads** the CSV as a GitHub Actions artifact for download and analysis

### Why this matters

WPMUDEV hosts production sites for FFC's charitable partners. This inventory helps administrators:

- **Detect drift**: Identify domains in WPMUDEV that are missing or misconfigured in Cloudflare DNS
- **Audit hosting**: Verify which domains are actively hosted vs. only registered
- **Reconcile billing**: Cross-reference WPMUDEV sites with WHMCS domain ownership records
- **Plan migrations**: Understand the full scope of hosted sites before infrastructure changes

## Required Setup

### GitHub Actions Environment: `wpmudev-prod`

1. In GitHub, navigate to **Settings** → **Environments**
2. Create a new environment named exactly: `wpmudev-prod` (case-sensitive)
3. **(Recommended)** Add **Required reviewers** to gate workflow execution
4. Add the required **Environment secret** (see below)

### Required Secret: `FFC_WPMUDEV_GA_API_Token`

This secret stores the WPMUDEV Hub API token.

**How to create the secret:**

1. In the `wpmudev-prod` environment (or Repository secrets if not using environments)
2. Click **Add secret**
3. Name: `FFC_WPMUDEV_GA_API_Token`
4. Value: Your WPMUDEV Hub API token

**How to obtain a WPMUDEV API token:**

1. Log in to [WPMUDEV](https://wpmudev.com/)
2. Navigate to **Hub** → **API**
3. Generate a new API key or use an existing one
4. The token must have at least **read access** to sites/domains

**Security notes:**

- The token is **read-only** for this workflow; no changes are made to WPMUDEV
- The workflow **never logs** the token value (only success/failure status)
- The token is passed via environment variable `WPMUDEV_API_TOKEN` to the PowerShell script

**Permissions required:**

The API token needs:

- Read access to the WPMUDEV Hub API endpoints:
  - `GET /api/hub/v1/account` (diagnostics check)
  - `GET /api/hub/v1/sites` (site listing, paginated)

## How to Run the Workflow

### Step 1: Navigate to Actions

1. Go to the repository **Actions** tab
2. Select the workflow: **40. WPMUDEV - Export Sites/Domains (Read-only)**
3. Click **Run workflow** (requires appropriate permissions)

### Step 2: Configure Inputs

The workflow accepts one optional input:

- **`output_file`**: CSV output filename
  - Default: `wpmudev_domains.csv`
  - Example: `wpmudev_inventory_2026-01-05.csv`

### Step 3: Run and Monitor

1. Click **Run workflow**
2. Wait for the workflow to complete (typically 1-2 minutes)
3. Review the workflow logs for:
   - **WPMUDEV Diagnostics** section: confirms API authentication
   - **WPMUDEV Summary** section: prints domain counts

### Step 4: Download the Artifact

After the workflow completes successfully:

1. Scroll to the bottom of the workflow run page
2. Find **Artifacts** section
3. Download: `wpmudev-domain-inventory` (contains the CSV file)
4. Unzip and open the CSV in Excel, Google Sheets, or a text editor

## CSV Schema

The exported CSV contains one row per unique domain, with the following columns:

| Column       | Type    | Description                                                                     |
| ------------ | ------- | ------------------------------------------------------------------------------- |
| `domain`     | string  | Normalized domain (lowercase, extracted from `home_url` or `domain` API field)  |
| `siteIds`    | string  | Semicolon-separated list of site IDs for this domain                            |
| `siteNames`  | string  | Semicolon+space separated list of site names/titles (may be empty if no title)  |
| `homeUrls`   | string  | Semicolon+space separated list of home URLs (may include path fragments)        |
| `sitesCount` | integer | Number of sites associated with this domain                                     |
| `source`     | string  | Always `wpmudev` (for cross-source comparison)                                  |
| `fetchedUtc` | string  | ISO-8601 UTC timestamp when the data was fetched (e.g., `2026-01-05T12:34:56Z`) |

### Example CSV rows

```csv
domain,siteIds,siteNames,homeUrls,sitesCount,source,fetchedUtc
example.org,12345,Example Site,https://example.org,1,wpmudev,2026-01-05T12:34:56Z
staging.example.org,67890,Example Staging,https://staging.example.org,1,wpmudev,2026-01-05T12:34:56Z
multi.example.com,11111;22222,Site A; Site B,https://multi.example.com; https://multi.example.com/siteB,2,wpmudev,2026-01-05T12:34:56Z
```

### Important Notes

- **Domain normalization**: Domains are converted to lowercase for consistent comparison
- **Path fragments**: Some `homeUrls` may include paths (e.g., `example.com/subsite`). The `domain`
  field extracts only the hostname for cleaner reconciliation.
- **Empty site names**: If the WPMUDEV API response does not include a `title`, `name`, or similar
  field, `siteNames` will be empty
- **Multiple sites per domain**: If a single domain hosts multiple WPMUDEV sites, they are
  aggregated into one row with semicolon-separated values

## Workflow Behavior Details

### Diagnostics Step

Before fetching sites, the workflow performs a diagnostic check:

```
GET https://wpmudev.com/api/hub/v1/account
```

This validates:

- The API token is valid
- The WPMUDEV API is reachable
- Authentication headers are correct

**Expected output:**

```
Diagnostics: success
```

**If authentication fails:**

```
Diagnostics: request failed with HTTP 401
Diagnostics: Unauthorized
```

See [Troubleshooting](#troubleshooting) below.

### Export Step

The export script (`scripts/wpmudev-sites-export.ps1`) performs the following:

1. **Pagination**: Fetches sites in pages of 100 (configurable via `-PerPage` parameter)
2. **Page detection**: Reads `X-WP-TotalPages` header to determine total pages
3. **Fallback**: If `X-WP-TotalPages` is missing, continues fetching until a page returns fewer than
   100 sites
4. **Header variants**: Tries multiple authentication header formats to ensure compatibility:
   - `AUTHORIZATION: <token>`
   - `AUTHORIZATION: Bearer <token>`
   - `Authorization: <token>`
   - `Authorization: Bearer <token>`
   - `X-Api-Key: <token>`
   - `X-WPMUDEV-API-Key: <token>`

### Summary Step

After the CSV is created, the workflow prints:

```
Summary: domains=42; domainsWithMultipleSites=3
```

This helps quickly assess:

- **domains**: Total unique domains exported
- **domainsWithMultipleSites**: How many domains host multiple WPMUDEV sites (potential complexity)

## Troubleshooting

### Error: `auth_required` or HTTP 401

**Cause**: The API token is invalid or missing.

**Fix:**

1. Verify the `FFC_WPMUDEV_GA_API_Token` secret exists in the `wpmudev-prod` environment
2. Check that the token has not expired or been revoked in WPMUDEV
3. Regenerate the token in WPMUDEV Hub → API and update the secret

### Error: `Request failed (403) Forbidden`

**Cause**: The API token lacks sufficient permissions.

**Fix:**

1. Verify the token has **read access** to the Hub API
2. Regenerate the token with appropriate permissions

### Error: `output file not found`

**Cause**: The export script failed before creating the CSV.

**Fix:**

1. Check the workflow logs for PowerShell errors
2. Look for error messages in the export script output
3. Verify the `scripts/wpmudev-sites-export.ps1` file exists in the repository

### Pagination Issues: Incomplete Results

**Symptoms**: The CSV has fewer domains than expected.

**Cause**: Pagination logic may have stopped early due to missing or malformed `X-WP-TotalPages`
header.

**Fix:**

1. Review the workflow logs for pagination details
2. Re-run the workflow to see if the issue persists
3. If consistent, reduce `-PerPage` to `50` in `scripts/wpmudev-sites-export.ps1` (line 14) and
   re-run

### Empty Site Names

**Symptoms**: The `siteNames` column is empty or has missing values.

**Cause**: The WPMUDEV API response may not include a consistent `title` or `name` field for all
sites.

**Fix:**

- This is expected behavior; not all sites have a title set in WPMUDEV
- Use the `homeUrls` column as a fallback identifier

## Security Considerations

- **Read-only operation**: This workflow does **not** modify WPMUDEV sites or configuration
- **No token logging**: The workflow explicitly avoids printing the API token in logs
- **Environment gating**: Use `wpmudev-prod` environment with **Required reviewers** to prevent
  unauthorized runs
- **Token rotation**: Rotate the WPMUDEV API token periodically (e.g., every 90 days)

## Integration with Other Workflows

The WPMUDEV inventory is designed to complement:

- **Cloudflare exports** (workflow 06): DNS zone and record summaries
- **WHMCS exports** (workflow 07): Billing and domain ownership records

For cross-source reconciliation, see
[docs/domain-inventory-reconciliation.md](domain-inventory-reconciliation.md).

## See Also

- [Domain Inventory Reconciliation](domain-inventory-reconciliation.md) - Cross-source drift
  detection
- [GitHub Actions Environments and Secrets](github-actions-environments-and-secrets.md) - How to
  configure `wpmudev-prod`
- [Workflow README](../.github/workflows/README.md) - Full workflow catalog
