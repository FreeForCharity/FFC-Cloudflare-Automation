#!/usr/bin/env python3
"""Export Cloudflare DNS summary per zone.

Generates a CSV with key DNS details for each zone:
- Apex A and AAAA records (IPs, TTLs, proxied)
- www CNAME target (content), TTL, proxied
- Optional counts of other A/AAAA/CNAME records

Usage examples:
    python export_zone_dns_summary.py --zones example.org,example.com --output zone_dns_summary.csv
    python export_zone_dns_summary.py --zones-file zones.txt --output zone_dns_summary.csv
    # If token cannot read zones, provide zone IDs directly:
    python export_zone_dns_summary.py --zones example.org --zone-ids example.org=0123456789abcdef0123456789abcdef --output zone_dns_summary.csv
    python export_zone_dns_summary.py --zones-file zones.txt --zone-id-file zone_ids.csv --output zone_dns_summary.csv

Token sourcing:
- Prefers env var CLOUDFLARE_API_KEY_READ_ALL; then CLOUDFLARE_API_KEY_DNS_ONLY; falls back to CLOUDFLARE_API_TOKEN.
- If a DNS-only token cannot list zones, provide explicit zone names via --zones/--zones-file.
"""
import csv
import os
import argparse
from typing import Optional, List, Dict, Any

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


def get_token(arg_token: Optional[str]) -> str:
    if arg_token:
        return arg_token.strip()
    env_token = (
        os.getenv("CLOUDFLARE_API_KEY_READ_ALL")
        or os.getenv("CLOUDFLARE_API_KEY_DNS_ONLY")
        or os.getenv("CLOUDFLARE_API_TOKEN")
    )
    if env_token:
        return env_token.strip()
    raise SystemExit("Cloudflare API token is required (set CLOUDFLARE_API_KEY_READ_ALL or CLOUDFLARE_API_KEY_DNS_ONLY or CLOUDFLARE_API_TOKEN, or pass --token)")


def get_zone_id_by_name(token: str, zone_name: str) -> Optional[str]:
    # Filter by name to avoid broad zone listing; permitted if token grants zone:read for this zone
    data = api_get("/zones", token, params={"name": zone_name, "per_page": 1})
    result = data.get("result", [])
    if not result:
        return None
    return result[0].get("id")


def dns_records(token: str, zone_id: str, params: Optional[dict] = None) -> List[Dict[str, Any]]:
    data = api_get(f"/zones/{zone_id}/dns_records", token, params=params or {})
    return data.get("result", [])


def list_all_zones(token: str) -> List[Dict[str, Any]]:
    zones: List[Dict[str, Any]] = []
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


def collect_zone_summary(token: str, zone_name: str, zone_id_override: Optional[str] = None) -> Dict[str, Any]:
    zid = zone_id_override or get_zone_id_by_name(token, zone_name)
    if not zid:
        return {
            "zone": zone_name,
            "apex_a_ips": "",
            "apex_a_ttls": "",
            "apex_a_proxied": "",
            "apex_aaaa_ips": "",
            "apex_aaaa_ttls": "",
            "apex_aaaa_proxied": "",
            "www_cname_target": "",
            "www_cname_ttl": "",
            "www_cname_proxied": "",
            "other_a_count": 0,
            "other_aaaa_count": 0,
            "other_cname_count": 0,
        }

    # Apex A/AAAA
    apex_a = dns_records(token, zid, params={"type": "A", "name": zone_name, "per_page": 100})
    apex_aaaa = dns_records(token, zid, params={"type": "AAAA", "name": zone_name, "per_page": 100})
    # www CNAME
    www_name = f"www.{zone_name}"
    www_cname = dns_records(token, zid, params={"type": "CNAME", "name": www_name, "per_page": 1})

    # Other counts (exclude apex and www)
    other_a = dns_records(token, zid, params={"type": "A", "per_page": 100})
    other_aaaa = dns_records(token, zid, params={"type": "AAAA", "per_page": 100})
    other_cname = dns_records(token, zid, params={"type": "CNAME", "per_page": 100})

    def filter_out(records: List[Dict[str, Any]], names: List[str]) -> List[Dict[str, Any]]:
        return [r for r in records if r.get("name") not in names]

    other_a_count = len(filter_out(other_a, [zone_name]))
    other_aaaa_count = len(filter_out(other_aaaa, [zone_name]))
    other_cname_count = len(filter_out(other_cname, [zone_name, www_name]))

    def join_field(records: List[Dict[str, Any]], key: str) -> str:
        values = [str(r.get(key, "")) for r in records]
        return ";".join(values)

    def join_bool(records: List[Dict[str, Any]], key: str) -> str:
        values = ["true" if r.get(key) else "false" for r in records]
        return ";".join(values)

    www_target = www_cname[0].get("content") if www_cname else ""
    www_ttl = www_cname[0].get("ttl") if www_cname else ""
    www_proxied = www_cname[0].get("proxied") if www_cname else None

    return {
        "zone": zone_name,
        "apex_a_ips": join_field(apex_a, "content"),
        "apex_a_ttls": join_field(apex_a, "ttl"),
        "apex_a_proxied": join_bool(apex_a, "proxied"),
        "apex_aaaa_ips": join_field(apex_aaaa, "content"),
        "apex_aaaa_ttls": join_field(apex_aaaa, "ttl"),
        "apex_aaaa_proxied": join_bool(apex_aaaa, "proxied"),
        "www_cname_target": www_target,
        "www_cname_ttl": www_ttl,
        "www_cname_proxied": "true" if www_proxied else ("false" if www_proxied is not None else ""),
        "other_a_count": other_a_count,
        "other_aaaa_count": other_aaaa_count,
        "other_cname_count": other_cname_count,
    }


