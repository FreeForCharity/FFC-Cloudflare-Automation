#!/usr/bin/env python3
"""Cloudflare DNS setup for GitHub Pages custom domain.

Configures `ffcworkingsite1.org` to point to a GitHub Pages site by:
- Setting apex CNAME (flattened) to the GitHub Pages hostname
- Setting `www` CNAME to the same hostname

Notes:
- Cloudflare supports CNAME flattening at the zone apex; keep `proxied` off for Pages.
- Provide your Cloudflare API token via env var `CLOUDFLARE_API_TOKEN` or `--token`.
"""
import argparse
import getpass
import os
from typing import Optional

import requests

API_BASE = "https://api.cloudflare.com/client/v4"
ROOT_DOMAIN = "ffcworkingsite1.org"


class CloudflareError(Exception):
    pass


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def api_get(path: str, token: str, params: Optional[dict] = None) -> dict:
    r = requests.get(f"{API_BASE}{path}", headers=_headers(token), params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data.get("success", False):
        raise CloudflareError(f"GET {path} returned error: {data}")
    return data


def api_post(path: str, token: str, payload: dict) -> dict:
    r = requests.post(f"{API_BASE}{path}", headers=_headers(token), json=payload, timeout=30)
    data = r.json() if r.headers.get("content-type","" ).startswith("application/json") else {"raw": r.text}
    if not r.ok:
        raise CloudflareError(f"POST {path} failed: {r.status_code} {data}")
    if not data.get("success", False):
        raise CloudflareError(f"POST {path} returned error: {data}")
    return data


def api_put(path: str, token: str, payload: dict) -> dict:
    r = requests.put(f"{API_BASE}{path}", headers=_headers(token), json=payload, timeout=30)
    data = r.json() if r.headers.get("content-type","" ).startswith("application/json") else {"raw": r.text}
    if not r.ok:
        raise CloudflareError(f"PUT {path} failed: {r.status_code} {data}")
    if not data.get("success", False):
        raise CloudflareError(f"PUT {path} returned error: {data}")
    return data


def get_zone_id(token: str, domain: str) -> str:
    data = api_get("/zones", token, params={"name": domain})
    result = data.get("result", [])
    if not result:
        raise CloudflareError(f"Zone for {domain} not found")
    return result[0]["id"]


def get_dns_records(token: str, zone_id: str, name: str, record_type: Optional[str] = None) -> list[dict]:
    params = {"name": name}
    if record_type:
        params["type"] = record_type
    data = api_get(f"/zones/{zone_id}/dns_records", token, params=params)
    return data.get("result", [])


def delete_records(token: str, zone_id: str, record_ids: list[str]) -> None:
    for rec_id in record_ids:
        r = requests.delete(f"{API_BASE}/zones/{zone_id}/dns_records/{rec_id}", headers=_headers(token), timeout=30)
        data = r.json() if r.headers.get("content-type","" ).startswith("application/json") else {"raw": r.text}
        if not r.ok or not data.get("success", False):
            raise CloudflareError(f"DELETE dns_records/{rec_id} failed: {r.status_code} {data}")


def create_a_record(token: str, zone_id: str, name: str, ip: str, ttl: int = 300, proxied: bool = False, comment: Optional[str] = None) -> str:
    payload = {"type": "A", "name": name, "content": ip, "ttl": ttl, "proxied": proxied}
    if comment:
        payload["comment"] = comment
    created = api_post(f"/zones/{zone_id}/dns_records", token, payload)
    r = created.get("result", {})
    return f"Created A {r.get('name')} -> {r.get('content')} proxied={r.get('proxied')}"


def create_aaaa_record(token: str, zone_id: str, name: str, ip6: str, ttl: int = 300, proxied: bool = False, comment: Optional[str] = None) -> str:
    payload = {"type": "AAAA", "name": name, "content": ip6, "ttl": ttl, "proxied": proxied}
    if comment:
        payload["comment"] = comment
    created = api_post(f"/zones/{zone_id}/dns_records", token, payload)
    r = created.get("result", {})
    return f"Created AAAA {r.get('name')} -> {r.get('content')} proxied={r.get('proxied')}"


def upsert_cname(token: str, zone_id: str, name: str, target: str, proxied: bool, ttl: int = 300, dry_run: bool = False) -> str:
    existing = get_dns_records(token, zone_id, name, record_type="CNAME")
    payload = {"type": "CNAME", "name": name, "content": target, "ttl": ttl, "proxied": proxied}
    if existing:
        rec_id = existing[0]["id"]
        current = existing[0]
        if current.get("content") == target and current.get("proxied") == proxied and (current.get("ttl") == ttl or current.get("ttl") == 1):
            return f"CNAME {name} already points to {target} (proxied={proxied})"
        if dry_run:
            return f"DRY-RUN: Would update CNAME {name} -> {target} proxied={proxied}"
        updated = api_put(f"/zones/{zone_id}/dns_records/{rec_id}", token, payload)
        r = updated.get("result", {})
        return f"Updated CNAME {r.get('name')} -> {r.get('content')} proxied={r.get('proxied')}"
    else:
        if dry_run:
            return f"DRY-RUN: Would create CNAME {name} -> {target} proxied={proxied}"
        created = api_post(f"/zones/{zone_id}/dns_records", token, payload)
        r = created.get("result", {})
        return f"Created CNAME {r.get('name')} -> {r.get('content')} proxied={r.get('proxied')}"


def retrieve_token(arg_token: Optional[str]) -> str:
    if arg_token:
        return arg_token.strip()
    # Prefer standard env var, fallback to repo-level var
    # Prefer repo-level DNS-only var, then standard token
    env_token = os.getenv("CLOUDFLARE_API_KEY_DNS_ONLY") or os.getenv("CLOUDFLARE_API_TOKEN")
    if env_token:
        return env_token.strip()
    return getpass.getpass("Enter Cloudflare API Token: ").strip()


def parse_args():
    p = argparse.ArgumentParser(description="Configure Cloudflare DNS for GitHub Pages custom domain")
    p.add_argument("--domain", default=ROOT_DOMAIN, help="Root domain to configure (default: ffcworkingsite1.org)")
    p.add_argument("--pages-host", help="GitHub Pages hostname (e.g., username.github.io)")
    p.add_argument("--token", help="Cloudflare API token")
    p.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    p.add_argument("--replace-apex", action="store_true", help="Delete existing A/AAAA apex records before setting CNAME")
    p.add_argument("--purge-other-cnames", action="store_true", help="Delete all other CNAMEs in the zone (except apex and www of the domain)")
    p.add_argument("--set-github-a", action="store_true", help="Replace apex with GitHub Pages A records (185.199.108.153/109/110/111)")
    p.add_argument("--set-github-aaaa", action="store_true", help="Add GitHub Pages AAAA records at apex (2606:50c0:8000::153/8001::153/8002::153/8003::153)")
    p.add_argument("--purge-a-and-cname", action="store_true", help="Delete all A and CNAME records except apex, www, and staging for the domain")
    p.add_argument("--set-apex-cname", action="store_true", help="Set apex CNAME to the Pages host (Cloudflare flattening). Not compatible with A/AAAA.")
    p.add_argument("--list-zones", action="store_true", help="List Cloudflare zones accessible with the provided token")
    # Revert/explicit controls
    p.add_argument("--set-apex-a", help="Set apex A record to the provided IPv4 address (deletes apex CNAME/A/AAAA first)")
    p.add_argument("--clear-apex-aaaa", action="store_true", help="Delete all apex AAAA records")
    p.add_argument("--set-www-cname", help="Set www.<domain> CNAME to the provided hostname")
    return p.parse_args()


def main():
    args = parse_args()
    token = retrieve_token(args.token)
    if not token:
        raise SystemExit("Cloudflare API token required")
    # Optional: list zones and exit
    if args.list_zones:
        print("Accessible zones:")
        page = 1
        per_page = 50
        while True:
            data = api_get("/zones", token, params={"page": page, "per_page": per_page})
            zones = data.get("result", [])
            for z in zones:
                print(f"- {z.get('name')} (id={z.get('id')})")
            info = data.get("result_info", {})
            total_pages = info.get("total_pages") or 1
            if page >= total_pages:
                break
            page += 1
        return
    zone_id = get_zone_id(token, args.domain)
    # If requested, remove existing A/AAAA at apex to allow CNAME
    if args.replace_apex:
        existing_apex_a = get_dns_records(token, zone_id, args.domain, record_type="A")
        existing_apex_aaaa = get_dns_records(token, zone_id, args.domain, record_type="AAAA")
        ids_to_delete = [r["id"] for r in (existing_apex_a + existing_apex_aaaa)]
        if ids_to_delete:
            if args.dry_run:
                print(f"DRY-RUN: Would delete apex records: {ids_to_delete}")
            else:
                delete_records(token, zone_id, ids_to_delete)
                print(f"Deleted apex A/AAAA records: {ids_to_delete}")
    # Set apex CNAME only when explicitly requested and not mixing with A/AAAA
    if args.set_apex_cname and not (args.set_github_a or args.set_github_aaaa):
        if not args.pages_host:
            raise SystemExit("--pages-host is required when setting apex CNAME")
        apex_msg = upsert_cname(token, zone_id, args.domain, args.pages_host, proxied=False, ttl=300, dry_run=args.dry_run)
        print(apex_msg)
    # Ensure www CNAME points to the Pages host if provided
    if args.pages_host:
        www_msg = upsert_cname(token, zone_id, f"www.{args.domain}", args.pages_host, proxied=False, ttl=300, dry_run=args.dry_run)
        print(www_msg)

    # Explicit revert: set apex A to provided IP
    if args.set_apex_a:
        # Remove apex CNAME and existing A/AAAA
        existing_apex_cname = get_dns_records(token, zone_id, args.domain, record_type="CNAME")
        cname_ids = [r["id"] for r in existing_apex_cname]
        existing_apex_a = get_dns_records(token, zone_id, args.domain, record_type="A")
        existing_apex_aaaa = get_dns_records(token, zone_id, args.domain, record_type="AAAA")
        ids_to_delete = cname_ids + [r["id"] for r in (existing_apex_a + existing_apex_aaaa)]
        if ids_to_delete:
            if args.dry_run:
                print(f"DRY-RUN: Would delete apex records before setting A {args.set_apex_a}: {ids_to_delete}")
            else:
                delete_records(token, zone_id, ids_to_delete)
                print(f"Deleted apex records: {ids_to_delete}")
        if args.dry_run:
            print(f"DRY-RUN: Would create A {args.domain} -> {args.set_apex_a} proxied=False")
        else:
            msg = create_a_record(token, zone_id, args.domain, args.set_apex_a, ttl=300, proxied=False)
            print(msg)

    # Explicit: clear apex AAAA
    if args.clear_apex_aaaa:
        existing_apex_aaaa = get_dns_records(token, zone_id, args.domain, record_type="AAAA")
        ids_to_delete = [r["id"] for r in existing_apex_aaaa]
        if ids_to_delete:
            if args.dry_run:
                print(f"DRY-RUN: Would delete apex AAAA records: {ids_to_delete}")
            else:
                delete_records(token, zone_id, ids_to_delete)
                print(f"Deleted apex AAAA records: {ids_to_delete}")

    # Explicit: set www CNAME to provided host
    if args.set_www_cname:
        www_msg = upsert_cname(token, zone_id, f"www.{args.domain}", args.set_www_cname, proxied=False, ttl=300, dry_run=args.dry_run)
        print(www_msg)
    # Optionally purge other CNAMEs in the zone
    if args.purge_other_cnames:
        all_cnames = get_dns_records(token, zone_id, name=args.domain.split('.')[0], record_type="CNAME")
        # Fetch all CNAMEs regardless of name
        data = api_get(f"/zones/{zone_id}/dns_records", token, params={"type": "CNAME"})
        others = []
        keep = {args.domain, f"www.{args.domain}"}
        for rec in data.get("result", []):
            if rec.get("name") not in keep:
                others.append(rec.get("id"))
        if others:
            if args.dry_run:
                print(f"DRY-RUN: Would delete other CNAMEs: {others}")
            else:
                delete_records(token, zone_id, others)
                print(f"Deleted other CNAMEs: {others}")
    # Optionally purge A and CNAME records except apex, www, and staging
    if args.purge_a_and_cname:
        keep_names = {args.domain, f"www.{args.domain}", f"staging.{args.domain}"}
        # Purge A records
        a_data = api_get(f"/zones/{zone_id}/dns_records", token, params={"type": "A"})
        a_to_delete = [rec.get("id") for rec in a_data.get("result", []) if rec.get("name") not in keep_names]
        if a_to_delete:
            if args.dry_run:
                print(f"DRY-RUN: Would delete A records: {a_to_delete}")
            else:
                delete_records(token, zone_id, a_to_delete)
                print(f"Deleted A records: {a_to_delete}")
        # Purge CNAME records
        cname_data = api_get(f"/zones/{zone_id}/dns_records", token, params={"type": "CNAME"})
        cname_to_delete = [rec.get("id") for rec in cname_data.get("result", []) if rec.get("name") not in keep_names]
        if cname_to_delete:
            if args.dry_run:
                print(f"DRY-RUN: Would delete CNAME records: {cname_to_delete}")
            else:
                delete_records(token, zone_id, cname_to_delete)
                print(f"Deleted CNAME records: {cname_to_delete}")
    # Optionally set GitHub Pages A/AAAA records at apex (combined logic)
    if args.set_github_a or args.set_github_aaaa:
        # Remove existing apex CNAME (cannot coexist with A/AAAA)
        existing_apex_cname = get_dns_records(token, zone_id, args.domain, record_type="CNAME")
        cname_ids = [r["id"] for r in existing_apex_cname]
        if cname_ids:
            if args.dry_run:
                print(f"DRY-RUN: Would delete apex CNAME before setting GitHub A/AAAA records: {cname_ids}")
            else:
                delete_records(token, zone_id, cname_ids)
                print(f"Deleted apex CNAME: {cname_ids}")
        # Remove existing apex A/AAAA (record previous IPs to attach as comment)
        existing_apex_a = get_dns_records(token, zone_id, args.domain, record_type="A")
        existing_apex_aaaa = get_dns_records(token, zone_id, args.domain, record_type="AAAA")
        previous_a = ",".join([r.get("content") for r in existing_apex_a]) if existing_apex_a else ""
        previous_aaaa = ",".join([r.get("content") for r in existing_apex_aaaa]) if existing_apex_aaaa else ""
        ids_to_delete = [r["id"] for r in (existing_apex_a + existing_apex_aaaa)]
        if ids_to_delete:
            if args.dry_run:
                print(f"DRY-RUN: Would delete apex A/AAAA before setting GitHub A/AAAA records: {ids_to_delete}")
            else:
                delete_records(token, zone_id, ids_to_delete)
                print(f"Deleted apex A/AAAA: {ids_to_delete}")
        # Create requested records
        if args.set_github_a:
            github_ips = ["185.199.108.153", "185.199.109.153", "185.199.110.153", "185.199.111.153"]
            for ip in github_ips:
                if args.dry_run:
                    print(f"DRY-RUN: Would create A {args.domain} -> {ip} proxied=False")
                else:
                    comment = f"Previous apex A: {previous_a}" if previous_a else None
                    msg = create_a_record(token, zone_id, args.domain, ip, ttl=300, proxied=False, comment=comment)
                    print(msg)
        if args.set_github_aaaa:
            github_ipv6 = [
                "2606:50c0:8000::153",
                "2606:50c0:8001::153",
                "2606:50c0:8002::153",
                "2606:50c0:8003::153",
            ]
            for ip6 in github_ipv6:
                if args.dry_run:
                    print(f"DRY-RUN: Would create AAAA {args.domain} -> {ip6} proxied=False")
                else:
                    comment = f"Previous apex AAAA: {previous_aaaa}" if previous_aaaa else None
                    msg = create_aaaa_record(token, zone_id, args.domain, ip6, ttl=300, proxied=False, comment=comment)
                    print(msg)
    print("Note: Enable the custom domain in GitHub Pages settings and add a CNAME file if needed.")


if __name__ == "__main__":
    main()
