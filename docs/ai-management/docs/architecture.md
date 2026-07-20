# Architecture: Template System and Sync Pipeline

This document explains how FFC-IN-AI-Management assembles and deploys AI configuration files across the FFC repository fleet.

## Base + Overlay Template System

AI config files are stored in a two-layer template structure:

```
templates/
  base/                        # Files that go to EVERY repo
    CLAUDE.md
    AGENTS.md
    .github/copilot-instructions.md
    .claude/settings.json
    ...
  overlays/
    base/                      # Extra files for "base" type repos (no-op if same as base)
    powershell-infra/           # Extra/replacement files for PowerShell infrastructure repos
      CLAUDE.md                 # Replaces the base CLAUDE.md entirely
      CLAUDE.md.patch           # OR appends to the base CLAUDE.md (see .patch files below)
```

### Assembly Order

For each target repo, the sync script:

1. **Collects all files from `templates/base/`** -- these form the foundation.
2. **Determines the repo type** using `Get-RepoType.ps1` (returns `base` or `powershell-infra`).
3. **Collects overlay files from `templates/overlays/{type}/`**.
4. **Merges overlays into the base set:**
   - A file with the same path as a base file **replaces** the base version entirely.
   - A `.patch` file **appends** its content to the corresponding base file (see below).
   - A file with a new path is **added** to the set.

### .patch Files

If an overlay directory contains a file named `CLAUDE.md.patch`, its content is appended to the end of the base `CLAUDE.md` rather than replacing it. This allows overlays to extend base files without duplicating their content.

Example:

```
templates/base/CLAUDE.md                     # Base instructions for all repos
templates/overlays/powershell-infra/CLAUDE.md.patch   # Additional PowerShell-specific guidance
```

Result: The target repo receives a `CLAUDE.md` that contains the base content followed by the patch content.

## Template Variable Replacement

After assembly, the sync script performs variable substitution on every file:

| Variable | Source | Example |
|----------|--------|---------|
| `{{REPO_NAME}}` | `repos.json` entry `.name` | `FFC-EX-legioninthewoods.org` |
| `{{DOMAIN_NAME}}` | `repos.json` entry `.domain` | `legioninthewoods.org` |
| `{{BASE_PATH}}` | `repos.json` entry `.basePath` | `/FFC-EX-legioninthewoods.org` |

Variables that resolve to `null` or empty are replaced with an empty string.

## Inventory-Driven Deployment

The sync script reads `inventory/repos.json` to determine which repos to target and what metadata to inject. The inventory is maintained in two ways:

1. **Manual entry** -- add repos directly to `repos.json`.
2. **Audit script** -- run `Audit-AIConfigs.ps1` to scan GitHub orgs and rebuild the inventory automatically.

Each repo entry includes:

```json
{
  "org": "FreeForCharity",
  "name": "FFC-EX-legioninthewoods.org",
  "type": "base",
  "domain": "legioninthewoods.org",
  "basePath": "/FFC-EX-legioninthewoods.org",
  "aiConfigs": {},
  "lastSync": null
}
```

## PR-Based Sync

The sync script **never pushes directly to `main`**. Instead, it:

1. Creates a branch named `chore/sync-ai-configs` from the target repo's `main` branch.
2. Commits each assembled file to that branch via the GitHub API.
3. Opens a pull request for review.

This ensures every change is reviewed before merging and maintains a clear audit trail.

## Data Flow Diagram

```
inventory/repos.json
        |
        v
Sync-AIConfigs.ps1
        |
        +---> templates/base/*          (collect base files)
        +---> templates/overlays/{type}/* (merge overlay files)
        +---> Variable replacement       ({{REPO_NAME}}, etc.)
        |
        v
GitHub API
        |
        +---> Create branch: chore/sync-ai-configs
        +---> Commit files to branch
        +---> Open pull request
```
