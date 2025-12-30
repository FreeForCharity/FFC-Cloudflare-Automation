#!/usr/bin/env python3
"""Export Cloudflare zones with first A record IP.

Outputs a CSV with columns: zone_name, zone_id, record_name, ip.

Usage:
  python export_zone_a_records.py --token <CLOUDFLARE_API_TOKEN> --output zone_a_records.csv
  # Or rely on env var CLOUDFLARE_API_TOKEN

Notes:
- "First" A record is retrieved by requesting per_page=1 from Cloudflare's DNS records API.
- If no A record exists for a zone, ip will be empty.
"""
import csv
import os
import sys
import argparse
from typing import Optional

import requests

API_BASE = "https://api.cloudflare.com/client/v4"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def api_get(path: str, token: str, params: Optional[dict] = None) -> dict:
    r = requests.get(f"{API_BASE}{path}", headers=_headers(token), params=params or {}, timeout=30)
    if not r.ok:
        raise SystemExit(f"GET {path} failed: {r.status_code} {r.text}")
    data = r.json()
    if not data.get("success", False):
        raise SystemExit(f"GET {path} returned error: {data}")
    return data


def list_all_zones(token: str) -> list[dict]:
    zones = []
    page = 1
    per_page = 50
    while True:
        data = api_get("/zones", token, params={"page": page, "per_page": per_page})
        batch = data.get("result", [])
        zones.extend(batch)
        info = data.get("result_info", {})
        total_pages = info.get("total_pages") or 1
        if page >= total_pages:
            break
        page += 1
    return zones


def first_a_record(token: str, zone_id: str) -> Optional[dict]:
    data = api_get(f"/zones/{zone_id}/dns_records", token, params={"type": "A", "per_page": 1, "page": 1})
    records = data.get("result", [])
    return records[0] if records else None


def parse_args():
    p = argparse.ArgumentParser(description="Export Cloudflare zones with first A record IP to CSV")
    p.add_argument("--token", help="Cloudflare API token; if omitted, env CLOUDFLARE_API_TOKEN is used")
    p.add_argument("--output", default="zone_a_records.csv", help="Output CSV file path")
    return p.parse_args()


def get_token(arg_token: Optional[str]) -> str:
    if arg_token:
        return arg_token.strip()
    # Prefer READ_ALL, then DNS-only, then standard token
    env_token = (
        os.getenv("CLOUDFLARE_API_KEY_READ_ALL")
        or os.getenv("CLOUDFLARE_API_KEY_DNS_ONLY")
        or os.getenv("CLOUDFLARE_API_TOKEN")
    )
    if env_token:
        return env_token.strip()
    raise SystemExit("Cloudflare API token is required (pass --token or set CLOUDFLARE_API_TOKEN or CLOUDFLARE_API_KEY_DNS_ONLY)")


def main():
    args = parse_args()
    token = get_token(args.token)
    zones = list_all_zones(token)
    rows = []
    for z in zones:
        zone_name = z.get("name")
        zone_id = z.get("id")
        rec = first_a_record(token, zone_id)
        if rec:
            rows.append({
                "zone_name": zone_name,
                "zone_id": zone_id,
                "record_name": rec.get("name"),
                "ip": rec.get("content"),
            })
        else:
            rows.append({
                "zone_name": zone_name,
                "zone_id": zone_id,
                "record_name": "",
                "ip": "",
            })
    # Write CSV
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["zone_name", "zone_id", "record_name", "ip"])
        writer.writeheader()
        writer.writerows(rows)
    # Also print a summary to stdout
    print(f"Wrote {len(rows)} rows to {args.output}")
    for r in rows[:10]:
        print(f"- {r['zone_name']}: {r['record_name']} -> {r['ip']}")


if __name__ == "__main__":
    main()
