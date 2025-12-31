# Staging Subdomain DNS Management

This guide explains how to update DNS records for the `staging.clarkemoyer.com` subdomain.

## Quick Start for Staging Updates

### PowerShell Script (Recommended)

**Requirements:**

- PowerShell 5.1+ (Windows) or PowerShell 7+ (cross-platform)

**Update staging A record:**

```powershell
# Basic usage (will prompt for API token)
.\Update-StagingDns.ps1 -NewIp 203.0.113.42

# With environment variable
$env:CLOUDFLARE_API_TOKEN = "your_token_here"
.\Update-StagingDns.ps1 -NewIp 203.0.113.42

# Enable Cloudflare proxy
.\Update-StagingDns.ps1 -NewIp 203.0.113.42 -Proxied

# Dry run
.\Update-StagingDns.ps1 -NewIp 203.0.113.42 -DryRun
```

### General DNS Management Script

For more flexibility, use the comprehensive DNS management script:

```powershell
# Update staging subdomain
.\Update-CloudflareDns.ps1 -Zone clarkemoyer.com -Name staging -Type A -Content 203.0.113.42

# With environment variable
$env:CLOUDFLARE_API_TOKEN = "your_token_here"
.\Update-CloudflareDns.ps1 -Zone clarkemoyer.com -Name staging -Type A -Content 203.0.113.42

# Enable Cloudflare proxy (orange cloud)
.\Update-CloudflareDns.ps1 -Zone clarkemoyer.com -Name staging -Type A -Content 203.0.113.42 -Proxied

# Dry run (preview changes)
.\Update-CloudflareDns.ps1 -Zone clarkemoyer.com -Name staging -Type A -Content 203.0.113.42 -DryRun
```

## Behavior

The scripts:

- Find the zone ID for `clarkemoyer.com`
- Fetch all existing A records for `staging.clarkemoyer.com`
- Update records with differing IP or proxy status
- Leave unchanged records as-is
- Create a new record if none exist
- Set TTL to 120 seconds (for `Update-StagingDns.ps1`) or Auto (for `Update-CloudflareDns.ps1`)
- Support Cloudflare proxy (orange cloud) via `-Proxied` flag

## Advanced Operations

### List Records

```powershell
.\Update-CloudflareDns.ps1 -Zone clarkemoyer.com -Name staging -Type A -List
```

### Delete a Specific Record

```powershell
.\Update-CloudflareDns.ps1 -Zone clarkemoyer.com -Name staging -Type A -Content 203.0.113.42 -Remove
```

### Update CNAME Record

```powershell
.\Update-CloudflareDns.ps1 -Zone clarkemoyer.com -Name staging -Type CNAME -Content example.com
```

## Security

- API tokens are never logged
- Tokens can be provided via environment variable to avoid command-line exposure
- Use Cloudflare API tokens with minimal permissions (DNS:Edit for the target zone)
- Dry-run mode allows inspection before making changes

## Multiple Records

If multiple A records exist for `staging.clarkemoyer.com`:

- Each record with a different IP or proxy status is updated
- Records already matching the requested IP and proxy state are left unchanged
- All matching records are processed
