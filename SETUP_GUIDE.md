# Setup Guide: Cloudflare + GitHub Pages

This guide walks you through the complete process of setting up a custom domain with Cloudflare and GitHub Pages using this Terraform automation.

## Part 1: Prerequisites Setup

### 1.1 Install Terraform

**macOS (using Homebrew):**
```bash
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
```

**Windows (using Chocolatey):**
```powershell
choco install terraform
```

**Linux (Ubuntu/Debian):**
```bash
wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install terraform
```

**Verify Installation:**
```bash
terraform --version
```

### 1.2 Cloudflare Account Setup

1. **Create/Login to Cloudflare Account**
   - Visit: https://dash.cloudflare.com
   - Sign up or log in

2. **Add Your Domain to Cloudflare**
   - Click "Add a Site"
   - Enter your domain name
   - Select a plan (Free plan works fine)
   - Follow the instructions to update your domain's nameservers

3. **Wait for DNS Propagation**
   - Cloudflare will scan your existing DNS records
   - Update your domain registrar with Cloudflare's nameservers
   - Wait for nameserver changes to propagate (can take up to 24 hours)

### 1.3 Create Cloudflare API Token

1. Go to: https://dash.cloudflare.com/profile/api-tokens
2. Click "Create Token"
3. Use "Edit zone DNS" template or create custom token with:
   - **Permissions:**
     - Zone → DNS → Edit
     - Zone → Zone Settings → Edit
     - Zone → Zone → Read
   - **Zone Resources:**
     - Include → Specific zone → [Your Domain]
4. Click "Continue to summary"
5. Click "Create Token"
6. **Save the token securely** - you won't be able to see it again!

## Part 2: GitHub Pages Setup

### 2.1 Create a GitHub Pages Repository

If you don't have one yet:

1. Create a new repository named `username.github.io` (replace username with your GitHub username)
2. Add your website files (at minimum an `index.html`)
3. Commit and push your files

### 2.2 Enable GitHub Pages

1. Go to your repository settings
2. Navigate to "Pages" section
3. Under "Source", select the branch you want to publish (usually `main` or `master`)
4. Click "Save"
5. Wait for the site to build and deploy

### 2.3 Note Your GitHub Pages Domain

Your GitHub Pages domain will be one of:
- `username.github.io` (for user/organization sites)
- `username.github.io/repository-name` (for project sites)

For custom domains, you'll use the base domain: `username.github.io`

## Part 3: Run Terraform Automation

### 3.1 Clone and Configure

```bash
# Clone this repository
git clone https://github.com/FreeForCharity/FFC-Cloudflare-Automation-.git
cd FFC-Cloudflare-Automation-

# Copy example configuration
cp terraform.tfvars.example terraform.tfvars

# Edit configuration (use your preferred editor)
nano terraform.tfvars
# or
vim terraform.tfvars
# or
code terraform.tfvars
```

### 3.2 Edit terraform.tfvars

Set these required values:

```hcl
cloudflare_api_token = "your-api-token-from-step-1.3"
domain_name         = "yourdomain.com"
github_pages_domain = "username.github.io"
```

**Example:**
```hcl
cloudflare_api_token = "abc123xyz789..."
domain_name         = "freeforcharity.org"
github_pages_domain = "freeforcharity.github.io"
```

### 3.3 Initialize Terraform

```bash
terraform init
```

Expected output:
```
Initializing the backend...
Initializing provider plugins...
- Finding cloudflare/cloudflare versions matching "~> 4.0"...
...
Terraform has been successfully initialized!
```

### 3.4 Plan Changes

```bash
terraform plan
```

Review the output. You should see:
- 4 A records to be created
- 1 CNAME record to be created (if create_www_record = true)
- SSL/TLS settings to be configured
- Page rules to be created (if create_https_redirect = true)

### 3.5 Apply Configuration

```bash
terraform apply
```

- Review the planned changes
- Type `yes` when prompted
- Wait for completion (usually takes 30-60 seconds)

### 3.6 Verify Outputs

```bash
terraform output
```

You should see:
- Zone ID
- Zone name
- Created A records
- Created CNAME record
- Nameservers
- Zone status

## Part 4: Configure GitHub Custom Domain

### 4.1 Add Custom Domain in GitHub

1. Go to your GitHub repository settings
2. Navigate to "Pages" section
3. Under "Custom domain", enter your domain (e.g., `yourdomain.com`)
4. Click "Save"

### 4.2 Wait for DNS Check

GitHub will verify the DNS records:
- This can take 10-15 minutes
- You'll see a checkmark when verification succeeds
- If it fails, wait longer or check DNS propagation

### 4.3 Enable HTTPS

Once DNS is verified:
1. Check the "Enforce HTTPS" box in GitHub Pages settings
2. Wait for GitHub to provision an SSL certificate (can take up to 24 hours)

### 4.4 Create CNAME File (if needed)

For project sites, create a file named `CNAME` in your repository root:

```bash
echo "yourdomain.com" > CNAME
git add CNAME
git commit -m "Add custom domain"
git push
```

