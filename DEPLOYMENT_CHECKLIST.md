# Deployment Checklist for ffcadmin.org

Use this checklist when deploying the Cloudflare automation for ffcadmin.org.

## Pre-Deployment

### Cloudflare Setup
- [x] Cloudflare account created
- [x] Domain `ffcadmin.org` added to Cloudflare
- [x] API token created with permissions:
  - Zone → DNS → Edit
  - Zone → Zone Settings → Edit
  - Zone → Zone → Read
- [x] API token scoped to `ffcadmin.org`
- [ ] Nameservers updated at domain registrar (if not already done)
- [ ] Cloudflare zone status is "Active"

### Local Environment
- [ ] Terraform installed (v1.6.0+)
- [ ] Git repository cloned
- [ ] Working directory: `FFC-Cloudflare-Automation-`

### Configuration File
- [x] `terraform.tfvars` created with:
  - Cloudflare API token
  - Domain: `ffcadmin.org`
  - GitHub Pages domain: `freeforcharity.github.io`
- [x] Configuration file NOT committed to git (.gitignore excludes it)

## Deployment Steps

### 1. Initialize Terraform
```bash
terraform init
```
- [ ] Provider plugins downloaded
- [ ] Working directory initialized
- [ ] No errors shown

### 2. Validate Configuration
```bash
terraform validate
```
- [ ] Configuration is valid
- [ ] No syntax errors

### 3. Review Plan
```bash
terraform plan
```
- [ ] Plan shows resources to be created
- [ ] 4 A records for GitHub Pages IPs
- [ ] 1 CNAME record for www subdomain
- [ ] 1 Page rule for HTTPS redirect
- [ ] SSL/TLS zone settings
- [ ] No unexpected changes
- [ ] Review output carefully

### 4. Apply Configuration
```bash
terraform apply
```
- [ ] Review plan one more time
- [ ] Type `yes` to confirm
- [ ] Wait for completion (30-60 seconds)
- [ ] No errors occurred
- [ ] Resources created successfully

### 5. Verify Outputs
```bash
terraform output
```
- [ ] Zone ID displayed
- [ ] Zone name is `ffcadmin.org`
- [ ] Zone status is `active`
- [ ] A records created (4 records)
- [ ] CNAME record created
- [ ] Nameservers displayed

## Post-Deployment

### DNS Verification
- [ ] Wait 5-10 minutes for initial DNS propagation
- [ ] Test A records: `dig ffcadmin.org A +short`
  - Should show 4 GitHub IPs
- [ ] Test CNAME: `dig www.ffcadmin.org CNAME +short`
  - Should show `freeforcharity.github.io`
- [ ] Check Cloudflare dashboard shows records

### GitHub Pages Configuration
- [ ] Navigate to GitHub repository settings
- [ ] Go to Pages section
- [ ] Add custom domain: `ffcadmin.org`
- [ ] Click Save
- [ ] Wait for DNS verification (10-30 minutes)
- [ ] DNS check shows green checkmark ✅
- [ ] Enable "Enforce HTTPS"
- [ ] Wait for SSL certificate provisioning (up to 24 hours)

### Website Testing
- [ ] Visit `http://ffcadmin.org`
  - Redirects to HTTPS
- [ ] Visit `https://ffcadmin.org`
  - Site loads correctly
  - SSL certificate valid
  - No security warnings
- [ ] Visit `http://www.ffcadmin.org`
  - Redirects to HTTPS
  - Site loads correctly
- [ ] Test from multiple devices/networks

### SSL/TLS Verification
- [ ] HTTPS works without warnings
- [ ] SSL certificate is valid
- [ ] Certificate issued by Let's Encrypt or Cloudflare
- [ ] TLS 1.2+ is enforced
- [ ] Test with: `curl -I https://ffcadmin.org`
- [ ] Optional: Run SSL Labs test (https://ssllabs.com/ssltest/)

## Documentation

- [ ] Save Terraform outputs for reference
- [ ] Document any custom configurations
- [ ] Note the deployment date
- [ ] Update team on completion

## Backup and State Management

- [ ] Terraform state file created (`.tfstate`)
- [ ] State file NOT committed to git (excluded by .gitignore)
- [ ] Consider backing up state file securely
- [ ] Optional: Set up remote state (Terraform Cloud, S3, etc.)

## Monitoring

- [ ] Add Cloudflare dashboard to bookmarks
- [ ] Set up Cloudflare email notifications (optional)
- [ ] Monitor GitHub Pages status
- [ ] Check site accessibility regularly

## Rollback Plan

If something goes wrong:

```bash
# Remove all resources
terraform destroy
```

Then investigate the issue before reapplying.

## Common Issues and Solutions

### Issue: Plan fails with "zone not found"
**Solution:**
- Verify domain exists in Cloudflare account
- Check API token has access to the zone
- Confirm domain name spelling in terraform.tfvars

### Issue: Apply fails with permission error
**Solution:**
- Verify API token permissions
- Ensure token hasn't expired
- Check token is scoped correctly

### Issue: DNS not resolving
**Solution:**
- Wait longer (DNS propagation takes time)
- Check nameservers at domain registrar
- Verify records in Cloudflare dashboard

### Issue: GitHub DNS check fails
**Solution:**
- Wait 15-30 minutes for propagation
- Remove and re-add custom domain
- Verify all 4 A records exist
- Check records point to correct IPs

## Maintenance

### Regular Tasks
- [ ] Check SSL certificate expiration (auto-renewed by GitHub)
- [ ] Monitor Cloudflare analytics
- [ ] Review DNS records periodically
- [ ] Keep Terraform configuration updated

### Updating Configuration
If you need to change settings:

1. Edit `terraform.tfvars`
2. Run `terraform plan`
3. Review changes
4. Run `terraform apply`

### Updating Terraform
Keep Terraform and providers up to date:

```bash
terraform init -upgrade
```

## Success Criteria

Deployment is successful when:
- ✅ All Terraform resources created
- ✅ DNS resolves to GitHub Pages IPs
- ✅ GitHub DNS check passed
- ✅ HTTPS enabled and working
- ✅ Site accessible at all URLs
- ✅ SSL certificate valid
- ✅ No browser security warnings

## Timeline

**Typical deployment timeline:**
- Terraform apply: 30-60 seconds
- DNS propagation: 5-30 minutes
- GitHub DNS verification: 10-30 minutes
- SSL certificate provisioning: 1-24 hours
- **Total: Plan for 1-2 hours minimum**

## Support Contacts

- GitHub Pages docs: https://docs.github.com/en/pages
- Cloudflare docs: https://developers.cloudflare.com/
- Terraform docs: https://www.terraform.io/docs
- Repository issues: https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues

## Deployment Sign-off

**Deployed by:** ___________________

**Date:** ___________________

**Time:** ___________________

**Terraform version:** ___________________

**Cloudflare provider version:** ___________________

**Notes:** 
_______________________________________________
_______________________________________________
_______________________________________________

**Verified by:** ___________________

**Verification date:** ___________________

---

✅ **Deployment Complete!**

The ffcadmin.org domain is now configured to work with GitHub Pages through Cloudflare.
