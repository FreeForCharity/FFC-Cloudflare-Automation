#!/usr/bin/env python3
"""
Cloudflare DNS Compliance Audit Tool

Checks DNS configuration for compliance with FFC standards:
- Microsoft 365 email setup (MX, SPF, DMARC)
- GitHub Pages configuration (A records, WWW CNAME)

Usage:
  python audit_compliance.py --zone example.org
  python audit_compliance.py --zone example.org --token YOUR_TOKEN
"""

import argparse
import os
import sys
from typing import List, Dict
import requests

API_BASE = "https://api.cloudflare.com/client/v4"


class CloudflareError(Exception):
    pass


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def api_get(path: str, token: str, params: dict = None) -> dict:
    r = requests.get(f"{API_BASE}{path}", headers=_headers(token), params=params, timeout=30)
    if not r.ok:
        raise CloudflareError(f"GET {path} failed: {r.status_code} {r.text}")
    data = r.json()
    if not data.get("success", False):
        raise CloudflareError(f"GET {path} returned error: {data}")
    return data


def get_zone_id(token: str, domain: str) -> str:
    data = api_get("/zones", token, params={"name": domain})
    result = data.get("result", [])
    if not result:
        raise CloudflareError(f"Zone for {domain} not found")
    return result[0]["id"]


def get_all_records(token: str, zone_id: str) -> List[Dict]:
    """Fetch all DNS records for a zone."""
    all_records = []
    page = 1
    per_page = 100
    
    while True:
        data = api_get(f"/zones/{zone_id}/dns_records", token, params={"per_page": per_page, "page": page})
        records = data.get("result", [])
        all_records.extend(records)
        
        result_info = data.get("result_info", {})
        if page >= result_info.get("total_pages", 1):
            break
        page += 1
    
    return all_records


def audit_zone(token: str, zone: str) -> int:
    """
    Run compliance audit on a zone.
    Returns 0 if all checks pass, 1 if any checks fail.
    """
    print(f"Running Compliance Audit for Zone: {zone}")
    print("=" * 60)
    
    zone_id = get_zone_id(token, zone)
    records = get_all_records(token, zone_id)
    
    issues_found = 0
    
    # 1. Microsoft 365 MX Record
    print("\n1. Microsoft 365 MX Record")
    mx_records = [r for r in records if r.get("type") == "MX" and "mail.protection.outlook.com" in r.get("content", "")]
    if mx_records:
        print(f"   ✓ [OK] M365 MX Record found ({mx_records[0]['content']})")
    else:
        print("   ✗ [MISSING] M365 MX Record (*.mail.protection.outlook.com)")
        issues_found += 1
    
    # 2. SPF Record
    print("\n2. Microsoft 365 SPF Record")
    spf_records = [r for r in records if r.get("type") == "TXT" and "include:spf.protection.outlook.com" in r.get("content", "")]
    if spf_records:
        print("   ✓ [OK] M365 SPF Record found")
    else:
        print("   ✗ [MISSING] M365 SPF Record (include:spf.protection.outlook.com)")
        issues_found += 1
    
    # 3. DMARC Record
    print("\n3. DMARC Record")
    dmarc_records = [r for r in records if r.get("type") == "TXT" and r.get("name", "").startswith(f"_dmarc.{zone}")]
    if dmarc_records:
        print(f"   ✓ [OK] DMARC Record found")
    else:
        print(f"   ✗ [MISSING] DMARC Record (_dmarc.{zone})")
        issues_found += 1
    
    # 4. GitHub Pages A Records
    print("\n4. GitHub Pages A Records")
    gh_ips = ['185.199.108.153', '185.199.109.153', '185.199.110.153', '185.199.111.153']
    apex_a_records = [r for r in records if r.get("type") == "A" and r.get("name") == zone]
    apex_ips = [r.get("content") for r in apex_a_records]
    missing_ips = [ip for ip in gh_ips if ip not in apex_ips]
    
    if not missing_ips and len(apex_a_records) >= 4:
        print("   ✓ [OK] GitHub Pages A Records found")
    else:
        print(f"   ✗ [MISSING/PARTIAL] GitHub Pages A Records")
        if missing_ips:
            print(f"      Missing IPs: {', '.join(missing_ips)}")
        issues_found += 1
    
    # 5. WWW CNAME Record
    print("\n5. WWW CNAME Record")
    www_records = [r for r in records if r.get("type") == "CNAME" and r.get("name") == f"www.{zone}"]
    if www_records:
        print(f"   ✓ [OK] WWW CNAME found ({www_records[0]['content']})")
    else:
        print("   ✗ [MISSING] WWW CNAME record")
        issues_found += 1
    
    print("\n" + "=" * 60)
    if issues_found == 0:
        print("✓ All compliance checks passed!")
        return 0
    else:
        print(f"✗ {issues_found} compliance issue(s) found")
        return 1


def retrieve_token(arg_token: str = None) -> str:
    if arg_token:
        return arg_token.strip()
    env_token = os.getenv("CLOUDFLARE_API_KEY_DNS_ONLY")
    if env_token:
        return env_token.strip()
    print("Error: Cloudflare API token required", file=sys.stderr)
    print("Set CLOUDFLARE_API_KEY_DNS_ONLY environment variable or use --token argument", file=sys.stderr)
    sys.exit(2)


def main():
    parser = argparse.ArgumentParser(
        description="Cloudflare DNS Compliance Audit Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--zone", required=True, help="Domain/zone name (e.g., 'example.org')")
    parser.add_argument("--token", help="Cloudflare API token (if omitted, env var used)")
    
    args = parser.parse_args()
    token = retrieve_token(args.token)
    
    try:
        exit_code = audit_zone(token, args.zone)
        sys.exit(exit_code)
    except CloudflareError as e:
        print(f"Cloudflare API error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
