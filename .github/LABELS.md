# GitHub Labels

This repository uses a standardized set of labels to categorize issues and pull requests. Labels are automatically synchronized using GitHub Actions.

## Automated Label Management

Labels are defined in `.github/labels.json` and automatically created/updated via the `Sync Labels` workflow when changes are pushed to the main branch.

## Available Labels

### Bot-Related Labels

- **dependencies** 🔵 - Pull requests that update a dependency file (used by Dependabot)
- **github-actions** ⚫ - Pull requests that update GitHub Actions workflows (used by Dependabot)
- **terraform** 🟣 - Pull requests that update Terraform code or providers (used by Dependabot)

### General Labels

- **security** 🔴 - Security-related issues or pull requests
- **documentation** 🔵 - Improvements or additions to documentation
- **bug** 🔴 - Something isn't working
- **enhancement** 🔵 - New feature or request
- **good first issue** 🟣 - Good for newcomers
- **help wanted** 🟢 - Extra attention is needed

## Adding New Labels

To add or modify labels:

1. Edit `.github/labels.json`
2. Add your label definition with `name`, `color`, and `description`
3. Commit and push to main branch
4. The workflow will automatically sync the labels

## Manual Sync

You can manually trigger the label sync workflow:

1. Go to Actions tab
2. Select "Sync Labels" workflow
3. Click "Run workflow"
