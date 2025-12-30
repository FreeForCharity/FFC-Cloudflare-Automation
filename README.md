# FFC-Cloudflare-Automation-

Automation utilities for Cloudflare tasks supporting Free For Charity.

## One-Time DNS Update Tool

Script: `update_dns.py` updates or creates the `A` record for `staging.clarkemoyer.com`.

### Requirements

- Python 3.9+ (tested locally)
- Install dependencies:

```powershell
python -m venv .venv; .\.venv\Scripts\activate; pip install -r requirements.txt
```

### Usage

Provide a new IPv4 address for the staging subdomain. Supply the Cloudflare API token either via environment variable, command-line argument, or interactive prompt.

```powershell
# Option 1: Prompt for token
python update_dns.py --ip 203.0.113.42

# Option 2: Environment variable
$env:CLOUDFLARE_API_TOKEN = "cf_api_token_value"
python update_dns.py --ip 203.0.113.42

# Option 3: Explicit argument
python update_dns.py --ip 203.0.113.42 --token cf_api_token_value

# Enable Cloudflare proxy (orange cloud)
python update_dns.py --ip 203.0.113.42 --proxied

# Dry run (no changes, shows intended payload)
python update_dns.py --ip 203.0.113.42 --dry-run --proxied
```

### Behavior

Behavior (Python & PowerShell scripts):

- Finds zone ID for `clarkemoyer.com`.
- Fetches all existing `A` records for `staging.clarkemoyer.com` (supports multiple records).
	- Each record with differing IP or differing proxied status is updated.
	- Records already matching requested IP and proxied state are left unchanged.
	- If no records exist, one is created.
- TTL fixed at 120 seconds.
- Add `--proxied` (Python) or `-Proxied` (PowerShell) to turn on Cloudflare proxy (orange cloud).

### PowerShell Variant

Script: `Update-StagingDns.ps1` mirrors Python functionality (multi-record, dry-run, proxy flag).

```powershell
# Prompt for token
./Update-StagingDns.ps1 -NewIp 203.0.113.42

# With environment token & proxy
$env:CLOUDFLARE_API_TOKEN = "cf_api_token_value"
./Update-StagingDns.ps1 -NewIp 203.0.113.42 -Proxied

# Dry run
./Update-StagingDns.ps1 -NewIp 203.0.113.42 -DryRun -Proxied
```

### Token Scope Recommendation

Use a Cloudflare API Token with the minimal permissions (e.g. DNS:Edit for the target zone) rather than the global key.

### Safety

The token is never logged. Dry-run mode allows inspection before changes. All matching A records for the FQDN are processed.

## GitHub Pages Custom Domain (ffcworkingsite1.org)

Use `update_pages_dns.py` to configure Cloudflare DNS so `ffcworkingsite1.org` points to your GitHub Pages site via CNAME records (Cloudflare CNAME flattening supports apex CNAME).

### Steps
- Decide the GitHub Pages host to target (e.g., `freeforcharity.github.io` for org/user Pages).
- Run the script with your Cloudflare API token.

```powershell
# Install deps and activate venv if needed
python -m venv .venv; .\.venv\Scripts\activate; pip install -r requirements.txt

# Set token and perform a dry run
$env:CLOUDFLARE_API_TOKEN = "<your_cf_api_token>"
python update_pages_dns.py --pages-host freeforcharity.github.io --dry-run

# Apply changes
python update_pages_dns.py --pages-host freeforcharity.github.io
```

This will:
- Create/Update `ffcworkingsite1.org` CNAME -> `freeforcharity.github.io` (proxied=false)
- Create/Update `www.ffcworkingsite1.org` CNAME -> `freeforcharity.github.io` (proxied=false)

### Configure GitHub Pages
- In your repository: Settings → Pages → Custom domain → enter `ffcworkingsite1.org`.
- Ensure the repo contains a `CNAME` file with `ffcworkingsite1.org` (GitHub may add it automatically).

### Verify
```powershell
nslookup ffcworkingsite1.org
nslookup www.ffcworkingsite1.org
```
Propagation may take several minutes. If GitHub Pages reports DNS check warnings, wait and retry.

## DNS Summary Export

Use `export_zone_dns_summary.py` to export a CSV summarizing apex A/AAAA and `www` CNAME details for specific zones. This tool is friendly to DNS-only tokens by accepting explicit zone names.

### Usage

```powershell
# Activate venv (if not already)
python -m venv .venv; .\.venv\Scripts\activate; pip install -r requirements.txt

# Provide your token via env (prefers CLOUDFLARE_API_KEY_READ_ALL, then CLOUDFLARE_API_KEY_DNS_ONLY)
$env:CLOUDFLARE_API_KEY_READ_ALL = "<read_all_token>"  # can list zones
# or
$env:CLOUDFLARE_API_KEY_DNS_ONLY = "<dns_only_token>"  # needs explicit zones/IDs

# Export for selected zones
python export_zone_dns_summary.py --zones ffcworkingsite1.org,freedomrisingusa.org,legioninthewoods.org,pagbooster.org --output zone_dns_summary.csv

# Or read zones from a file (one zone per line)
python export_zone_dns_summary.py --zones-file .\zones.txt --output zone_dns_summary.csv

# Export for all zones if your token can read zones
python export_zone_dns_summary.py --all-zones --output zone_dns_summary.csv

# If your token cannot read zone details, provide zone IDs directly
# (Get zone ID from Cloudflare Dashboard → Zone Overview)
python export_zone_dns_summary.py --zones ffcworkingsite1.org --zone-ids ffcworkingsite1.org=<zone_id> --output zone_dns_summary.csv
python export_zone_dns_summary.py --zones-file .\zones.txt --zone-id-file .\zone_ids.csv --output zone_dns_summary.csv
```

### CSV Columns
- `zone`: zone name
- `apex_a_ips`: semicolon-separated apex A IPs
- `apex_a_ttls`: semicolon-separated TTLs for apex A
- `apex_a_proxied`: semicolon-separated proxied flags (true/false)
- `apex_aaaa_ips`: semicolon-separated apex AAAA IPs
- `apex_aaaa_ttls`: semicolon-separated TTLs for apex AAAA
- `apex_aaaa_proxied`: semicolon-separated proxied flags
- `www_cname_target`: CNAME target for `www`
- `www_cname_ttl`: TTL for `www` CNAME
- `www_cname_proxied`: proxied flag for `www` CNAME
- `other_a_count`: count of non-apex A records
- `other_aaaa_count`: count of non-apex AAAA records
- `other_cname_count`: count of non-apex/`www` CNAME records

If your token lacks permission to list all zones, supply explicit zones with `--zones`/`--zones-file`.

### GitHub Actions
- Secret: set `CLOUDFLARE_API_KEY_READ_ALL` (preferred) or `CLOUDFLARE_API_KEY_DNS_ONLY`.
- Workflow: `DNS Summary Export`.
	- Provide `zones` input to target specific zones, or set `all_zones=true` to export everything accessible to the token.
	- The workflow prefers `CLOUDFLARE_API_KEY_READ_ALL` and falls back to `CLOUDFLARE_API_KEY_DNS_ONLY`.

