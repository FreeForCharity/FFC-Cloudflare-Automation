# AI Agent Instructions for CloudFlare Automation

## ⚠️ CRITICAL SECURITY REQUIREMENTS

**This document provides mandatory instructions for ALL AI agents (GitHub Copilot, ChatGPT, Claude, etc.) working on this repository.**

---

## 🔒 Secret Management - MANDATORY RULES

### Rule 1: NEVER Expose API Tokens or Secrets

**FORBIDDEN ACTIONS:**
- ❌ NEVER write actual API tokens in code, documentation, or comments
- ❌ NEVER hardcode secrets in any file
- ❌ NEVER include secrets in example files
- ❌ NEVER commit secrets to git history
- ❌ NEVER expose secrets in logs, outputs, or error messages
- ❌ NEVER store secrets in environment files committed to git

**VIOLATION RESPONSE:**
If you accidentally expose a secret:
1. Immediately stop all operations
2. Alert the user that a secret was exposed
3. Instruct them to revoke the exposed secret immediately
4. Create a new secret
5. Remove the exposed secret from all files and git history

---

## ✅ CORRECT Secret Management Methods

### Method 1: GitHub Secrets (REQUIRED for CI/CD)

**When modifying GitHub Actions workflows:**

1. **Always use `${{ secrets.SECRET_NAME }}` syntax**:
   ```yaml
   env:
     TF_VAR_cloudflare_api_token: ${{ secrets.CLOUDFLARE_API_TOKEN }}
   ```

2. **Always validate secret presence BEFORE use**:
   ```yaml
   - name: Validate Secret Presence
     run: |
       if [ -z "${{ secrets.CLOUDFLARE_API_TOKEN }}" ]; then
         echo "::error::CLOUDFLARE_API_TOKEN secret is not set"
         exit 1
       fi
   ```

3. **NEVER echo or print secrets**:
   ```yaml
   # ❌ WRONG
   - run: echo ${{ secrets.CLOUDFLARE_API_TOKEN }}
   
   # ✅ CORRECT
   - run: echo "Secret is configured"
   ```

### Method 2: Environment Variables (Local Development)

**When instructing users for local development:**

1. **Always use `TF_VAR_` prefix**:
   ```bash
   export TF_VAR_cloudflare_api_token="<user-must-provide>"
   ```

2. **Always provide placeholder text, NEVER actual values**:
   ```bash
   # ✅ CORRECT
   export TF_VAR_cloudflare_api_token="your-api-token-here"
   
   # ❌ WRONG
   export TF_VAR_cloudflare_api_token="abc123xyz..."
   ```

### Method 3: Local terraform.tfvars (Individual Development)

**When creating example files:**

1. **ONLY commit `.tfvars.example` files**:
   ```hcl
   # terraform.tfvars.example
   cloudflare_api_token = "your-cloudflare-api-token-here"
   domain_name          = "example.com"
   ```

2. **Ensure `.gitignore` excludes actual secrets**:
   ```gitignore
   *.tfvars
   !*.tfvars.example
   ```

3. **Instruct users to copy and edit**:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   # Edit terraform.tfvars with actual values
   ```

---

## 📝 Documentation Guidelines

### When Writing Documentation

**DO:**
- ✅ Use placeholder text: `"your-api-token-here"`
- ✅ Reference GitHub Secrets: `${{ secrets.CLOUDFLARE_API_TOKEN }}`
- ✅ Use environment variables: `$TF_VAR_cloudflare_api_token`
- ✅ Instruct users to obtain secrets from official sources
- ✅ Link to official credential management docs

**DON'T:**
- ❌ Include actual API tokens (even if "example")
- ❌ Use realistic-looking token formats
- ❌ Copy tokens from user messages into docs
- ❌ Assume a token is fake - treat all token-like strings as real

### Example Documentation Patterns

**✅ CORRECT:**
```markdown
1. Create a CloudFlare API token at https://dash.cloudflare.com/profile/api-tokens
2. Add the token to GitHub Secrets as `CLOUDFLARE_API_TOKEN`
3. The workflow will use: `${{ secrets.CLOUDFLARE_API_TOKEN }}`
```

**❌ WRONG:**
```markdown
1. Use this API token: em7chiooYdKI4T3d3Oo1j31-ekEV2FiUfZxwjv-Q
2. Copy it to your configuration
```

---

## 🔍 Code Review Checklist

**Before suggesting or committing ANY change, verify:**

- [ ] No hardcoded secrets in code
- [ ] No hardcoded secrets in documentation
- [ ] GitHub Actions use `${{ secrets.* }}` syntax
- [ ] Secret validation exists in workflows
- [ ] `.gitignore` excludes secret files
- [ ] Example files use placeholders only
- [ ] Environment variables use `TF_VAR_` prefix
- [ ] No secrets in git history
- [ ] No secrets in commit messages
- [ ] Instructions guide users to secure methods

---

## 🚨 If User Provides a Secret

**When a user shares an API token or secret:**

1. **DO NOT** write it in any file
2. **DO NOT** include it in documentation
3. **DO NOT** commit it to the repository
4. **DO** instruct them to:
   - Add it to GitHub Secrets (for CI/CD)
   - Store it in local `terraform.tfvars` (for local dev)
   - Use environment variables (alternative)
5. **DO** remind them about security:
   ```
   ⚠️ SECURITY NOTE: I will not include your actual token in any files.
   Please add it to GitHub Secrets or your local terraform.tfvars file.
   ```

---

## 📋 Workflow Modification Guidelines

### Adding New Workflows

**Required security steps for any workflow using secrets:**

```yaml
jobs:
  job-name:
    runs-on: ubuntu-latest
    steps:
      # STEP 1: Always validate secret presence first
      - name: Validate Secret Presence
        run: |
          if [ -z "${{ secrets.CLOUDFLARE_API_TOKEN }}" ]; then
            echo "::error::Required secret not set"
            exit 1
          fi
      
      # STEP 2: Use secret via environment variables
      - name: Use Secret
        env:
          TF_VAR_cloudflare_api_token: ${{ secrets.CLOUDFLARE_API_TOKEN }}
        run: |
          # Your commands here
          # Secret is available as environment variable
      
      # STEP 3: Never echo secrets in logs
      - name: Show Status
        run: echo "✅ Workflow completed successfully"
