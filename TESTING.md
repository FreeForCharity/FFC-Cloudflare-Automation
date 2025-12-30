# Testing Guide for ffcadmin.org

This guide helps you test the Cloudflare automation for the ffcadmin.org domain.

## Prerequisites

You have:
- ✅ Cloudflare API token (provided separately, not shown in documentation)
- ✅ Domain: `ffcadmin.org`
- ✅ Terraform configuration files ready

## Step 1: Verify Prerequisites

### Check Terraform Installation

```bash
terraform version
```

Expected output: `Terraform v1.0.0` or later

### Verify Configuration File

The `terraform.tfvars` file should be created with:
- API token for ffcadmin.org (obtain from your Cloudflare account)
- Domain name set to ffcadmin.org
- GitHub Pages domain set to freeforcharity.github.io

⚠️ **Security Note**: The API token should be stored securely in `terraform.tfvars` which is excluded from git by `.gitignore`. Never commit API tokens to version control.

Check the file exists:

```bash
ls -la terraform.tfvars
```

## Step 2: Initialize Terraform

```bash
cd /path/to/FFC-Cloudflare-Automation-
terraform init
```

Expected output:
```
Initializing the backend...
Initializing provider plugins...
- Finding cloudflare/cloudflare versions matching "~> 4.0"...
- Installing cloudflare/cloudflare...
Terraform has been successfully initialized!
```

## Step 3: Validate Configuration

```bash
terraform validate
```

Expected output:
```
Success! The configuration is valid.
```

## Step 4: Preview Changes

```bash
terraform plan
```

This will show you what Terraform will create:

**Expected resources to be created:**
1. Data source lookup for zone `ffcadmin.org`
2. 4 A records for GitHub Pages:
   - `ffcadmin.org` → `185.199.108.153`
   - `ffcadmin.org` → `185.199.109.153`
   - `ffcadmin.org` → `185.199.110.153`
   - `ffcadmin.org` → `185.199.111.153`
3. 1 CNAME record:
   - `www.ffcadmin.org` → `freeforcharity.github.io`
4. Page rule for HTTPS redirect
5. Zone settings for SSL/TLS

**Expected output:**
```
Plan: 6 to add, 0 to change, 0 to destroy.
```

## Step 5: Apply Configuration

If the plan looks correct:

```bash
terraform apply
```

Review the planned changes and type `yes` to confirm.

**Expected output:**
```
Apply complete! Resources: 6 added, 0 changed, 0 destroyed.

Outputs:

github_pages_a_records = [
  {
    "content" = "185.199.108.153"
    "id" = "..."
    "name" = "@"
    "type" = "A"
  },
  ...
]
github_pages_www_record = {
  "content" = "freeforcharity.github.io"
  "id" = "..."
  "name" = "www"
  "type" = "CNAME"
}
nameservers = [
  "ns1.cloudflaressl.com",
  "ns2.cloudflaressl.com"
]
zone_id = "..."
zone_name = "ffcadmin.org"
status = "active"
```

## Step 6: Verify DNS Records

### Using dig

```bash
dig ffcadmin.org A +short
```

Expected output:
```
185.199.108.153
185.199.109.153
185.199.110.153
185.199.111.153
```

```bash
dig www.ffcadmin.org CNAME +short
```

Expected output:
```
freeforcharity.github.io.
```

### Using nslookup

```bash
nslookup ffcadmin.org
nslookup www.ffcadmin.org
```

### Using Cloudflare Dashboard

1. Log in to Cloudflare dashboard
2. Select the `ffcadmin.org` domain
3. Go to DNS → Records
4. Verify the records were created

## Step 7: Configure GitHub Pages

### In GitHub Repository Settings

1. Go to your Free For Charity GitHub repository
2. Navigate to **Settings** → **Pages**
3. Under **Custom domain**, enter: `ffcadmin.org`
4. Click **Save**
5. Wait for DNS check (can take 10-15 minutes)
6. Once verified, check **Enforce HTTPS**

