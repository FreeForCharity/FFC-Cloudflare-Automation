#!/usr/bin/env python3
"""Admin DNS Agent: orchestrate Cloudflare zone check/add and GitHub Pages DNS update.

Usage:
  python admin_dns_agent.py --domain example.org --pages-host freeforcharity.github.io [--apply]

Behavior:
  - Checks if zone exists in Cloudflare via /zones?name=...
  - If missing, triggers the GitHub Actions workflow `cloudflare-zone-add.yml` (dry-run unless --apply).
  - If present, triggers `cloudflare-dns.yml` to set GitHub Pages apex A+AAAA and www CNAME (dry-run unless --apply).

Requirements:
  - Environment token for Cloudflare read: CLOUDFLARE_API_KEY_READ_ALL (preferred) or CLOUDFLARE_API_TOKEN
  - GitHub CLI installed (gh); authenticated with repo access (`gh auth login`)
"""
import argparse
import os
import subprocess
import sys
from typing import Optional

import requests

CF_API = "https://api.cloudflare.com/client/v4"


def cf_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def cf_get(path: str, token: str, params: Optional[dict] = None) -> dict:
    r = requests.get(f"{CF_API}{path}", headers=cf_headers(token), params=params or {}, timeout=30)
    if not r.ok:
        raise SystemExit(f"Cloudflare GET {path} failed: {r.status_code} {r.text}")
    data = r.json()
    if not data.get("success", False):
        raise SystemExit(f"Cloudflare GET {path} returned error: {data}")
    return data


def get_cf_token() -> str:
    t = os.getenv("CLOUDFLARE_API_KEY_READ_ALL") or os.getenv("CLOUDFLARE_API_TOKEN")
    if not t:
        raise SystemExit("Set CLOUDFLARE_API_KEY_READ_ALL or CLOUDFLARE_API_TOKEN in environment")
    return t.strip()


def gh() -> str:
    # Prefer PATH, else common Windows location
    exe = "gh"
    if sys.platform.startswith("win"):
        candidate = r"C:\Program Files\GitHub CLI\gh.exe"
        if os.path.exists(candidate):
            exe = candidate
    return exe


def run(cmd: list[str]) -> int:
    print("$", " ".join(cmd))
    return subprocess.call(cmd)


def parse_args():
    p = argparse.ArgumentParser(description="Admin DNS Agent orchestrator")
    p.add_argument("--domain", required=True, help="Root domain to manage (zone)")
    p.add_argument("--pages-host", required=True, help="GitHub Pages hostname (e.g., freeforcharity.github.io)")
    p.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    return p.parse_args()


def main():
    args = parse_args()
    token = get_cf_token()
    # Check zone existence
    try:
        z = cf_get("/zones", token, params={"name": args.domain, "per_page": 1})
        exists = bool(z.get("result"))
    except SystemExit as e:
        print(str(e))
        exists = False

    apply_str = "true" if args.apply else "false"
    if not exists:
        print(f"Zone {args.domain} not found; triggering zone add workflow (apply={apply_str})...")
        rc = run([gh(), "workflow", "run", "cloudflare-zone-add.yml", "-F", f"domain={args.domain}", "-F", f"apply={apply_str}"])
        if rc != 0:
            raise SystemExit("Failed to dispatch zone add workflow")
        print("Dispatched zone add workflow. Approvals may be required.")
        return

    print(f"Zone {args.domain} exists; triggering DNS update to GitHub Pages (apply={apply_str})...")
    rc = run([
        gh(), "workflow", "run", "cloudflare-dns.yml",
        "-F", f"domain={args.domain}",
        "-F", f"pages_host={args.pages_host}",
        "-F", "mode=github-pages-a-aaaa",
        "-F", f"apply={apply_str}",
    ])
    if rc != 0:
        raise SystemExit("Failed to dispatch DNS update workflow")
    print("Dispatched DNS update workflow. Approvals may be required.")


if __name__ == "__main__":
    main()