```

### Modifying Existing Workflows

**When editing workflows:**

1. Check if secret validation exists
2. If missing, add validation step
3. Verify secrets use `${{ secrets.* }}` syntax
4. Ensure no secrets are echoed to logs
5. Test workflow with secret validation

---

## 🎯 User Instructions - Standard Responses

### When User Asks About API Token Setup

**Standard response template:**

```markdown
To configure your CloudFlare API token:

**For GitHub Actions (Recommended):**
1. Go to repository Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Name: `CLOUDFLARE_API_TOKEN`
4. Value: [Paste your CloudFlare API token]
5. Click "Add secret"

The workflows will automatically use: `${{ secrets.CLOUDFLARE_API_TOKEN }}`

**For Local Development:**
1. Copy the example file:
   ```bash
   cp terraform.tfvars.example terraform.tfvars
   ```
2. Edit `terraform.tfvars` and add your token
3. File is excluded by `.gitignore` - never commit it

**Obtain Token From:**
- CloudFlare Dashboard: https://dash.cloudflare.com/profile/api-tokens
- Required permissions: Zone DNS Edit, Zone Settings Edit, Zone Read
```

### When User Asks to Add Token to Docs

**Standard response:**

```markdown
⚠️ For security reasons, I cannot add actual API tokens to documentation files.

Instead, I'll show you how to configure it securely:
- GitHub Actions: Use GitHub Secrets
- Local dev: Use terraform.tfvars (excluded from git)

See SECURITY.md and GITHUB_ACTIONS.md for complete instructions.
```

---

## 🔄 Secret Rotation Best Practices

**When discussing secret management:**

1. **Recommend regular rotation**: Every 90 days
2. **Provide rotation steps**:
   - Generate new token in CloudFlare
   - Update GitHub Secret (for CI/CD)
   - Update local terraform.tfvars (for local)
   - Revoke old token
   - Test deployments
3. **Never expose old tokens**: Treat as sensitive as new ones

---

## 📚 Required Reading for AI Agents

Before making ANY changes to this repository, review:

1. **SECURITY.md** - Security policies and best practices
2. **GITHUB_ACTIONS.md** - GitHub Secrets setup and usage
3. **.gitignore** - Files excluded from version control
4. **.github/workflows/** - Existing workflow patterns

---

## ✅ Validation Checklist Before Committing

Run this checklist for EVERY change:

```bash
# 1. Check for hardcoded secrets
git grep -i "api.*token.*=.*[a-zA-Z0-9_-]\{20,\}"

# 2. Check for exposed keys
git grep -E "[a-zA-Z0-9_-]{32,}"

# 3. Verify .gitignore
cat .gitignore | grep -E "(tfvars|\.env|secrets)"

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

## 🎓 Training Examples

### Example 1: User Shares Token

**User says:**
> "Here's my API token: em7chiooYdKI4T3d3Oo1j31-ekEV2FiUfZxwjv-Q"

**AI Response:**
```
⚠️ For security, I won't include your actual token in any files.

To configure it:
1. Add to GitHub Secrets as CLOUDFLARE_API_TOKEN, OR
2. Add to local terraform.tfvars file

See SECURITY.md for detailed instructions.
```

### Example 2: Updating Workflows

**User asks:**
> "Add the CloudFlare token to the workflow"

**AI Response:**
```
I'll add the token using GitHub Secrets (secure method):

env:
  TF_VAR_cloudflare_api_token: ${{ secrets.CLOUDFLARE_API_TOKEN }}

The actual token value should be added to repository secrets.
```

### Example 3: Creating Documentation

**User asks:**
> "Document how to use the API token"

**AI Response:**
```
I'll create documentation with secure examples:

1. Using placeholders: "your-api-token-here"
2. Referencing GitHub Secrets: ${{ secrets.CLOUDFLARE_API_TOKEN }}
3. Using environment variables: $TF_VAR_cloudflare_api_token

No actual tokens will be included in the documentation.
```

---

## 🚀 Summary for AI Agents

**Remember these 3 golden rules:**

1. **NEVER expose actual secrets** - Use placeholders, references, or environment variables
2. **ALWAYS validate secrets exist** - Before using them in workflows
3. **ALWAYS instruct users on secure methods** - GitHub Secrets, local files, environment variables

**When in doubt:**
- Default to NOT including anything that looks like a secret
- Ask the user to add it via secure methods
- Reference documentation on proper secret management

---

## 📞 Support

If you encounter a situation not covered by these instructions:
1. Default to the most secure option
2. Consult SECURITY.md
3. Ask the user for clarification
4. Document the scenario for future reference

---

**Version:** 1.0  
**Last Updated:** 2025-11-16  
**Applies To:** All AI agents working on this repository
