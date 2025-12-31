#!/usr/bin/env python3
"""
Cloudflare DNS Standard Enforcement Tool

Enforces FFC standard DNS configuration:
- Microsoft 365 email setup (MX, SPF, DMARC)
- GitHub Pages configuration (A records, WWW CNAME)

Usage:
  python enforce_standard.py --zone example.org --dry-run
  python enforce_standard.py --zone example.org  # Apply changes
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


def api_post(path: str, token: str, payload: dict) -> dict:
    r = requests.post(f"{API_BASE}{path}", headers=_headers(token), json=payload, timeout=30)
    if not r.ok:
        raise CloudflareError(f"POST {path} failed: {r.status_code} {r.text}")
    data = r.json()
    if not data.get("success", False):
        raise CloudflareError(f"POST {path} returned error: {data}")
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


def ensure_record(token: str, zone_id: str, zone: str, record_def: Dict, all_records: List[Dict], dry_run: bool) -> bool:
    """
    Ensure a DNS record exists with the specified configuration.
    Returns True if record was created/needed, False if already exists.
    """
    rec_type = record_def["type"]
    rec_name = zone if record_def["name"] == "@" else f"{record_def['name']}.{zone}"
    rec_content = record_def["content"]
    
    # Check if record exists
    matches = [r for r in all_records if r.get("type") == rec_type and r.get("name") == rec_name]
    
    if rec_type in ["MX", "TXT", "A"]:
        # Multi-value types - check for exact match
        exact_match = [r for r in matches if r.get("content") == rec_content]
        if rec_type == "MX":
            exact_match = [r for r in exact_match if r.get("priority") == record_def.get("priority", 10)]
        
        if exact_match:
            print(f"   [OK] {rec_type} {record_def['name']} already exists")
            return False
    else:
        # Single value types (CNAME) - check if any exists
        if matches and matches[0].get("content") == rec_content:
            print(f"   [OK] {rec_type} {record_def['name']} already exists")
            return False
    
    # Record doesn't exist - create it
    if dry_run:
        print(f"   [DRY-RUN] Would create {rec_type} {record_def['name']} -> {rec_content}")
        return True
    
    print(f"   [CREATE] Creating {rec_type} {record_def['name']} -> {rec_content}...", end="")
    
    payload = {
        "type": rec_type,
        "name": rec_name,
        "content": rec_content,
        "ttl": 1  # Auto
    }
    
    if rec_type == "MX":
        payload["priority"] = record_def.get("priority", 10)
    
    if rec_type in ["A", "CNAME"]:
        payload["proxied"] = record_def.get("proxied", True)
    
    try:
        api_post(f"/zones/{zone_id}/dns_records", token, payload)
        print(" ✓ Done")
        return True
    except CloudflareError as e:
        print(f" ✗ Failed: {e}")
        raise


def enforce_standard(token: str, zone: str, dry_run: bool = True):
    """
    Enforce FFC standard DNS configuration on a zone.
    """
    mode = "DRY-RUN MODE" if dry_run else "LIVE MODE"
    print(f"Enforcing FFC Standard Configuration for Zone: {zone} ({mode})")
    print("=" * 70)
    
    zone_id = get_zone_id(token, zone)
    all_records = get_all_records(token, zone_id)
    
    # Define standard records
    standards = [
        # Microsoft 365 Email
        {"type": "MX", "name": "@", "content": "freeforcharity-org.mail.protection.outlook.com", "priority": 0},
        {"type": "TXT", "name": "@", "content": "v=spf1 include:spf.protection.outlook.com -all"},
        {"type": "TXT", "name": "_dmarc", "content": "v=DMARC1; p=none; rua=mailto:dmarc-rua@freeforcharity.org"},
        
        # GitHub Pages (Apex)
        {"type": "A", "name": "@", "content": "185.199.108.153", "proxied": False},
        {"type": "A", "name": "@", "content": "185.199.109.153", "proxied": False},
        {"type": "A", "name": "@", "content": "185.199.110.153", "proxied": False},
        {"type": "A", "name": "@", "content": "185.199.111.153", "proxied": False},
        
        # GitHub Pages (WWW)
        {"type": "CNAME", "name": "www", "content": zone, "proxied": False}
    ]
    
    changes_needed = 0
    
    for std in standards:
        if ensure_record(token, zone_id, zone, std, all_records, dry_run):
            changes_needed += 1
    
    print("\n" + "=" * 70)
    if changes_needed == 0:
        print("✓ All standard records are in place")
    elif dry_run:
        print(f"⚠ {changes_needed} change(s) needed (run without --dry-run to apply)")
    else:
        print(f"✓ {changes_needed} change(s) applied successfully")


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
        description="Cloudflare DNS Standard Enforcement Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--zone", required=True, help="Domain/zone name (e.g., 'example.org')")
    parser.add_argument("--token", help="Cloudflare API token (if omitted, env var used)")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    
    args = parser.parse_args()
    token = retrieve_token(args.token)
    
    try:
        enforce_standard(token, args.zone, args.dry_run)
    except CloudflareError as e:
        print(f"\nCloudflare API error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"\nNetwork error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