## Part 5: Verification and Testing

### 5.1 Test DNS Resolution

```bash
# Test A records (should show GitHub IPs)
dig yourdomain.com A +short

# Test CNAME record
dig www.yourdomain.com CNAME +short

# Or use nslookup
nslookup yourdomain.com
nslookup www.yourdomain.com
```

Expected A record results:
```
185.199.108.153
185.199.109.153
185.199.110.153
185.199.111.153
```

Expected CNAME result:
```
username.github.io.
```

### 5.2 Test in Browser

1. Visit `http://yourdomain.com`
2. Should redirect to `https://yourdomain.com`
3. Should show your GitHub Pages site
4. Check SSL certificate (should be valid)

### 5.3 Test www Subdomain

1. Visit `http://www.yourdomain.com`
2. Should redirect to `https://www.yourdomain.com` or `https://yourdomain.com`
3. Should show your GitHub Pages site

### 5.4 Check SSL Grade

Use SSL Labs to check your SSL configuration:
- Visit: https://www.ssllabs.com/ssltest/
- Enter your domain
- Wait for analysis (takes a few minutes)
- Aim for an A or A+ grade

## Part 6: Troubleshooting

### Issue: DNS check failing in GitHub

**Solutions:**
1. Wait longer (DNS propagation can take time)
2. Check DNS records in Cloudflare dashboard
3. Verify nameservers are pointing to Cloudflare
4. Use `dig` or online tools to verify DNS records
5. Try removing and re-adding the custom domain in GitHub

### Issue: SSL certificate not provisioning

**Solutions:**
1. Ensure DNS is fully propagated (wait 24 hours)
2. Check that CAA records aren't blocking Let's Encrypt
3. Remove and re-add custom domain in GitHub
4. Make sure HTTPS redirect is not enabled until after SSL is provisioned

### Issue: Site not loading

**Solutions:**
1. Clear browser cache
2. Try incognito/private browsing mode
3. Check GitHub Pages build status
4. Verify repository is public (or you have GitHub Pro for private repos)
5. Check that index.html exists in your repository

### Issue: Terraform errors

**Solutions:**
1. Verify API token has correct permissions
2. Check that domain exists in Cloudflare
3. Ensure zone is active (not pending)
4. Run `terraform plan` to see detailed error messages

## Part 7: Maintenance

### Updating Configuration

To change settings:

```bash
# Edit terraform.tfvars
nano terraform.tfvars

# Plan changes
terraform plan

# Apply changes
terraform apply
```

### Viewing Current State

```bash
# Show all resources
terraform show

# Show specific outputs
terraform output zone_id
terraform output nameservers
```

### Removing Configuration

If you need to remove all settings:

```bash
terraform destroy
```

**Warning:** This removes all DNS records and settings created by Terraform.

## Part 8: Next Steps

### Performance Optimization

1. **Enable Cloudflare Proxy**
   - Set `proxied = true` in terraform.tfvars
   - Benefit from Cloudflare CDN
   - Get DDoS protection

2. **Configure Caching**
   - Set up Page Rules for cache TTL
   - Configure browser cache settings

3. **Enable Additional Security**
   - Enable DNSSEC in Cloudflare
   - Configure firewall rules
   - Set up rate limiting

### Monitoring

1. **Cloudflare Analytics**
   - Monitor traffic in Cloudflare dashboard
   - Check for security threats
   - View performance metrics

2. **GitHub Pages Status**
   - Monitor build status
   - Check deployment logs
   - Set up status checks

### Backup

1. **Export Terraform State**
   ```bash
   terraform state pull > backup.tfstate
   ```

2. **Version Control**
   - Keep terraform files in git
   - Document all changes
   - Tag releases

## Support and Resources

- **GitHub Pages Docs:** https://docs.github.com/en/pages
- **Cloudflare Docs:** https://developers.cloudflare.com/
- **Terraform Docs:** https://www.terraform.io/docs
- **Issues:** https://github.com/FreeForCharity/FFC-Cloudflare-Automation-/issues

## Quick Reference

### Terraform Commands
```bash
terraform init        # Initialize working directory
terraform plan        # Preview changes
terraform apply       # Apply changes
terraform destroy     # Remove resources
terraform show        # Show current state
terraform output      # Show outputs
terraform validate    # Validate configuration
terraform fmt         # Format configuration files
```

### DNS Troubleshooting Tools
```bash
dig domain.com A              # Check A records
dig www.domain.com CNAME      # Check CNAME
nslookup domain.com           # Check DNS resolution
whois domain.com | grep -i ns # Check nameservers
```

### File Checklist
- [ ] terraform.tfvars configured
- [ ] Cloudflare API token created
- [ ] Domain added to Cloudflare
- [ ] Nameservers updated
- [ ] GitHub Pages site created
- [ ] Terraform applied successfully
- [ ] Custom domain added in GitHub
- [ ] DNS verified in GitHub
- [ ] HTTPS enabled
- [ ] Site accessible at custom domain

Congratulations! Your custom domain should now be fully configured with Cloudflare and GitHub Pages.
