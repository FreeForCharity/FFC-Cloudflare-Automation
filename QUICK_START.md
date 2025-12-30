# Quick Start Guide

Get your GitHub Pages custom domain up and running in 5 minutes!

## Prerequisites Checklist

- [ ] Terraform installed (1.6+)
- [ ] Cloudflare account with domain added
- [ ] Cloudflare API token created
- [ ] GitHub Pages site ready

## 5-Step Setup

### 1. Clone and Configure

```bash
git clone https://github.com/FreeForCharity/FFC-Cloudflare-Automation-.git
cd FFC-Cloudflare-Automation-
cp terraform.tfvars.example terraform.tfvars
```

### 2. Edit Configuration

Edit `terraform.tfvars`:

```hcl
cloudflare_api_token = "YOUR_API_TOKEN"
domain_name         = "yourdomain.com"
github_pages_domain = "username.github.io"
```

### 3. Initialize Terraform

```bash
terraform init
```

### 4. Apply Configuration

```bash
terraform apply
```

Type `yes` when prompted.

### 5. Configure GitHub

1. Go to repository Settings → Pages
2. Add custom domain: `yourdomain.com`
3. Wait for DNS verification
4. Enable "Enforce HTTPS"

## Verify

```bash
# Check DNS
dig yourdomain.com A +short

# Should show:
# 185.199.108.153
# 185.199.109.153
# 185.199.110.153
# 185.199.111.153

# Test in browser
curl -I https://yourdomain.com
```

## Troubleshooting

**DNS not working?**
- Wait 10-15 minutes for propagation
- Check Cloudflare dashboard for records
- Verify nameservers: `dig yourdomain.com NS +short`

**GitHub DNS check failing?**
- Ensure all 4 A records are created
- Wait longer (up to 24 hours)
- Try removing and re-adding domain

**SSL not working?**
- Wait for GitHub to provision certificate
- Check HTTPS is enabled in GitHub Pages
- Verify `ssl_mode = "full"` in terraform.tfvars

## Next Steps

- Read the [full README](README.md) for advanced options
- Check [SETUP_GUIDE.md](SETUP_GUIDE.md) for detailed walkthrough
- Configure additional settings in `terraform.tfvars`

## Common Commands

```bash
terraform plan      # Preview changes
terraform apply     # Apply changes
terraform output    # View outputs
terraform destroy   # Remove all resources
```

## Need Help?

- Check [SETUP_GUIDE.md](SETUP_GUIDE.md) for detailed instructions
- See [README.md](README.md) for troubleshooting
- Open an issue on GitHub

---

**Total time: ~5-10 minutes** (plus DNS propagation time)
