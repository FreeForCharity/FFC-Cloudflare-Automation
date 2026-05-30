# HCL Wellness (hclwellness.org) — GitHub repo onboarding

Onboarding runbook for standing up the FFC-EX GitHub repository for
[hclwellness.org](https://hclwellness.org/) with **GitHub-default Pages** (no custom domain yet).

## Goal

- Create a public repo under the `FreeForCharity` org named **`FFC-EX-hclwellness.org`**.
- Deploy the single-page site to **GitHub Pages using the default GitHub URL**
  (`https://freeforcharity.github.io/FFC-EX-hclwellness.org/`).
- **Do not** attach `hclwellness.org` as a Pages custom domain. The domain is not (yet) under FFC
  Cloudflare control, so we ship on the github.io URL first and cut the apex over later via a
  follow-up ticket.

This maps to `PagesDomainType=github-default` in `scripts/Create-GitHubRepo.ps1`, which enables
Pages with `build_type=workflow` (the template deploys via GitHub Actions) and skips the
`CNAME`/HTTPS-enforcement steps.

## Workflow to run

Run **`89. Repo - Create GitHub Repo [Repo]`** (`.github/workflows/create-repo.yml`) from the
Actions tab → **Run workflow**, with these inputs:

| Input             | Value                                           |
| ----------------- | ----------------------------------------------- |
| `RepoName`        | `FFC-EX-hclwellness.org`                        |
| `Description`     | `Website for HCL Wellness (hclwellness.org)`    |
| `TemplateRepo`    | `FreeForCharity/FFC_Single_Page_Template`       |
| `Visibility`      | `public`                                        |
| `EnableIssues`    | `true`                                          |
| `EnablePages`     | `true`                                          |
| `PagesDomainType` | `github-default`                                |
| `CNAME`           | _(leave empty)_                                 |
| `DryRun`          | `false` (run live; use `true` first to preview) |

Notes:

- The job runs in the `github-prod` environment and authenticates with the `CBM_TOKEN` secret (needs
  org repo-creation rights).
- `github-default` intentionally ignores `CNAME`; if one is supplied the script warns and drops it.
- Run once with `DryRun=true` to confirm the planned `gh` commands, then re-run with `DryRun=false`.

### Alternative: full website provisioning

If you also want the footer/leadership content patched in one pass, run
**`15. Website - Provision (Issue Assigned) [CF+Repo]`** instead. Because hclwellness.org is not in
FFC Cloudflare, the zone check resolves to "not controlled", so it automatically enables Pages
**without** a custom domain (same github-default outcome) and skips DNS enforcement.

## After the repo exists

1. Confirm Pages is live at `https://freeforcharity.github.io/FFC-EX-hclwellness.org/`.
2. Add the requester / technical POC as repo maintainers (workflow 15 does this automatically; for
   workflow 89 add them manually).
3. **Follow-up ticket** to move `hclwellness.org` into FFC Cloudflare, point DNS at GitHub Pages
   (apex + `www`), and set the Pages custom domain — see
   `.github/ISSUE_TEMPLATE/05-adminonly-github-pages-apex.yml` and
   [GitHub custom domain docs](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site).
