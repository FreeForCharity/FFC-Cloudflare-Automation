# FFC Sites List

Canonical, machine-generated inventory of Free For Charity domains.

## Files

| File              | Purpose                                                             |
| ----------------- | ------------------------------------------------------------------- |
| `sites_list.csv`  | Human / spreadsheet friendly; line-based diffs (one row per domain) |
| `sites_list.json` | Structured form for programmatic consumers                          |

Both files contain the same records and columns:
`Section, Domain, Status, In WHMCS, In Cloudflare, In WPMUDEV, Server In Use, Old Server Abandoned?, Notes, Cloudflare IP, Is In Cloudflare, Repo URL, Site Health, Priority, Repo Archived, Last PR Closed, Open PRs, Last Commit, Dev Status, Left FFC, Host Category, Is Staging, Domain Age, Expiry, Recurring, Work Tier, Migration Score, Maintenance Score, Dev Score`.

The later columns are derived enrichment: GitHub dev-activity (`Repo Archived` … `Dev Status`),
`Left FFC` / `Work Tier` triage, host/age/cost signals (`Host Category`, `Is Staging`, `Domain Age`,
`Expiry`, `Recurring`), and the volunteer-persona priority scores (`Migration`, `Maintenance`,
`Dev`).

## How it is generated

`.github/workflows/sites-list-generate.yml` (weekly + manual) dispatches the read-only export
workflows — WHMCS (`7-whmcs-export-domains.yml`), Cloudflare DNS (`4-export-summary.yml`), WPMUDEV
(`13-wpmudev-export-sites.yml`) — merges their artifacts with the curated base list via
`scripts/update-sites-data.mjs`, runs per-domain HTTPS health checks, and commits the regenerated
files.

Curated columns (`Section`, `Server In Use`, `Notes`, `Priority`, …) are preserved from the existing
`sites_list.csv`; edit them there. If an export is unavailable, the matching membership flags are
preserved rather than wiped.

## Consuming from other repos

These files are public; pull them without credentials, e.g.:

```
https://raw.githubusercontent.com/FreeForCharity/FFC-Cloudflare-Automation/main/sites-list/sites_list.json
https://raw.githubusercontent.com/FreeForCharity/FFC-Cloudflare-Automation/main/sites-list/sites_list.csv
```
