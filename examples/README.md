# Configuration Examples

This directory contains example Terraform variable files for different use cases.

## Available Examples

### 1. Basic Configuration (`basic.tfvars`)

Minimal setup with default settings. Good for getting started quickly.

**Use case:** Simple GitHub Pages site with standard configuration

```bash
terraform apply -var-file=examples/basic.tfvars
```

### 2. With Cloudflare CDN (`with-cdn.tfvars`)

Enables Cloudflare's CDN proxying for improved performance and DDoS protection.

**Use case:** Production site that needs global CDN and security features

**Features:**
- Traffic proxied through Cloudflare
- Automatic caching
- DDoS protection
- Web application firewall (on paid plans)

```bash
terraform apply -var-file=examples/with-cdn.tfvars
```

**Note:** When using proxied mode, some GitHub Pages features may behave differently.

### 3. Strict SSL (`strict-ssl.tfvars`)

Maximum security configuration with strict SSL/TLS settings.

**Use case:** Sites that require high security standards and latest TLS protocols

**Features:**
- Strict SSL mode
- TLS 1.3 minimum
- CAA records for certificate management
- HTTPS enforcement

```bash
terraform apply -var-file=examples/strict-ssl.tfvars
```

### 4. Apex Domain Only (`apex-only.tfvars`)

Configuration for root domain only, without www subdomain.

**Use case:** Sites that only want to use example.com (not www.example.com)

**Features:**
- No www subdomain CNAME
- All traffic to root domain
- Standard SSL settings

```bash
terraform apply -var-file=examples/apex-only.tfvars
```

## Using Examples

### Method 1: Direct Application

```bash
# Copy example and modify
cp examples/basic.tfvars terraform.tfvars
nano terraform.tfvars

# Apply
terraform apply
```

### Method 2: Using -var-file Flag

```bash
# Apply without creating terraform.tfvars
terraform apply -var-file=examples/basic.tfvars
```

### Method 3: Multiple Environments

```bash
# Create environment-specific files
cp examples/basic.tfvars prod.tfvars
cp examples/with-cdn.tfvars staging.tfvars

# Apply to specific environment
terraform apply -var-file=prod.tfvars
```

## Customization

All examples are starting points. Customize them based on your needs:

1. Copy an example that matches your use case
2. Modify variables as needed
3. Test with `terraform plan`
4. Apply with `terraform apply`

## Common Customizations

### Change GitHub Pages IPs

If GitHub updates their IP addresses:

```hcl
github_pages_ips = [
  "new.ip.1",
  "new.ip.2",
  "new.ip.3",
  "new.ip.4"
]
```

### Custom DNS TTL

For faster DNS updates during testing:

```hcl
dns_ttl = 120  # 2 minutes
```

For production (better performance):

```hcl
dns_ttl = 1  # Automatic (Cloudflare decides)
```

### Disable HTTPS Redirect

If you're testing without SSL:

```hcl
create_https_redirect = false
```

### Custom SSL Mode

Options: `off`, `flexible`, `full`, `strict`

```hcl
ssl_mode = "flexible"  # For sites without HTTPS backend
ssl_mode = "full"      # Standard for GitHub Pages
ssl_mode = "strict"    # Requires valid certificate
```

## Comparison Table

| Feature | Basic | With CDN | Strict SSL | Apex Only |
|---------|-------|----------|------------|-----------|
| www subdomain | ✅ | ✅ | ✅ | ❌ |
| Cloudflare CDN | ❌ | ✅ | ❌ | ❌ |
| HTTPS redirect | ✅ | ✅ | ✅ | ✅ |
| SSL mode | full | full | strict | full |
| Min TLS | 1.2 | 1.2 | 1.3 | 1.2 |
| CAA records | ❌ | ❌ | ✅ | ❌ |

## Need Help?

- See [README.md](../README.md) for variable reference
- Check [SETUP_GUIDE.md](../SETUP_GUIDE.md) for detailed setup
- See [QUICK_START.md](../QUICK_START.md) for fast setup

## Contributing

Have a useful configuration example? Submit a PR!

Guidelines:
- Add clear comments
- Include use case description
- Test the configuration
- Update this README
