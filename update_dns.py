#!/usr/bin/env python3
"""Flexible Cloudflare DNS updater for managing DNS records across multiple domains

Usage examples:
  python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42
  python update_dns.py --zone example.org --name www --type CNAME --target example.org
  python update_dns.py --zone example.org --name @ --type AAAA --ip 2606:50c0:8000::153
  python update_dns.py --zone example.org --name @ --type MX --content mail.protection.outlook.com --priority 0
  python update_dns.py --zone example.org --name @ --type TXT --content "v=spf1 include:spf.protection.outlook.com -all"
  python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42 --dry-run
  python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42 --no-proxy

Prompts for Cloudflare API token if not provided via env var CLOUDFLARE_API_KEY_DNS_ONLY or --token argument.

Exits with non-zero status on failure.
"""
import argparse
import getpass
import ipaddress
import os
import sys
import textwrap
from typing import Optional, List

import requests

API_BASE = "https://api.cloudflare.com/client/v4"

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

def api_delete(path: str, token: str) -> dict:
    r = requests.delete(f"{API_BASE}{path}", headers=_headers(token), timeout=30)
    if not r.ok:
        raise CloudflareError(f"DELETE {path} failed: {r.status_code} {r.text}")
    data = r.json()
    if not data.get("success", False):
        raise CloudflareError(f"DELETE {path} returned error: {data}")
    return data

def get_zone_id(token: str, domain: str) -> str:
    data = api_get("/zones", token, params={"name": domain})
    result = data.get("result", [])
    if not result:
        raise CloudflareError(f"Zone for {domain} not found")
    return result[0]["id"]

def get_dns_records(token: str, zone_id: str, name: str, record_type: str = "A") -> List[dict]:
    data = api_get(f"/zones/{zone_id}/dns_records", token, params={"type": record_type, "name": name})
    return data.get("result", [])

def delete_dns_record(token: str, zone_id: str, record_id: str, dry_run: bool) -> dict:
    """Delete a DNS record by ID."""
    if dry_run:
        return {"message": "DRY-RUN: Would delete record", "record_id": record_id}
    result = api_delete(f"/zones/{zone_id}/dns_records/{record_id}", token)
    return {"message": "Record deleted", "record_id": record_id, "result": result}

def update_or_create_cname(token: str, zone_id: str, name: str, target: str, dry_run: bool, proxied: bool) -> dict:
    """Update or create CNAME record(s)."""
    existing_records = get_dns_records(token, zone_id, name, record_type="CNAME")
    results = []
    payload_template = {
        "type": "CNAME",
        "name": name,
        "content": target,
        "ttl": 120,
        "proxied": proxied,
    }
    if existing_records:
        for rec in existing_records:
            needs_target_change = rec.get("content") != target
            needs_proxy_change = rec.get("proxied") != proxied
            if not needs_target_change and not needs_proxy_change:
                results.append({"id": rec.get("id"), "status": "unchanged", "old_target": rec.get("content"), "proxied": rec.get("proxied")})
                continue
            if dry_run:
                results.append({"id": rec.get("id"), "status": "dry-run", "proposed": payload_template, "old_target": rec.get("content"), "old_proxied": rec.get("proxied")})
                continue
            updated = api_patch(f"/zones/{zone_id}/dns_records/{rec.get('id')}", token, payload_template)
            updated_rec = updated.get("result", {})
            results.append({"id": rec.get("id"), "status": "updated", "new_target": updated_rec.get("content"), "old_target": rec.get("content"), "new_proxied": updated_rec.get("proxied"), "old_proxied": rec.get("proxied")})
        return {"message": "Existing CNAME records processed", "count": len(existing_records), "details": results}
    else:
        if dry_run:
            return {"message": "DRY-RUN would CREATE new CNAME record", "proposed": payload_template}
        created = api_post(f"/zones/{zone_id}/dns_records", token, payload_template)
        return {"message": "CNAME record created", "result": created.get("result")}