### Create CNAME File (if needed)

In your repository root:

```bash
echo "ffcadmin.org" > CNAME
git add CNAME
git commit -m "Add custom domain ffcadmin.org"
git push
```

## Step 8: Test the Site

### Test HTTP → HTTPS Redirect

```bash
curl -I http://ffcadmin.org
```

Expected: `HTTP/1.1 301` or `308` redirecting to HTTPS

### Test HTTPS

```bash
curl -I https://ffcadmin.org
```

Expected: `HTTP/1.1 200 OK` or `301` to www version

### Test in Browser

1. Visit: `http://ffcadmin.org`
2. Should redirect to `https://ffcadmin.org`
3. Should show your GitHub Pages site
4. SSL certificate should be valid

### Test www Subdomain

1. Visit: `http://www.ffcadmin.org`
2. Should redirect to HTTPS
3. Should show your site

## Troubleshooting

### Issue: "Error listing zones"

**Solution:** Check API token permissions
```bash
# Verify the token in terraform.tfvars
grep cloudflare_api_token terraform.tfvars
```

### Issue: "Zone not found"

**Possible causes:**
1. Domain not added to Cloudflare account
2. API token not scoped for this domain
3. Typo in domain name

**Solution:** Verify domain in Cloudflare dashboard

### Issue: DNS not propagating

**Solution:** Wait longer (up to 24 hours) or check propagation:
```bash
# Check multiple DNS servers
dig @8.8.8.8 ffcadmin.org A +short
dig @1.1.1.1 ffcadmin.org A +short
```

Online tool: https://dnschecker.org

### Issue: GitHub DNS check failing

**Solutions:**
1. Wait 15-30 minutes for DNS propagation
2. Verify all 4 A records exist
3. Remove and re-add custom domain in GitHub
4. Check nameservers are pointing to Cloudflare

### Issue: SSL certificate not working

**Solutions:**
1. Wait up to 24 hours for provisioning
2. Ensure `ssl_mode = "full"` in terraform.tfvars
3. Verify HTTPS is enabled in GitHub Pages
4. Check certificate using: `openssl s_client -connect ffcadmin.org:443`

## Verification Checklist

- [ ] Terraform initialized successfully
- [ ] Terraform plan shows expected changes
- [ ] Terraform apply completed without errors
- [ ] DNS A records resolving to GitHub IPs
- [ ] DNS CNAME record pointing to freeforcharity.github.io
- [ ] Custom domain added in GitHub Pages
- [ ] GitHub DNS check passed
- [ ] HTTPS enabled in GitHub Pages
- [ ] Site accessible at http://ffcadmin.org (redirects to HTTPS)
- [ ] Site accessible at https://ffcadmin.org
- [ ] Site accessible at www.ffcadmin.org
- [ ] SSL certificate valid and working

## View Current State

```bash
# See all created resources
terraform show

# See specific outputs
terraform output zone_id
terraform output nameservers
terraform output github_pages_a_records
terraform output github_pages_www_record
```

## Make Changes

If you need to modify settings:

1. Edit `terraform.tfvars`
2. Run `terraform plan` to preview changes
3. Run `terraform apply` to apply changes

## Remove Resources

To delete all Cloudflare settings:

```bash
terraform destroy
```

⚠️ **Warning:** This removes all DNS records and settings created by Terraform.

## Support

- Check [README.md](README.md) for general documentation
- See [SETUP_GUIDE.md](SETUP_GUIDE.md) for detailed setup
- Open an issue on GitHub for problems

## Success Indicators

When everything is working:
1. `terraform apply` completes successfully
2. DNS resolves correctly (dig/nslookup)
3. GitHub shows "DNS check successful" ✅
4. HTTPS lock icon appears in browser 🔒
5. Site loads at ffcadmin.org
6. SSL certificate is valid

Expected total time: 10-30 minutes (including DNS propagation)
