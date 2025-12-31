# GitHub Labels Configuration

This directory contains the configuration for GitHub repository labels used by various automation tools and bots.

## Files

### `labels.yml`
Defines all labels used in the repository for issues, pull requests, and automation.

**Label Categories:**

1. **Dependency Management** - Used by Dependabot
   - `dependencies` - Updates to dependencies
   - `github-actions` - Updates to GitHub Actions workflows
   - `python` - Updates to Python dependencies

2. **Domain Management** - Used by issue templates and workflows
   - `domain-add` - Request to add a new domain
   - `domain-purchase` - Request to purchase a new domain
   - `domain-remove` - Request to remove a domain
   - `cloudflare` - Related to Cloudflare configuration
   - `github-pages` - Related to GitHub Pages configuration
   - `dns-config` - DNS configuration changes
   - `subdomain` - Subdomain configuration

3. **General Labels** - For project management
   - `bug` - Something isn't working
   - `enhancement` - New feature or request
   - `documentation` - Improvements or additions to documentation
   - `security` - Security-related issues or updates
   - `help wanted` - Extra attention is needed
   - `good first issue` - Good for newcomers

## Workflows

### `initialize-labels.yml`
Manual workflow to create or update all labels defined in `labels.yml`.

**How to use:**
1. Go to Actions tab in GitHub
2. Select "Initialize Labels" workflow
3. Click "Run workflow"
4. Select the branch and run

This will create/update all labels in the repository based on the configuration.

### `sync-labels.yml`
Automatic workflow that syncs labels whenever `labels.yml` is updated on the main branch.

**Triggers:**
- Push to main branch that modifies `.github/labels.yml`
- Manual workflow dispatch

## Initial Setup

After this PR is merged, the labels should be created by:

1. The `sync-labels.yml` workflow will automatically run on merge to main
2. Alternatively, you can manually run the `initialize-labels.yml` workflow

## Modifying Labels

To add, update, or remove labels:

1. Edit `.github/labels.yml`
2. Commit and push to main branch
3. The `sync-labels.yml` workflow will automatically apply changes
4. Or manually trigger `initialize-labels.yml`

## Label Usage

These labels are automatically applied by:
- **Dependabot**: Uses `dependencies`, `github-actions`, and `python` labels for its PRs
- **Issue Templates**: Automatically apply labels based on the template used
- **Manual Assignment**: Maintainers can apply any label to issues/PRs

## References

- [GitHub Labels Documentation](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/managing-labels)
- [Dependabot Configuration](https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/configuration-options-for-the-dependabot.yml-file)