def update_or_create_records(token: str, zone_id: str, name: str, new_ip: str, dry_run: bool, proxied: bool, record_type: str = "A") -> dict:
    """Update or create A or AAAA records."""
    existing_records = get_dns_records(token, zone_id, name, record_type=record_type)
    results = []
    payload_template = {
        "type": record_type,
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
            return {"message": f"DRY-RUN would CREATE new {record_type} record", "proposed": payload_template}
        created = api_post(f"/zones/{zone_id}/dns_records", token, payload_template)
        return {"message": f"{record_type} record created", "result": created.get("result")}


def update_or_create_mx_txt(token: str, zone_id: str, name: str, content: str, dry_run: bool, record_type: str, priority: int = 10) -> dict:
    """Update or create MX or TXT records (multi-value records)."""
    existing_records = get_dns_records(token, zone_id, name, record_type=record_type)
    
    payload_template = {
        "type": record_type,
        "name": name,
        "content": content,
        "ttl": 1,  # Auto
    }
    
    if record_type == "MX":
        payload_template["priority"] = priority
    
    # Check for exact match
    exact_match = None
    for rec in existing_records:
        if rec.get("content") == content:
            if record_type != "MX" or rec.get("priority") == priority:
                exact_match = rec
                break
    
    if exact_match:
        return {"message": f"{record_type} record already exists with exact content", "id": exact_match.get("id"), "status": "unchanged"}
    
    # No exact match - create new record (multi-value records allow multiple)
    if dry_run:
        return {"message": f"DRY-RUN would CREATE new {record_type} record", "proposed": payload_template}
    
    created = api_post(f"/zones/{zone_id}/dns_records", token, payload_template)
    return {"message": f"{record_type} record created", "result": created.get("result")}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Cloudflare DNS record management tool for multiple domains",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """Examples:
  # Update A record
  python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42
  
  # Update AAAA record (IPv6)
  python update_dns.py --zone example.org --name @ --type AAAA --ip 2606:50c0:8000::153
  
  # Update CNAME record
  python update_dns.py --zone example.org --name www --type CNAME --target example.org
  
  # Create MX record
  python update_dns.py --zone example.org --name @ --type MX --content mail.protection.outlook.com --priority 0
  
  # Create TXT record
  python update_dns.py --zone example.org --name @ --type TXT --content "v=spf1 include:spf.protection.outlook.com -all"
  
  # Search for records
  python update_dns.py --zone example.org --name staging --type A --search
  
  # Delete a specific record
  python update_dns.py --zone example.org --record-id abc123 --delete
  
  # Dry run
  python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42 --dry-run
  
  # Disable proxy (DNS only mode)
  python update_dns.py --zone example.org --name staging --type A --ip 203.0.113.42 --no-proxy"""
        ),
    )
    parser.add_argument("--zone", required=True, help="Domain/zone name (e.g., 'example.org')")
    parser.add_argument("--name", help="Record name (use '@' for apex/root, or subdomain like 'staging')")
    parser.add_argument("--type", choices=["A", "AAAA", "CNAME", "MX", "TXT"], help="DNS record type")
    parser.add_argument("--ip", help="IPv4 address for A records or IPv6 address for AAAA records")
    parser.add_argument("--target", help="Target domain for CNAME records")
    parser.add_argument("--content", help="Content for MX or TXT records")
    parser.add_argument("--priority", type=int, default=10, help="Priority for MX records (default: 10)")
    parser.add_argument("--record-id", help="Specific record ID for deletion")
    parser.add_argument("--search", action="store_true", help="Search and display existing records")
    parser.add_argument("--delete", action="store_true", help="Delete the specified record")
    parser.add_argument("--token", help="Cloudflare API token (if omitted, env var or prompt used)")
    parser.add_argument("--dry-run", action="store_true", help="Show intended changes without applying")
    parser.add_argument("--proxied", action="store_true", help="Enable Cloudflare proxy (orange cloud) on the record(s)")
    parser.add_argument("--no-proxy", action="store_true", help="Explicitly disable Cloudflare proxy (DNS only mode)")
    return parser.parse_args()


def retrieve_token(arg_token: Optional[str]) -> str:
    if arg_token:
        return arg_token.strip()
    # Prefer repo-level DNS-only var, then standard token
    env_token = os.getenv("CLOUDFLARE_API_KEY_DNS_ONLY") or os.getenv("CLOUDFLARE_API_TOKEN")
    if env_token:
        return env_token.strip()
    return getpass.getpass("Enter Cloudflare API Token: ").strip()


def validate_ip(ip: str, record_type: str) -> None:
    """Validate IP address based on record type."""
    try:
        if record_type == "A":
            ipaddress.IPv4Address(ip)
        elif record_type == "AAAA":
            ipaddress.IPv6Address(ip)
    except (ipaddress.AddressValueError, ValueError) as e:
        raise SystemExit(f"Invalid {record_type} address '{ip}': {e}")


def main():
    args = parse_args()
    
    # Validate arguments
    if args.delete:
        if not args.record_id:
            print("--record-id required for deletion", file=sys.stderr)
            sys.exit(2)
    elif args.search:
        if not args.name or not args.type:
            print("--name and --type required for search", file=sys.stderr)
            sys.exit(2)
    else:
        # Update/create operation
        if not args.name or not args.type:
            print("--name and --type required for update/create", file=sys.stderr)
            sys.exit(2)
        if args.type in ["A", "AAAA"] and not args.ip:
            print(f"--ip required for {args.type} records", file=sys.stderr)
            sys.exit(2)
        if args.type == "CNAME" and not args.target:
            print("--target required for CNAME records", file=sys.stderr)
            sys.exit(2)
        if args.type in ["MX", "TXT"] and not args.content:
            print(f"--content required for {args.type} records", file=sys.stderr)
            sys.exit(2)
        if args.type in ["A", "AAAA"]:
            validate_ip(args.ip, args.type)
    
    # Handle --no-proxy flag (takes precedence over --proxied)
    if args.no_proxy and args.proxied:
        print("--no-proxy and --proxied are mutually exclusive", file=sys.stderr)
        sys.exit(2)
    
    proxied = args.proxied and not args.no_proxy
    
    token = retrieve_token(args.token)
    if not token:
        print("Cloudflare API token required", file=sys.stderr)
        sys.exit(2)
    
    try:
        zone_id = get_zone_id(token, args.zone)
        # Handle @ for apex domain or construct full name
        if args.name == "@":
            full_name = args.zone
        elif args.name:
            full_name = f"{args.name}.{args.zone}"
        else:
            full_name = None
        
        # Handle deletion
        if args.delete:
            result = delete_dns_record(token, zone_id, args.record_id, args.dry_run)
            print(result["message"])
            print(f"Record ID: {result['record_id']}")
            return
        
        # Handle search
        if args.search:
            records = get_dns_records(token, zone_id, full_name, args.type)
            if not records:
                print(f"No {args.type} records found for {full_name}")
            else:
                print(f"Found {len(records)} {args.type} record(s) for {full_name}:")
                for rec in records:
                    print(f"  ID: {rec.get('id')}")
                    print(f"  Name: {rec.get('name')}")
                    print(f"  Type: {rec.get('type')}")
                    print(f"  Content: {rec.get('content')}")
                    print(f"  Proxied: {rec.get('proxied')}")
                    print(f"  TTL: {rec.get('ttl')}")
                    print()
            return
        
        # Handle update/create
        if args.type in ["A", "AAAA"]:
            result = update_or_create_records(token, zone_id, full_name, args.ip, args.dry_run, proxied, args.type)
        elif args.type == "CNAME":
            result = update_or_create_cname(token, zone_id, full_name, args.target, args.dry_run, proxied)
        elif args.type in ["MX", "TXT"]:
            result = update_or_create_mx_txt(token, zone_id, full_name, args.content, args.dry_run, args.type, args.priority)
        
        print(result["message"])
        if "result" in result:
            r = result["result"]
            print(f"Created record: id={r.get('id')} name={r.get('name')} content={r.get('content')} proxied={r.get('proxied')}")
        elif "details" in result:
            print(f"Processed {result.get('count')} existing record(s):")
            for d in result["details"]:
                status = d.get("status")
                if status == "unchanged":
                    content_key = "old_ip" if "old_ip" in d else "old_target"
                    print(f"  id={d['id']} unchanged content={d.get(content_key)} proxied={d['proxied']}")
                elif status == "dry-run":
                    old_content = d.get("old_ip") or d.get("old_target")
                    print(f"  id={d['id']} DRY-RUN old_content={old_content} -> new_content={d['proposed']['content']} old_proxied={d['old_proxied']} -> new_proxied={d['proposed']['proxied']}")
                elif status == "updated":
                    old_content = d.get("old_ip") or d.get("old_target")
                    new_content = d.get("new_ip") or d.get("new_target")
                    print(f"  id={d['id']} updated {old_content} -> {new_content} proxied {d['old_proxied']} -> {d['new_proxied']}")
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