def parse_args():
    p = argparse.ArgumentParser(description="Export Cloudflare DNS summary (A/AAAA/www CNAME) per zone to CSV")
    p.add_argument("--zones", help="Comma-separated zone names to export (e.g., example.org,example.com)")
    p.add_argument("--zones-file", help="Path to a file with one zone name per line")
    p.add_argument("--output", default="zone_dns_summary.csv", help="Output CSV file path")
    p.add_argument("--token", help="Cloudflare API token; defaults to env")
    p.add_argument("--zone-ids", help="Comma-separated mapping of zone=zone_id (e.g., example.org=abc123,example.com=def456)")
    p.add_argument("--zone-id-file", help="CSV with columns zone,zone_id to provide IDs")
    p.add_argument("--all-zones", action="store_true", help="Export for all zones accessible to the token")
    return p.parse_args()


def load_zones(args) -> List[str]:
    zones: List[str] = []
    if args.all_zones:
        # Caller will load via API; allow empty list here
        return zones
    if args.zones:
        zones.extend([z.strip() for z in args.zones.split(",") if z.strip()])
    if args.zones_file and os.path.exists(args.zones_file):
        with open(args.zones_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    zones.append(line)
    if not zones:
        raise SystemExit("No zones provided. Use --zones or --zones-file, or pass --all-zones.")
    # Deduplicate preserving order
    seen = set()
    unique = []
    for z in zones:
        if z not in seen:
            unique.append(z)
            seen.add(z)
    return unique


def load_zone_id_map(args) -> Dict[str, str]:
    mapping: Dict[str, str] = {}
    if args.zone_ids:
        parts = [p.strip() for p in args.zone_ids.split(",") if p.strip()]
        for part in parts:
            if "=" in part:
                name, zid = part.split("=", 1)
                name = name.strip()
                zid = zid.strip()
                if name and zid:
                    mapping[name] = zid
    if args.zone_id_file and os.path.exists(args.zone_id_file):
        with open(args.zone_id_file, "r", encoding="utf-8") as f:
            # Supports CSV with header zone,zone_id
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            # If not a DictReader compatible file, fallback to plain lines "zone,zone_id"
            if "zone" in headers and "zone_id" in headers:
                for row in reader:
                    name = (row.get("zone") or "").strip()
                    zid = (row.get("zone_id") or "").strip()
                    if name and zid:
                        mapping[name] = zid
            else:
                f.seek(0)
                for line in f:
                    line = line.strip()
                    if not line or "," not in line:
                        continue
                    name, zid = [s.strip() for s in line.split(",", 1)]
                    if name and zid:
                        mapping[name] = zid
    return mapping


def main():
    args = parse_args()
    token = get_token(args.token)
    zones = load_zones(args)
    zone_id_map = load_zone_id_map(args)

    if args.all_zones:
        allzones = list_all_zones(token)
        zones = [z.get("name") for z in allzones if z.get("name")]
        for z in allzones:
            name = z.get("name")
            zid = z.get("id")
            if name and zid:
                zone_id_map[name] = zid

    rows = []
    for zone_name in zones:
        try:
            rows.append(collect_zone_summary(token, zone_name, zone_id_map.get(zone_name)))
        except SystemExit as e:
            # Bubble up hard failures
            raise
        except Exception as e:
            # Collect empty row on error but continue
            rows.append({
                "zone": zone_name,
                "apex_a_ips": "",
                "apex_a_ttls": "",
                "apex_a_proxied": "",
                "apex_aaaa_ips": "",
                "apex_aaaa_ttls": "",
                "apex_aaaa_proxied": "",
                "www_cname_target": "",
                "www_cname_ttl": "",
                "www_cname_proxied": "",
                "other_a_count": 0,
                "other_aaaa_count": 0,
                "other_cname_count": 0,
            })

    fieldnames = [
        "zone",
        "apex_a_ips",
        "apex_a_ttls",
        "apex_a_proxied",
        "apex_aaaa_ips",
        "apex_aaaa_ttls",
        "apex_aaaa_proxied",
        "www_cname_target",
        "www_cname_ttl",
        "www_cname_proxied",
        "other_a_count",
        "other_aaaa_count",
        "other_cname_count",
    ]

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows to {args.output}")
    for r in rows[:10]:
        print(f"- {r['zone']}: A={r['apex_a_ips']} AAAA={r['apex_aaaa_ips']} www->{r['www_cname_target']}")


if __name__ == "__main__":
    main()
