# AI Agent Instructions for CloudFlare Automation

> **Managed by FFC-IN-AI-Management.** This file is deployed from the centralized AI
> configuration repository. Do not edit directly in target repos -- changes will be
> overwritten on the next sync. To update, modify the source at:
> `templates/overlays/powershell-infra/.github/agents/AI_AGENT_INSTRUCTIONS.md`
> in the FFC-IN-AI-Management repository.

## CRITICAL SECURITY REQUIREMENTS

**This document provides mandatory instructions for ALL AI agents (GitHub Copilot, ChatGPT,
Claude, Gemini, etc.) working on this repository.**

---

## Secret Management - MANDATORY RULES

### Rule 1: NEVER Expose API Tokens or Secrets

**FORBIDDEN ACTIONS:**

- NEVER write actual API tokens in code, documentation, or comments
- NEVER hardcode secrets in any file
- NEVER include secrets in example files
- NEVER commit secrets to git history
- NEVER expose secrets in logs, outputs, or error messages
- NEVER store secrets in environment files committed to git

**VIOLATION RESPONSE:** If you accidentally expose a secret:

1. Immediately stop all operations
2. Alert the user that a secret was exposed
3. Instruct them to revoke the exposed secret immediately
4. Create a new secret
5. Remove the exposed secret from all files and git history

---

## CORRECT Secret Management Methods

### Method 1: GitHub Secrets (REQUIRED for CI/CD)

**When modifying GitHub Actions workflows:**

1. **Always use `${{ secrets.SECRET_NAME }}` syntax**:

   ```yaml
   env:
     CLOUDFLARE_API_TOKEN_FFC: ${{ secrets.FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}
     CLOUDFLARE_API_TOKEN_CM: ${{ secrets.CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}
   ```

2. **Always validate secret presence BEFORE use**:

   ```yaml
   - name: Validate Secret Presence
     run: |
       if [ -z "${{ secrets.FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}" ] && [ -z "${{ secrets.CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}" ]; then
          echo "::error::Cloudflare token secret(s) are not set"
          exit 1
       fi
   ```

3. **NEVER echo or print secrets**:

   ```yaml
   # WRONG
   - run: echo ${{ secrets.FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}

   # CORRECT
   - run: echo "Secret is configured"
   ```

### Method 2: Environment Variables (Local Development)

**When instructing users for local development:**

1. **Use `CLOUDFLARE_API_TOKEN` (or `CLOUDFLARE_API_TOKEN_FFC` / `CLOUDFLARE_API_TOKEN_CM`)**:

   ```bash
   export CLOUDFLARE_API_TOKEN="<user-must-provide>"
   ```

2. **Always provide placeholder text, NEVER actual values**:

   ```bash
   # CORRECT
   export CLOUDFLARE_API_TOKEN="your-api-token-here"

   # WRONG
   export CLOUDFLARE_API_TOKEN="abc123xyz..."
   ```

### Method 3: Local .env Files (Individual Development)

**When creating example files:**

1. **ONLY commit `.env.example` files**:

   ```bash
   # .env.example
   CLOUDFLARE_API_TOKEN=your-cloudflare-api-token-here
   ```

2. **Ensure `.gitignore` excludes actual secrets**:

   ```gitignore
   .env
   .env.local
   .env*.local
   ```

3. **Instruct users to copy and edit**:
   ```bash
   cp .env.example .env
   # Edit .env with actual values
   ```

---

## Documentation Guidelines

### When Writing Documentation

**DO:**

- Use placeholder text: `"your-api-token-here"`
- Reference GitHub Secrets: `${{ secrets.FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}` /
  `${{ secrets.CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}`
- Use environment variables: `$CLOUDFLARE_API_TOKEN` / `$CLOUDFLARE_API_TOKEN_FFC` /
  `$CLOUDFLARE_API_TOKEN_CM`
- Instruct users to obtain secrets from official sources
- Link to official credential management docs

**DON'T:**

- Include actual API tokens (even if "example")
- Use realistic-looking token formats
- Copy tokens from user messages into docs
- Assume a token is fake -- treat all token-like strings as real

---

## Code Review Checklist

**Before suggesting or committing ANY change, verify:**

- [ ] No hardcoded secrets in code
- [ ] No hardcoded secrets in documentation
- [ ] GitHub Actions use `${{ secrets.* }}` syntax
- [ ] Secret validation exists in workflows
- [ ] `.gitignore` excludes secret files
- [ ] Example files use placeholders only
- [ ] Environment variables are properly documented
- [ ] No secrets in git history
- [ ] No secrets in commit messages
- [ ] Instructions guide users to secure methods

