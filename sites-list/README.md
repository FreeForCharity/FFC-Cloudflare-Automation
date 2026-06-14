# FFC Sites List

Canonical, machine-generated inventory of Free For Charity domains.

## Files

| File              | Purpose                                                             |
| ----------------- | ------------------------------------------------------------------- |
| `sites_list.csv`  | Human / spreadsheet friendly; line-based diffs (one row per domain) |
| `sites_list.json` | Structured form for programmatic consumers                          |

Both files contain the same records and columns:
`Section, Domain, Status, In WHMCS, In Cloudflare, In WPMUDEV, Server In Use, Old Server Abandoned?, Notes, Cloudflare IP, Is In Cloudflare, Repo URL, Site Health, Priority, Repo Archived, Last PR Closed, Open PRs, Last Commit, Dev Status, Left FFC, Host Category, Is Staging, Domain Age, Expiry, Recurring, Work Tier, Migration Score, Maintenance Score, Dev Score`.

The later columns are derived enrichment: GitHub dev-activity, Left FFC / Work Tier triage,
host/age/cost signals, and the volunteer-persona priority scores (Migration, Maintenance, Dev).

## How it is generated

`.github/workflows/sites-list-generate.yml` (weekly + manual) dispatches the read-only export
workflows — WHMCS (`7-whmcs-export-domains.yml`), Cloudflare DNS (`4-export-summary.yml`), WPMUDEV
(`13-wpmudev-export-sites.yml`) — merges their artifacts with the curated base list via
`scripts/update-sites-data.mjs`, runs per-domain HTTPS health checks, and commits the regenerated
files.

Curated columns (`Section`, `Server In Use`, `Notes`, `Priority`, …) are preserved from the existing
`sites_list.csv`; edit them there. If an export is unavailable, the matching membership flags are
preserved rather than wiped.

## Ordering

Rows are grouped so the most relevant sites are easiest to find:

1. **Stable + live on GitHub Pages** (top) — `Host Category`/`Server In Use` is GitHub Pages and
   `Site Health` is `Live`.
2. **Everything else** (middle) — other live/redirecting sites we still manage.
3. **Unidentified** (bottom) — health `Unknown`/`Unreachable`, parked/unresolved, or an unknown
   status with no live response (the "don't know about" set).
4. **Left FFC** (very bottom) — `Left FFC = Yes`.

Within each group, rows are ordered by `Work Tier`, then most-recent activity, then keep
`.org`/`.com` pairs together by lead domain.

## Consuming from other repos

These files are public; pull them without credentials, e.g.:

```
https://raw.githubusercontent.com/FreeForCharity/FFC-Cloudflare-Automation/main/sites-list/sites_list.json
https://raw.githubusercontent.com/FreeForCharity/FFC-Cloudflare-Automation/main/sites-list/sites_list.csv
```
