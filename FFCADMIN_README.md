# ffcadmin.org Cloudflare Setup

This document provides specific instructions for deploying the Cloudflare automation for the **ffcadmin.org** domain.

## Quick Deploy

Your configuration is already set up! Just run:

```bash
# Navigate to the repository
cd FFC-Cloudflare-Automation-

# Initialize Terraform
terraform init

# Preview what will be created
terraform plan

# Apply the configuration
terraform apply
```

Type `yes` when prompted to create the resources.

## What This Will Do

The automation will configure your Cloudflare DNS to point **ffcadmin.org** to GitHub Pages:

### DNS Records Created:
1. **A Records** (root domain):
   - `ffcadmin.org` → `185.199.108.153`
   - `ffcadmin.org` → `185.199.109.153`
   - `ffcadmin.org` → `185.199.110.153`
   - `ffcadmin.org` → `185.199.111.153`

2. **CNAME Record** (www subdomain):
   - `www.ffcadmin.org` → `freeforcharity.github.io`

### Cloudflare Settings:
- **SSL/TLS Mode**: Full (secure)
- **Minimum TLS Version**: 1.2
- **Always Use HTTPS**: Enabled
- **Automatic HTTPS Rewrites**: Enabled
- **HTTPS Redirect**: Page rule created

## After Terraform Apply

Once Terraform completes successfully:

### 1. Wait for DNS Propagation (5-10 minutes)

Test DNS resolution:
```bash
dig ffcadmin.org A +short
dig www.ffcadmin.org CNAME +short
```

### 2. Configure GitHub Pages

1. Go to your GitHub repository (freeforcharity)
2. Navigate to **Settings** → **Pages**
3. Under **Custom domain**, enter: `ffcadmin.org`
4. Click **Save**
5. Wait for DNS verification (green checkmark ✅)
6. Enable **Enforce HTTPS**

### 3. Create CNAME File in Repository

Add a CNAME file to your GitHub repository:

```bash
echo "ffcadmin.org" > CNAME
git add CNAME
git commit -m "Add custom domain ffcadmin.org"
git push
```

### 4. Test Your Site

```bash
# Test HTTP redirect
curl -I http://ffcadmin.org

# Test HTTPS
curl -I https://ffcadmin.org

# Test www subdomain
curl -I https://www.ffcadmin.org
```

Visit in browser:
- http://ffcadmin.org (should redirect to HTTPS)
- https://ffcadmin.org (should load your site)
- https://www.ffcadmin.org (should work)

## Configuration Details

Your `terraform.tfvars` file contains:

```hcl
cloudflare_api_token = "your-cloudflare-api-token-here"
domain_name          = "ffcadmin.org"
github_pages_domain  = "freeforcharity.github.io"

dns_ttl              = 1     # Automatic
proxied              = false # Direct to GitHub
create_www_record    = true  # Include www subdomain

# SSL/TLS settings
configure_ssl_settings     = true
ssl_mode                   = "full"
always_use_https           = true
automatic_https_rewrites   = true
min_tls_version            = "1.2"
create_https_redirect      = true
```

⚠️ **Security Note**: 
- The `terraform.tfvars` file contains your actual API token
- This file is NOT committed to git (protected by .gitignore)
- The actual API token should be obtained from your Cloudflare account or team lead
- Never commit API tokens to version control

## Troubleshooting

### DNS Not Working?

Check if DNS has propagated:
```bash
dig @8.8.8.8 ffcadmin.org A +short
```

Or use: https://dnschecker.org

### GitHub DNS Check Failing?

1. Wait 15-30 minutes for DNS to fully propagate
2. Remove and re-add the custom domain in GitHub
3. Verify all 4 A records exist in Cloudflare

### SSL Certificate Issues?

GitHub may take up to 24 hours to provision the SSL certificate. In the meantime:
- Don't enable "Enforce HTTPS" yet
- Wait for the certificate to be issued
- Check back in a few hours

### Need to Make Changes?

Edit `terraform.tfvars` and run:
```bash
terraform plan
terraform apply
```

### Need to Start Over?

Remove all resources:
```bash
terraform destroy
```

## Current Status

- ✅ Terraform configuration created
- ✅ Variables configured for ffcadmin.org
- ✅ API token configured and secured
- ✅ Configuration validated
- ⏳ Ready to deploy with `terraform apply`

## Timeline

**Expected deployment time:**
- Terraform apply: 30-60 seconds
- DNS propagation: 5-30 minutes
- GitHub DNS verification: 10-30 minutes
- SSL certificate: 1-24 hours
- **Total: Plan for 1-2 hours**

## Support

- **Full Documentation**: See [README.md](README.md)
- **Step-by-Step Guide**: See [SETUP_GUIDE.md](SETUP_GUIDE.md)
- **Testing Guide**: See [TESTING.md](TESTING.md)
- **Deployment Checklist**: See [DEPLOYMENT_CHECKLIST.md](DEPLOYMENT_CHECKLIST.md)

## Commands Reference

```bash
# Initialize
terraform init

# Check configuration
terraform validate

# Preview changes
terraform plan

# Apply configuration
terraform apply

# View outputs
terraform output

# View current state
terraform show

# Destroy resources
terraform destroy
```

## Success Indicators

✅ Deployment successful when:
1. `terraform apply` completes without errors
2. DNS resolves to GitHub IPs
3. GitHub DNS check shows green checkmark
4. HTTPS enabled in GitHub Pages
5. Site loads at https://ffcadmin.org
6. SSL certificate is valid

## Next Steps After Successful Deployment

1. **Test the site** thoroughly from multiple devices
2. **Monitor Cloudflare analytics** for traffic
3. **Set up GitHub Pages** build/deploy process
4. **Update content** in your GitHub repository
5. **Share the URL** with your team

## Additional Features

Want to enable more features? Edit `terraform.tfvars`:

### Enable Cloudflare CDN
```hcl
proxied = true
```

### Use Stricter SSL
```hcl
ssl_mode = "strict"
min_tls_version = "1.3"
```

### Add CAA Records
```hcl
create_caa_records = true
```

Then run `terraform apply` to update.

---

**Ready to deploy?** Run `terraform apply` now! 🚀
