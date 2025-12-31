# Legacy Terraform Files

## Important Notice

**The Terraform files in this repository are legacy artifacts and are no longer the primary method for managing DNS configurations.**

## Current Workflow

FFC Cloudflare Automation now uses an **issue-based workflow**:

1. **Submit requests** using GitHub issue templates in `.github/ISSUE_TEMPLATE/`
2. **Administrators execute** DNS changes using Python scripts and Cloudflare API
3. **Full audit trail** through GitHub issues

## Supported Operations via Issue Templates

1. Purchase and add new .org domains
2. Add existing domains to Cloudflare
3. Remove domains from Cloudflare
4. Configure GitHub Pages for apex domains
5. Configure GitHub Pages for subdomains

## Why the Change?

The repository transitioned from Terraform-based infrastructure management to a more flexible, script-based approach because:

- **Greater flexibility**: Python scripts allow for more dynamic DNS management
- **Better tracking**: GitHub issues provide clear audit trail of all changes
- **Simpler workflow**: No need to manage Terraform state or plan/apply cycles
- **Direct API access**: Scripts use Cloudflare API directly for faster execution
- **Issue-based requests**: Structured templates ensure all necessary information is captured

## Legacy Files Retained

The following Terraform files are kept for historical reference:

- `main.tf` - Main Terraform configuration
- `variables.tf` - Terraform variables
- `outputs.tf` - Terraform outputs
- `versions.tf` - Terraform version constraints
- `terraform.tfvars.example` - Example configuration file
- Related documentation:
  - `DEPLOYMENT_CHECKLIST.md`
  - `FFCADMIN_README.md`
  - `GITHUB_ACTIONS.md`
  - `QUICK_START.md`
  - `SETUP_GUIDE.md`
  - `TESTING.md`

## For Administrators

Instead of Terraform, use:

### Python Scripts
- `update_dns.py` - Manage DNS records
- `export_zone_dns_summary.py` - Export DNS configurations
- `export_zone_a_records.py` - Export A records
- `Update-StagingDns.ps1` - PowerShell alternative for quick updates

### Issue Templates
Navigate to: `.github/ISSUE_TEMPLATE/` or [create a new issue](https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues/new/choose)

## Migration Path

If you have existing Terraform state:

1. **Export current configuration** using Terraform outputs or Cloudflare dashboard
2. **Document all resources** managed by Terraform
3. **Create corresponding GitHub issues** for ongoing management
4. **Safely remove Terraform state** once all resources are documented
5. **Use Python scripts** for all future changes

## Questions?

For questions about the legacy Terraform setup or migration, please:
- Review the legacy documentation files listed above
- Open a GitHub issue with the `question` label
- Contact the FFC Cloudflare administrators

---

**Last Updated**: December 2025
