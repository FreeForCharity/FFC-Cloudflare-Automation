# AI Agent Instructions: FFC-Cloudflare-Automation

**Project:** FFC-Cloudflare-Automation -- a Free For Charity nonprofit website

**Organization:** [Free For Charity](https://freeforcharity.org) provides free, professionally built websites for 501(c)(3) nonprofit organizations. Every repo in this organization serves that mission.

---

## Tech Stack

| Layer     | Technology                                                         |
| --------- | ------------------------------------------------------------------ |
| Framework | Next.js with App Router (see package.json for version)             |
| Language  | TypeScript (strict mode)                                           |
| Styling   | Tailwind CSS v4 (CSS-based config, no tailwind.config file)        |
| Export    | Static (`output: 'export'` in next.config.ts)                      |
| Hosting   | GitHub Pages (custom domain + subpath fallback)                    |
| CI/CD     | GitHub Actions                                                     |
| Testing   | Jest + Testing Library, Playwright (E2E), jest-axe (accessibility) |

---

## Core Commands

| Command            | What It Does                | Typical Duration |
| ------------------ | --------------------------- | ---------------- |
| `npm install`      | Install dependencies        | ~17s             |
| `npm run dev`      | Start dev server            | ~1s startup      |
| `npm run format`   | Run Prettier to format code | ~2s              |
| `npm run lint`     | Run ESLint                  | ~2s              |
| `npm test`         | Run Jest unit tests         | ~5s              |
| `npm run build`    | Production static build     | ~30s             |
| `npm run test:e2e` | Run Playwright E2E tests    | ~15s             |

**NEVER CANCEL long-running commands.** Builds and E2E tests take time. Set your timeout to 180+ seconds and let them finish.

---

## Development Workflow

All changes follow this process:

1. **Issue** -- Work starts from a GitHub Issue
2. **Branch** -- Create a feature branch from `main`
3. **Develop** -- Make changes, commit frequently
4. **Pre-commit checklist** (run in this order):
   1. `npm run format` -- Auto-fix formatting
   2. `npm run lint` -- Catch code quality issues
   3. `npm test` -- Run unit tests
   4. `npm run build` -- Verify the static export succeeds
   5. `npm run test:e2e` -- Run end-to-end tests
5. **PR** -- Open a Pull Request, link to the issue with `Fixes #NNN` or `Refs #NNN`
6. **Merge** -- Merge via merge queue (no direct commits to `main`)

---

## Project Architecture

```
src/
  app/                  # Next.js App Router -- pages and layouts
    page.tsx            # Home page
    layout.tsx          # Root layout
    [route]/page.tsx     # Additional routes (e.g., privacy-policy/)
  components/           # Reusable UI components
  data/                 # Content modules (.ts) and JSON data files
  lib/                  # Utility functions and helpers
    assetPath.ts        # GitHub Pages asset path helper
public/                 # Static assets (Images/, Svgs/, fonts, favicons)
next.config.ts          # Next.js configuration
tsconfig.json           # TypeScript configuration
```

---

## Naming Conventions

**ALL route folders MUST use kebab-case.** This is an SEO best practice per Google Search Central. URLs like `/about-us` are preferred over `/aboutUs` or `/about_us`.

Examples:

- `src/app/about-us/page.tsx` (correct)
- `src/app/aboutUs/page.tsx` (wrong)
- `src/app/contact-form/page.tsx` (correct)

Component files use PascalCase: `HeroSection.tsx`, `DonateButton.tsx`.

---

## GitHub Pages & Asset Paths

These sites deploy to `https://freeforcharity.github.io/FFC-Cloudflare-Automation/` and optionally to a custom domain if one is configured for this repo.

**Always use the `assetPath()` helper** from `src/lib/assetPath.ts` for image and asset references:

```tsx
import { assetPath } from '@/lib/assetPath';

// Correct -- works on both custom domain and GitHub Pages subpath
<img src={assetPath('/Images/hero.jpg')} alt="Hero" />

// Wrong -- breaks on GitHub Pages subpath
<img src="/Images/hero.jpg" alt="Hero" />
```

The `NEXT_PUBLIC_BASE_PATH` environment variable controls the `basePath` in `next.config.ts`. The build system handles this automatically; you should not hardcode paths.

---

## Security

- **NEVER** expose API tokens or secrets in code, comments, or documentation
- **NEVER** hardcode secrets in any file
- In GitHub Actions workflows, **ALWAYS** use `${{ secrets.SECRET_NAME }}` syntax
- **ALWAYS** validate that secrets exist before using them in workflows
- **NEVER** echo or print secrets to logs
- For local development, use `.env` files (excluded from git via `.gitignore`)
- If a user provides a secret, **DO NOT** write it in any file. Instruct them to add it to GitHub Secrets or a local `.env` file.

---

## Testing Strategy

| Type          | Tool                   | Purpose                                 |
| ------------- | ---------------------- | --------------------------------------- |
| Unit          | Jest + Testing Library | Component rendering, utility functions  |
| Accessibility | jest-axe               | WCAG compliance, ARIA validation        |
| E2E           | Playwright             | Full page navigation, visual regression |

**Accessibility target:** WCAG AA compliance. The jest-axe integration catches common ARIA issues, color contrast violations, and missing landmarks.

---

## Known Issues

- **ESLint `img` warnings:** Some ESLint rules flag `<img>` tags in favor of `next/image`. For static exports, `<img>` with `assetPath()` is the correct approach. These warnings are expected.
- **Google Fonts:** Font loading may fail on restricted networks or air-gapped environments. The site should degrade gracefully with system fonts.
- **Static export limitations:** Dynamic features like API routes, middleware, and ISR are not available. All pages must be statically renderable at build time.

---

## Commit Message Format

Use [Conventional Commits](https://www.conventionalcommits.org/) format: `<type>: <description>`

| Type        | When to Use                             |
| ----------- | --------------------------------------- |
| `feat:`     | New feature or page                     |
| `fix:`      | Bug fix                                 |
| `docs:`     | Documentation only                      |
| `style:`    | Formatting (no code change)             |
| `refactor:` | Code restructuring (no behavior change) |
| `test:`     | Adding or updating tests                |
| `chore:`    | Build config, dependencies, CI          |

Example: `feat: add volunteer signup form with validation`

---

## CI Pipeline

GitHub Actions enforces the following on every PR:

1. **Prettier** -- `npm run format:check` (formatting must pass)
2. **ESLint** -- `npm run lint` (no errors allowed)
3. **Jest** -- `npm test` (all unit tests must pass)
4. **Build** -- `npm run build` (static export must succeed)
5. **Playwright** -- `npm run test:e2e` (E2E tests must pass)
6. **CodeQL** -- Static analysis and security scanning (separate workflow)

PRs cannot merge until all checks pass.

# PowerShell Infrastructure Overlay for AGENTS.md

> Append this content to the base AGENTS.md when deploying to PowerShell/infrastructure repos
> such as FFC-Cloudflare-Automation. This file is tool-agnostic and read by all AI assistants.

---

## PowerShell Infrastructure Context

This repository is a PowerShell-based infrastructure automation toolset. It manages DNS,
email authentication, domain billing, and website provisioning for ~30 charity domains
across the Free For Charity network.

### PowerShell Conventions

- **Formatting**: All `.ps1` files must pass `Invoke-Formatter` (from PSScriptAnalyzer). The CI
  pipeline compares original vs. formatted output and fails on any difference. Run locally before
  committing:
  ```powershell
  Import-Module PSScriptAnalyzer
  $original = Get-Content -Path .\MyScript.ps1 -Raw
  $formatted = Invoke-Formatter -ScriptDefinition $original
  Set-Content -Path .\MyScript.ps1 -Value $formatted -NoNewline
  ```
- **Linting**: PSScriptAnalyzer runs in CI with `-Severity @('Error','Warning')`. Errors fail the
  build; warnings are reported but tolerated.
- **Approved verbs**: Use standard PowerShell verbs: `Get-`, `Set-`, `New-`, `Remove-`, `Update-`,
  `Export-`, `Import-`, `Invoke-`, `Test-`.
- **Parameters**: Follow existing patterns -- `[Parameter(Mandatory = $true)]` with
  `[ValidateNotNullOrEmpty()]` or `[ValidateSet()]` as appropriate.
- **Destructive operations**: Support `-DryRun` or `-WhatIf` switches. Preview changes and print
  what would happen without applying them.
- **Error handling**: Use `try/catch` blocks. Call `Write-Error` for failures. Use `exit 1` for
  fatal errors that should stop the pipeline.
- **API token validation**: Always check that the required API token environment variable is
  present before making any API call. Fail early with a clear message if missing.

### GitHub Actions Workflows

This repo has 24+ GitHub Actions workflows using a numbered naming convention for UI ordering:

| Prefix | Category | Examples |
|--------|----------|---------|
| 0-* | Status/reporting | `0-domain-status.yml` |
| 1-* | Standards enforcement | `1-enforce-domain-standard.yml`, `1-audit-compliance.yml` |
| 2-* | Enforcement | `2-enforce-standard.yml` |
| 3-* | DNS record management | `3-manage-record.yml` |
| 4-* | Export/inventory | `4-domain-export-inventory.yml`, `4-export-summary.yml` |
| 5-8 | M365 operations | `5-m365-domain-and-dkim.yml`, `6-m365-list-domains.yml` |
| 7-8 | WHMCS operations | `7-whmcs-export-domains.yml`, `8-whmcs-export-products.yml` |
| 9-10 | WHMCS billing | `9-whmcs-export-payment-methods.yml`, `10-whmcs-zeffy-payments-import-draft.yml` |
| 11-15 | Provisioning | `11-cloudflare-zone-create.yml`, `15-website-provision.yml` |

Workflow names in YAML must start with a two-digit prefix (e.g., `name: '05. M365 - DKIM Setup'`).
The CI pipeline validates that prefixes are present and unique.

### CI Pipeline Checks

The `ci.yml` workflow runs on every PR and push to main:

1. **Workflow name prefix validation** -- two-digit prefixes, unique across all workflows
2. **actionlint** -- validates all workflow YAML syntax and expressions
3. **Prettier** -- checks formatting of YAML, JSON, Markdown files
4. **PowerShell syntax validation** -- parses all `.ps1` files for syntax errors
5. **PSScriptAnalyzer** -- lints PowerShell for errors and warnings
6. **Invoke-Formatter** -- checks PowerShell formatting matches expected output
7. **Sensitive file detection** -- scans for `.pem`, `.key`, `.env`, `.env.local` files

### Cloudflare Integration

- **Dual-account support**: FFC account and CM (Clarke Moyer) account, each with separate API tokens
- Environment variables: `CLOUDFLARE_API_TOKEN_FFC` and `CLOUDFLARE_API_TOKEN_CM`
- GitHub Secrets: `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` and `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`
- Scripts auto-detect which token can access a given zone
- Operations: zone creation, DNS record CRUD, DNS export, domain status reporting

### M365 Integration

- **Graph API via OIDC**: Federated credentials, no stored client secrets
- Domain operations: add domain to tenant, verify ownership via TXT record
- Email authentication: DKIM enable, SPF/DKIM/DMARC record creation in Cloudflare
- Standard MX record: `0 <domain>.mail.protection.outlook.com`
- Standard SPF: `v=spf1 include:spf.protection.outlook.com -all`
- DKIM: Two CNAME selectors pointing to `*.domainkey.<domain>.onmicrosoft.com`
- DMARC: `v=DMARC1; p=reject; rua=mailto:dmarc@<domain>`

### WHMCS Integration

- Domain billing exports and lookups
- Nameserver management (set Cloudflare NS on registered domains)
- Client, invoice, and transaction reporting
- Payment method exports

### WPMUDEV Integration

- WordPress hosting site inventory exports
- Site health monitoring data

### GitHub Environment Secrets

Workflows that need secrets must use GitHub Environments:

| Environment | Secrets Available |
|-------------|------------------|
| `cloudflare-prod` | `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`, `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` |
| `m365-prod` | Azure AD OIDC credentials for Graph API |
| `github-prod` | `GH_PAT` for cross-repo operations |
| `wpmudev-prod` | `WPMUDEV_API_KEY` |
| `whmcs-prod` | `WHMCS_API_IDENTIFIER`, `WHMCS_API_SECRET`, `WHMCS_API_URL` |
