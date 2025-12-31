# Staging Subdomain DNS Management

This guide explains how to update DNS records for the `staging.clarkemoyer.com` subdomain.

## Quick Start for Staging Updates

### Python Script (Recommended)

**Requirements:**
- Python 3.9+
- Install dependencies: `pip install -r requirements.txt`

**Update staging A record:**
```bash
# Basic usage (will prompt for API token)
python update_dns.py --name staging --type A --ip 203.0.113.42

# With environment variable
export CLOUDFLARE_API_TOKEN="your_token_here"
python update_dns.py --name staging --type A --ip 203.0.113.42

# With explicit token
python update_dns.py --name staging --type A --ip 203.0.113.42 --token your_token_here

# Enable Cloudflare proxy (orange cloud)
python update_dns.py --name staging --type A --ip 203.0.113.42 --proxied

# Dry run (preview changes)
python update_dns.py --name staging --type A --ip 203.0.113.42 --dry-run
```

### PowerShell Script (Alternative)

**Requirements:**
- PowerShell 5.1+

**Update staging A record:**
```powershell
# Basic usage (will prompt for API token)
./Update-StagingDns.ps1 -NewIp 203.0.113.42

# With environment variable
$env:CLOUDFLARE_API_TOKEN = "your_token_here"
./Update-StagingDns.ps1 -NewIp 203.0.113.42

# Enable Cloudflare proxy
./Update-StagingDns.ps1 -NewIp 203.0.113.42 -Proxied

# Dry run
./Update-StagingDns.ps1 -NewIp 203.0.113.42 -DryRun
```

## Behavior

Both scripts:
- Find the zone ID for `clarkemoyer.com`
- Fetch all existing A records for `staging.clarkemoyer.com`
- Update records with differing IP or proxy status
- Leave unchanged records as-is
- Create a new record if none exist
- Set TTL to 120 seconds
- Support Cloudflare proxy (orange cloud) via `--proxied` or `-Proxied` flag

## Advanced Operations

### Search for Records
```bash
python update_dns.py --name staging --type A --search
```

### Delete a Specific Record
```bash
python update_dns.py --record-id abc123xyz --delete
```

### Update CNAME Record
```bash
python update_dns.py --name staging --type CNAME --target example.com
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
