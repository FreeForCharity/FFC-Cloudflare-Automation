# eNOM → Cloudflare Registrar Transition (Project)

## Goal
Transition all domains currently registered via **eNOM** to be registered under the **Free For Charity Cloudflare account**.

This repository already has workflows to export inventories (Cloudflare + WHMCS + WPMUDEV) and to onboard domains into Cloudflare. This project adds a repeatable way to:

- Pull the authoritative domain set from https://ffcadmin.org/sites-list/
- Classify domains into the three requested categories
- Prioritize the most critical domains first (HTTP 200 / 3xx)
- Track work via 3 epic issues (one per category)

## Category definitions

- **Category 1**: Domain is already present in Cloudflare (zone exists) but the domain registration is **not** with Cloudflare Registrar.
  - In practice for this project: `in_cloudflare=true` AND `in_whmcs=true` AND `registrar != cloudflare` (typically `enom`).
  - Action: Create a registrar transfer ticket/issue and execute the transfer to Cloudflare Registrar under the FFC account.

- **Category 2**: Domain is in WHMCS but not in Cloudflare.
  - `in_whmcs=true` AND `in_cloudflare=false`.
  - Action: Onboard the zone to Cloudflare first, then initiate the registrar transfer.

- **Category 3**: Domain is not present in WHMCS or Cloudflare.
  - `in_whmcs=false` AND `in_cloudflare=false` (but is in the authoritative list).
  - Action: Manual review.

## Priority rule
Most critical domains are those that return:

- **Live**: HTTP 200
- **Redirect**: HTTP 3xx

These should be migrated first.

## Repo rule (.com/.org pairs)
When both a `.com` and `.org` exist for the same base name (e.g., `example.com` and `example.org`), only the **`.org`** gets a GitHub repo.

- The `.com` should be a redirect to the `.org`.
- For repo naming, we use the convention `FFC-EX-<domain>` and create repos only for the `.org` apex.

## Repo provisioning

- **Single repo**: GitHub Actions: `89. Repo - Create GitHub Repo [Repo]` (or run `scripts/Create-GitHubRepo.ps1`).
- **Batch ensure** (Live/Redirect/Error from the authoritative sites list): GitHub Actions: `90. Repo - Ensure GitHub Repos [Repo]`.
   - Defaults are intentionally conservative (`DryRun=true`, `Limit=25`). Rerun with `DryRun=false` when ready.

## Recommended workflow (repeatable)

1. **Export all-source inventory** (Cloudflare + WHMCS + WPMUDEV)
   - GitHub Actions: `04. Domain - Export Inventory (All Sources) [CF+M365+WHMCS+WPMUDEV]`
   - Download artifacts: Cloudflare + WHMCS CSVs

2. **Export authoritative domain list** from FFC Admin
   - Run `scripts/ffcadmin-sites-list-domains.ps1`

3. **Run HTTP probe** to compute “critical” domains
   - Run `scripts/domain-http-probe.ps1`

4. **Build transition inventory and categories**
   - Run `scripts/enom-cloudflare-transition-inventory.ps1`

5. **Create tracking issues**
   - Create one top-level project issue + 3 category epics.
   - Optionally generate per-domain sub-issues from the inventory.

## Notes / assumptions
- This project intentionally treats the **WHMCS export** as the registrar source-of-truth (because it includes a `registrar` field).
- Subdomains (e.g., `staging.example.org`) are included in the sites list, but registrar transfers generally apply to **apex domains**. The inventory script flags subdomains so you can exclude them from registrar-transfer issue creation if desired.
