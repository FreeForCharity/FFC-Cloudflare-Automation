# GitHub Actions Rules

## Workflow Naming

- Workflows use numbered naming: `0-domain-status.yml`, `1-enforce-domain-standard.yml`, `15-website-provision.yml`
- Number indicates logical ordering, not execution sequence

## Environments and Secrets

GitHub Environments used for secret scoping:

| Environment       | Secrets                                                                         |
| ----------------- | ------------------------------------------------------------------------------- |
| `cloudflare-prod` | `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`, `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` |
| `m365-prod`       | `FFC_AZURE_CLIENT_ID`, `FFC_AZURE_TENANT_ID`                                    |
| `github-prod`     | `CBM_TOKEN`                                                                     |
| `wpmudev-prod`    | `FFC_WPMUDEV_GA_API_Token`                                                      |

## Secret Usage in Workflows

- Always reference secrets with: `${{ secrets.SECRET_NAME }}`
- Always validate presence before use
- Map to environment variables in the `env:` block
- Never echo secrets to logs

## Workflow Triggers

- `workflow_dispatch` for manual runs
- `issues: types: [assigned]` for automation (e.g., website provisioning)
- `push` / `pull_request` to `main` for CI
- `schedule` with cron for periodic tasks

## Validation

- actionlint validates all workflow YAML (CI enforces this)
- Test workflows with `act` locally when possible
- Use `--dry-run` or `-WhatIf` parameters in PowerShell scripts called from workflows
