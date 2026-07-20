# Sync Guide: Auditing and Deploying AI Configs

This guide walks through the day-to-day use of the audit and sync scripts that keep AI configuration files consistent across all FFC repositories.

## Prerequisites

Before running any scripts, ensure you have:

1. **PowerShell 5.1+** (Windows) or **PowerShell 7+** (cross-platform)
   ```powershell
   $PSVersionTable.PSVersion
   ```

2. **GitHub CLI (`gh`) authenticated**
   ```powershell
   gh auth status
   ```
   If not authenticated:
   ```powershell
   gh auth login
   ```
   Ensure your token has `repo` scope for all target organizations.

3. **Access to target organizations** -- your GitHub account must be a member of `FreeForCharity` and `koenig-childhood-cancer-foundation` (or whichever orgs you target).

## Step 1: Running the Audit

The audit script scans all repos and records which AI config files are present.

```powershell
cd path\to\FFC-IN-AI-Management
.\scripts\Audit-AIConfigs.ps1
```

### Options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-Organizations` | `@("FreeForCharity", "koenig-childhood-cancer-foundation")` | GitHub orgs to scan |
| `-OutputPath` | `"inventory"` | Directory for output files |

### Example: Audit a single org

```powershell
.\scripts\Audit-AIConfigs.ps1 -Organizations @("FreeForCharity")
```

### Output

The audit produces two files:

- **`inventory/repos.json`** -- Machine-readable inventory with every repo's AI config status.
- **`inventory/audit-report.md`** -- Human-readable markdown table showing presence/absence of each config file.

## Step 2: Understanding the Audit Report

Open `inventory/audit-report.md` to see a table like:

| Org | Repo | Type | CLAUDE.md | AGENTS.md | GEMINI.md | copilot-instructions | ... |
|-----|------|------|-----------|-----------|-----------|---------------------|-----|
| FreeForCharity | FFC-EX-legioninthewoods.org | base | Yes | - | - | Yes | ... |

- **Yes** means the file or directory is present.
- **-** means it is not present.

Use this to identify repos that are missing expected configurations.

## Step 3: Dry-Run Sync

Before making any changes, always preview with `-DryRun`:

```powershell
# Preview changes for a single repo
.\scripts\Sync-AIConfigs.ps1 -RepoName "FFC-EX-legioninthewoods.org" -DryRun

# Preview changes for all "base" type repos
.\scripts\Sync-AIConfigs.ps1 -RepoType "base" -DryRun

# Preview changes for everything
.\scripts\Sync-AIConfigs.ps1 -All -DryRun
```

The dry-run output shows:
- Which repos would be targeted
- Which files would be created or updated
- The first 80 characters of each file (for quick verification)
- File sizes

## Step 4: Running Sync for Real

Once you are satisfied with the dry-run output:

```powershell
# Sync a single repo
.\scripts\Sync-AIConfigs.ps1 -RepoName "FFC-EX-legioninthewoods.org"

# Sync all repos of a type
.\scripts\Sync-AIConfigs.ps1 -RepoType "base"

# Sync everything
.\scripts\Sync-AIConfigs.ps1 -All
```

### What happens during sync

For each target repo, the script:

1. Assembles files from `templates/base/` + `templates/overlays/{type}/`
2. Replaces template variables (`{{REPO_NAME}}`, `{{DOMAIN_NAME}}`, `{{BASE_PATH}}`)
3. Creates a branch `chore/sync-ai-configs` from `main`
4. Commits each file to the branch via the GitHub API
5. Opens a pull request titled "chore: sync AI configuration files"

You then review and merge each PR as normal.

## Handling Merge Conflicts

If a target repo has local modifications to AI config files that conflict with the synced versions:

1. The PR will show merge conflicts in the GitHub UI.
2. Resolve conflicts manually in the PR, keeping any repo-specific customizations that should be preserved.
3. If the repo needs permanent deviations from the template, consider:
   - Adding a new overlay type for that class of repo
   - Adding the repo-specific content to the overlay's `.patch` file so it is appended rather than replaced

## Adding New Repos to Inventory

### Option A: Run the audit

The simplest approach -- the audit script discovers all repos automatically:

```powershell
.\scripts\Audit-AIConfigs.ps1
```

### Option B: Manual entry

Add a new entry to `inventory/repos.json`:

```json
{
  "org": "FreeForCharity",
  "name": "FFC-EX-newcharity.org",
  "type": "base",
  "domain": "newcharity.org",
  "basePath": "/FFC-EX-newcharity.org",
  "aiConfigs": {},
  "lastSync": null
}
```

Then run the sync:

```powershell
.\scripts\Sync-AIConfigs.ps1 -RepoName "FFC-EX-newcharity.org"
```

## Common Workflows

### New charity site onboarded

```powershell
# 1. Add to inventory (or re-run audit)
.\scripts\Audit-AIConfigs.ps1

# 2. Preview what will be synced
.\scripts\Sync-AIConfigs.ps1 -RepoName "FFC-EX-newcharity.org" -DryRun

# 3. Sync
.\scripts\Sync-AIConfigs.ps1 -RepoName "FFC-EX-newcharity.org"

# 4. Review and merge the PR on GitHub
```

### Template updated

```powershell
# 1. Edit files in templates/base/ or templates/overlays/
# 2. Preview across all repos
.\scripts\Sync-AIConfigs.ps1 -All -DryRun

# 3. Deploy
.\scripts\Sync-AIConfigs.ps1 -All

# 4. Review and merge PRs
```

### Quarterly audit

```powershell
# 1. Re-scan everything
.\scripts\Audit-AIConfigs.ps1

# 2. Review the report
code inventory\audit-report.md

# 3. Sync any repos that are out of date
.\scripts\Sync-AIConfigs.ps1 -All -DryRun
.\scripts\Sync-AIConfigs.ps1 -All
```