---

## Workflow Modification Guidelines

### Adding New Workflows

**Required security steps for any workflow using secrets:**

```yaml
jobs:
  job-name:
    runs-on: ubuntu-latest
    environment: cloudflare-prod
    steps:
      # STEP 1: Always validate secret presence first
      - name: Validate Secret Presence
        run: |
          if [ -z "${{ secrets.FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}" ] && [ -z "${{ secrets.CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}" ]; then
             echo "::error::Cloudflare token secret(s) not set"
             exit 1
          fi

      # STEP 2: Use secret via environment variables
      - name: Use Secret
        env:
          CLOUDFLARE_API_TOKEN_FFC: ${{ secrets.FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}
          CLOUDFLARE_API_TOKEN_CM: ${{ secrets.CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS }}
        run: |
          # Your commands here
          # Secret is available as environment variable

      # STEP 3: Never echo secrets in logs
      - name: Show Status
        run: echo "Workflow completed successfully"
```

### Modifying Existing Workflows

**When editing workflows:**

1. Check if secret validation exists
2. If missing, add validation step
3. Verify secrets use `${{ secrets.* }}` syntax
4. Ensure no secrets are echoed to logs
5. Test workflow with secret validation

---

## If User Provides a Secret

**When a user shares an API token or secret:**

1. **DO NOT** write it in any file
2. **DO NOT** include it in documentation
3. **DO NOT** commit it to the repository
4. **DO** instruct them to:
   - Add it to GitHub Secrets (for CI/CD)
   - Store it in a local `.env` file (for local dev)
   - Use environment variables (alternative)
5. **DO** remind them about security:
   ```
   SECURITY NOTE: I will not include your actual token in any files.
   Please add it to GitHub Secrets or your local .env file.
   ```

---

## User Instructions - Standard Responses

### When User Asks About API Token Setup

**Standard response template:**

```
To configure your CloudFlare API token:

For GitHub Actions (Recommended):

1. Go to repository Settings > Environments > cloudflare-prod
2. Add Environment secrets:
   - FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS
   - CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS
3. Workflows map these to environment variables: CLOUDFLARE_API_TOKEN_FFC /
   CLOUDFLARE_API_TOKEN_CM

For Local Development:

1. Create a .env file: cp .env.example .env
2. Edit .env and add your token
3. File is excluded by .gitignore - never commit it

Obtain Token From:

- CloudFlare Dashboard: https://dash.cloudflare.com/profile/api-tokens
- Required permissions: Zone DNS Edit, Zone Settings Edit, Zone Read
```

---

## Secret Rotation Best Practices

**When discussing secret management:**

1. **Recommend regular rotation**: Every 90 days
2. **Provide rotation steps**:
   - Generate new token in CloudFlare
   - Update GitHub Secret (for CI/CD)
   - Update local .env file (for local)
   - Revoke old token
   - Test deployments
3. **Never expose old tokens**: Treat as sensitive as new ones

---

## Required Reading for AI Agents

Before making ANY changes to this repository, review:

1. **SECURITY.md** -- Security policies and best practices
2. **.gitignore** -- Files excluded from version control
3. **.github/workflows/** -- Existing workflow patterns

---

## Validation Checklist Before Committing

Run this checklist for EVERY change:

```bash
# 1. Check for hardcoded secrets
git grep -i "api.*token.*=.*[a-zA-Z0-9_-]\{20,\}"

# 2. Check for exposed keys
git grep -E "[a-zA-Z0-9_-]{32,}"

# 3. Verify .gitignore
cat .gitignore | grep -E "(\.env|secrets)"

# 4. Check GitHub Actions
grep -r "secrets\." .github/workflows/

# 5. Verify no secrets in history
git log --all --full-history --source --pickaxe-regex -S "token.*[a-zA-Z0-9_-]{20,}"
```

**If ANY check reveals a secret:**

1. STOP immediately
2. Alert the user
3. Instruct them to revoke the secret
4. Clean git history if needed

---

## Summary for AI Agents

**Remember these 3 golden rules:**

1. **NEVER expose actual secrets** -- Use placeholders, references, or environment variables
2. **ALWAYS validate secrets exist** -- Before using them in workflows
3. **ALWAYS instruct users on secure methods** -- GitHub Secrets, local files, environment variables

**When in doubt:**

- Default to NOT including anything that looks like a secret
- Ask the user to add it via secure methods
- Reference documentation on proper secret management

---

**Version:** 2.0
**Last Updated:** 2026-02-16
**Managed By:** FFC-IN-AI-Management
**Applies To:** All AI agents working on PowerShell infrastructure repositories
