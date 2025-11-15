# Security Policy

## Sensitive Information Handling

### CloudFlare API Tokens

**⚠️ CRITICAL: Never commit API tokens to version control**

#### Where the API Token is Stored

The CloudFlare API token is stored **only** in the `terraform.tfvars` file, which is:
- ✅ Located on your local machine
- ✅ Excluded from git by `.gitignore`
- ✅ Never committed to the repository
- ✅ Never pushed to GitHub

#### How to Securely Obtain the Token

1. **From CloudFlare Dashboard**:
   - Log in to https://dash.cloudflare.com
   - Go to "My Profile" → "API Tokens"
   - Create a new token or use an existing one
   - Copy the token and store it in `terraform.tfvars`

2. **From Your Team Lead**:
   - Request the API token through secure channels (not email, Slack, etc.)
   - Use a password manager or secure sharing tool
   - Delete any messages containing the token after retrieval

#### Configuration File Security

The `terraform.tfvars` file contains sensitive information:

```hcl
# This file should NEVER be committed to git
cloudflare_api_token = "your-actual-token-here"
domain_name          = "ffcadmin.org"
github_pages_domain  = "freeforcharity.github.io"
```

**Protection Mechanisms**:
- File is listed in `.gitignore`
- Git will not track or commit this file
- File remains on your local machine only

#### What to Do If a Token is Exposed

If an API token is accidentally committed or exposed:

1. **Immediately revoke the token** in CloudFlare dashboard
2. **Generate a new API token** with the same permissions
3. **Update `terraform.tfvars`** with the new token
4. **Notify your team** of the security incident
5. **Review git history** and remove exposed tokens if possible

#### Best Practices

✅ **DO**:
- Store tokens in `terraform.tfvars` (excluded by .gitignore)
- Use scoped tokens with minimal required permissions
- Rotate tokens regularly (every 90 days recommended)
- Use different tokens for different environments (dev/staging/prod)
- Review `.gitignore` before committing changes

❌ **DON'T**:
- Commit tokens to git repositories
- Share tokens via email, Slack, or other unsecured channels
- Use tokens in documentation or README files
- Hard-code tokens in Terraform files
- Use the same token across multiple projects

#### Alternative: Environment Variables

For additional security, you can use environment variables instead of `terraform.tfvars`:

```bash
export TF_VAR_cloudflare_api_token="your-token-here"
export TF_VAR_domain_name="ffcadmin.org"
export TF_VAR_github_pages_domain="freeforcharity.github.io"

terraform apply
```

This keeps the token out of files entirely.

#### Verification

To verify your token is not tracked by git:

```bash
# This should NOT show terraform.tfvars
git ls-files | grep terraform.tfvars

# This should show terraform.tfvars.example only
git ls-files | grep tfvars
```

## Reporting Security Issues

If you discover a security vulnerability:

1. **Do NOT** open a public issue
2. Contact the repository maintainers privately
3. Provide details about the vulnerability
4. Wait for confirmation before disclosing publicly

## Security Checklist

Before deploying:

- [ ] API token stored in `terraform.tfvars` (not committed)
- [ ] `.gitignore` includes `*.tfvars` (except `*.tfvars.example`)
- [ ] No tokens in documentation files
- [ ] No tokens in example files
- [ ] Git history doesn't contain exposed tokens
- [ ] Token has minimal required permissions
- [ ] Token is scoped to specific domains only

## Additional Resources

- [CloudFlare API Token Documentation](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)
- [Terraform Sensitive Variables](https://www.terraform.io/docs/language/values/variables.html#suppressing-values-in-cli-output)
- [Git Security Best Practices](https://git-scm.com/book/en/v2/GitHub-Account-Administration-and-Security)

---

**Remember**: Security is everyone's responsibility. When in doubt, ask before committing!
