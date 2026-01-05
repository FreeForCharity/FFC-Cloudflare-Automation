---
title: Refactor DNS Automation from Python to PowerShell
labels: enhancement, refactor, powershell, automation
assignees: clark
---

## Objective

Refactor the existing Python-based DNS automation (`update_dns.py`) into a unified, robust
PowerShell module/script (`Update-CloudflareDns.ps1`). This change aims to simplify GitHub Actions
workflows (removing Python dependency management) and align tooling with the primary development
environment.

## Motivation

- **Simplification**: Remove `pip install` steps and virtual environment overhead in CI/CD.
- **Native Execution**: Leverage PowerShell which is pre-installed on all GitHub Actions runners.
- **Feature Gaps**: The current Python script lacks support for MX and TXT records, which are
  required for "New Domain" workflows.
- **Consolidation**: Merge the logic of `update_dns.py` and `Update-StagingDns.ps1` into a single,
  capable tool.

## Scope & Requirements

The new PowerShell script must replicate all `update_dns.py` functionality and expand upon it to
support all undefined use cases in the Issue Templates.

### Core Features (Parity)

- [ ] CRUD operations for **A**, **AAAA**, and **CNAME** records.
- [ ] **Proxy Support**: Toggle `proxied` status (Orange/Grey cloud).
- [ ] **Dry Run**: `WhatIf` support or explicit `-DryRun` switch.
- [ ] **Authentication**: Support token via parameter and environment variable
      (Cloudflare API token via `CLOUDFLARE_API_TOKEN` / `CLOUDFLARE_API_TOKEN_FFC` / `CLOUDFLARE_API_TOKEN_CM`).

### Expanded Features (New)

- [ ] **MX Record Support**: Required for "Purchase New Domain" (Microsoft 365 setup).
- [ ] **TXT Record Support**: Required for SPF, DMARC, and Verification.
- [ ] **SRV Record Support**: (Optional but recommended for full coverage).
- [ ] **Zone Lists**: Ability to list zones/records without finding a specific ID first (search
      improvement).

## Implementation Plan (Sub-tasks per Workflow)

### 1. Purchase & Add New .org Domain (Template 01)

- [ ] Implement `New-CloudflareRecord -Type MX` (for Exchange Online).
- [ ] Implement `New-CloudflareRecord -Type TXT` (for SPF & DMARC).
- [ ] Verify handling of multiple records in batch (if needed for setup).

### 2. Add Existing Domain (Template 02)

- [ ] Ensure `Import-CloudflareRecords` capability (or guidance on bulk script usage).
- [ ] Verify `TXT` record verification steps for domain ownership.

### 3. Remove Domain (Template 03)

- [ ] Implement `Remove-CloudflareZone` or verify `Remove-CloudflareRecord` safeguards.
- [ ] (Optional) Script `Export-CloudflareZone` to CSV (porting `export_zone_dns_summary.py` logic)
      _before_ removal.

### 4. GitHub Pages - Apex Domain (Template 04)

- [ ] Script specific "Apex Setup" logic:
  - Create 4 A records (`185.199.108.153`...`111.153`).
  - Disable Proxy (`-Proxied $false`).

### 5. GitHub Pages - Subdomain (Template 05)

- [ ] Script specific "Subdomain Setup" logic:
  - Create CNAME to `[user].github.io`.
  - Disable Proxy (`-Proxied $false`).

## Acceptance Criteria

- [ ] `update_dns.py` can be deprecated/removed.
- [ ] CI/CD workflows are updated to use `pwsh`.
- [ ] Documentation (`README.md`) is updated with PowerShell examples.
