#!/usr/bin/env python3
"""One-time Cloudflare DNS updater for staging.clarkemoyer.com

Usage examples:
  python update_dns.py --ip 203.0.113.42
  CLOUDFLARE_API_TOKEN=tokenvalue python update_dns.py --ip 203.0.113.42
  python update_dns.py --ip 203.0.113.42 --dry-run

Prompts for Cloudflare API token if not provided via env var CLOUDFLARE_API_TOKEN or --token argument.

Exits with non-zero status on failure.
"""
import argparse
import getpass
import ipaddress
import os
import sys
import textwrap
from typing import Optional

import requests

API_BASE = "https://api.cloudflare.com/client/v4"
ROOT_DOMAIN = "clarkemoyer.com"
SUBDOMAIN = "staging"
FULL_NAME = f"{SUBDOMAIN}.{ROOT_DOMAIN}"

class CloudflareError(Exception):
    pass

def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

def api_get(path: str, token: str, params: Optional[dict] = None) -> dict:
    r = requests.get(f"{API_BASE}{path}", headers=_headers(token), params=params, timeout=30)
    if not r.ok:
        raise CloudflareError(f"GET {path} failed: {r.status_code} {r.text}")
    data = r.json()
    if not data.get("success", False):
        raise CloudflareError(f"GET {path} returned error: {data}")
    return data

def api_patch(path: str, token: str, payload: dict) -> dict:
    r = requests.patch(f"{API_BASE}{path}", headers=_headers(token), json=payload, timeout=30)
    if not r.ok:
        raise CloudflareError(f"PATCH {path} failed: {r.status_code} {r.text}")
    data = r.json()
    if not data.get("success", False):
        raise CloudflareError(f"PATCH {path} returned error: {data}")
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

def get_dns_records(token: str, zone_id: str, name: str, record_type: str = "A") -> list[dict]:
    data = api_get(f"/zones/{zone_id}/dns_records", token, params={"type": record_type, "name": name})
    return data.get("result", [])

def update_or_create_records(token: str, zone_id: str, name: str, new_ip: str, dry_run: bool, proxied: bool) -> dict:
    existing_records = get_dns_records(token, zone_id, name)
    results = []
    payload_template = {
        "type": "A",
        "name": name,
        "content": new_ip,
        "ttl": 120,
        "proxied": proxied,
    }
    if existing_records:
        for rec in existing_records:
            needs_ip_change = rec.get("content") != new_ip
            needs_proxy_change = rec.get("proxied") != proxied
            if not needs_ip_change and not needs_proxy_change:
                results.append({"id": rec.get("id"), "status": "unchanged", "old_ip": rec.get("content"), "proxied": rec.get("proxied")})
                continue
            if dry_run:
                results.append({"id": rec.get("id"), "status": "dry-run", "proposed": payload_template, "old_ip": rec.get("content"), "old_proxied": rec.get("proxied")})
                continue
            updated = api_patch(f"/zones/{zone_id}/dns_records/{rec.get('id')}", token, payload_template)
            updated_rec = updated.get("result", {})
            results.append({"id": rec.get("id"), "status": "updated", "new_ip": updated_rec.get("content"), "old_ip": rec.get("content"), "new_proxied": updated_rec.get("proxied"), "old_proxied": rec.get("proxied")})
        return {"message": "Existing records processed", "count": len(existing_records), "details": results}
    else:
        if dry_run:
            return {"message": "DRY-RUN would CREATE new record", "proposed": payload_template}
        created = api_post(f"/zones/{zone_id}/dns_records", token, payload_template)
        return {"message": "Record created", "result": created.get("result")}


def parse_args():
    parser = argparse.ArgumentParser(
        description="One-time updater for Cloudflare DNS A record of staging.clarkemoyer.com",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """Examples:\n  python update_dns.py --ip 203.0.113.42\n  CLOUDFLARE_API_TOKEN=token python update_dns.py --ip 203.0.113.42\n  python update_dns.py --ip 203.0.113.42 --dry-run"""
        ),
    )
    parser.add_argument("--ip", required=True, help="New IPv4 address for the staging subdomain")
    parser.add_argument("--token", help="Cloudflare API token (if omitted, env var or prompt used)")
    parser.add_argument("--dry-run", action="store_true", help="Show intended changes without applying")
    parser.add_argument("--proxied", action="store_true", help="Enable Cloudflare proxy (orange cloud) on the record(s)")
    return parser.parse_args()


def retrieve_token(arg_token: Optional[str]) -> str:
    if arg_token:
        return arg_token.strip()
    # Prefer repo-level DNS-only var, then standard token
    env_token = os.getenv("CLOUDFLARE_API_KEY_DNS_ONLY") or os.getenv("CLOUDFLARE_API_TOKEN")
    if env_token:
        return env_token.strip()
    return getpass.getpass("Enter Cloudflare API Token: ").strip()


def validate_ip(ip: str) -> None:
    try:
        ipaddress.IPv4Address(ip)
    except Exception as e:
        raise SystemExit(f"Invalid IPv4 address '{ip}': {e}")


def main():
    args = parse_args()
    validate_ip(args.ip)
    token = retrieve_token(args.token)
    if not token:
        print("Cloudflare API token required", file=sys.stderr)
        sys.exit(2)
    try:
        zone_id = get_zone_id(token, ROOT_DOMAIN)
        result = update_or_create_records(token, zone_id, FULL_NAME, args.ip, args.dry_run, args.proxied)
        print(result["message"])
        if "result" in result:
            r = result["result"]
            print(f"Created record: id={r.get('id')} name={r.get('name')} content={r.get('content')} proxied={r.get('proxied')}")
        elif "details" in result:
            print(f"Processed {result.get('count')} existing record(s):")
            for d in result["details"]:
                status = d.get("status")
                if status == "unchanged":
                    print(f"  id={d['id']} unchanged ip={d['old_ip']} proxied={d['proxied']}")
                elif status == "dry-run":
                    print(f"  id={d['id']} DRY-RUN old_ip={d['old_ip']} -> new_ip={d['proposed']['content']} old_proxied={d['old_proxied']} -> new_proxied={d['proposed']['proxied']}")
                elif status == "updated":
                    print(f"  id={d['id']} updated {d['old_ip']} -> {d['new_ip']} proxied {d['old_proxied']} -> {d['new_proxied']}")
        elif "proposed" in result:
            print("Proposed payload:")
            print(result["proposed"])
    except CloudflareError as e:
        print(f"Cloudflare API error: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"Network error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
