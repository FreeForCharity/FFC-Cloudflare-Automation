#!/usr/bin/env python3
"""Federated-credential subject audit — exact-match, not just presence.

The #625 outage was an *expected* Azure AD federated credential
(`github-oidc-m365-prod`) whose **subject had a typo** — a trailing hyphen,
`repo:FreeForCharity/FFC-Cloudflare-Automation-:environment:m365-prod`. OIDC
subject matching is exact-string, so every M365 job failed `AADSTS700213` while
the credential still *existed* — a presence/enumeration check (cf. #589) passes it.

This audit compares live Azure state to the declarative expected map in
`config/federated-credentials.json` and asserts, for each expected credential,
that a matching credential exists with an EXACT canonical `subject`, `issuer`,
and `audiences`. It also flags any THIS-repo credential that is not expected.

Modes:
  # Self-check the config is well-formed (default; runs in CI, no Azure):
  python3 scripts/check-federated-credential-subjects.py

  # Live audit against Azure (operator; needs an authenticated `az` with Graph read):
  python3 scripts/check-federated-credential-subjects.py --live

  # Offline audit against saved `az ad app federated-credential list` dumps
  # (files named creds-<objectId>.json in a directory):
  python3 scripts/check-federated-credential-subjects.py --actual-dir ./dumps

Exit codes: 0 = OK, 1 = a problem (malformed config, or, in audit mode, drift).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

CONFIG = "config/federated-credentials.json"
GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
ENV_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def load_config(path):
    cfg = json.load(open(path, encoding="utf-8"))
    for key in ("repo", "issuer", "audiences", "apps"):
        if key not in cfg:
            raise SystemExit(f"{path}: missing required key '{key}'")
    return cfg


def expected_subject(cfg, env):
    return f"repo:{cfg['repo']}:environment:{env}"


def self_check(cfg, path):
    errors = []
    seen = set()
    for app in cfg["apps"]:
        oid = app.get("objectId", "")
        if not GUID_RE.match(oid):
            errors.append(f"{app.get('displayName', '?')}: objectId '{oid}' is not a GUID")
        envs = app.get("environments", [])
        if not envs:
            errors.append(f"{app.get('displayName', '?')}: no environments listed")
        for env in envs:
            if not ENV_RE.match(env):
                errors.append(f"{app.get('displayName', '?')}: bad environment slug '{env}'")
            key = (oid, env)
            if key in seen:
                errors.append(f"duplicate (app, environment): {oid} / {env}")
            seen.add(key)
    if cfg["issuer"] != "https://token.actions.githubusercontent.com":
        errors.append(f"unexpected issuer: {cfg['issuer']}")
    if cfg["audiences"] != ["api://AzureADTokenExchange"]:
        errors.append(f"unexpected audiences: {cfg['audiences']}")
    if errors:
        print(f"Federated-credential config self-check FAILED ({path}):")
        for e in errors:
            print("  -", e)
        return 1
    n = sum(len(a["environments"]) for a in cfg["apps"])
    print(f"Federated-credential config OK: {len(cfg['apps'])} apps, {n} expected env credentials.")
    return 0


def az_list(object_id):
    az = shutil.which("az") or shutil.which("az.cmd") or "az"
    r = subprocess.run(
        [az, "ad", "app", "federated-credential", "list", "--id", object_id, "-o", "json"],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise SystemExit(f"az list failed for {object_id}: {r.stderr.strip()}")
    return json.loads(r.stdout or "[]")


def audit(cfg, get_actual):
    """get_actual(object_id) -> list of credential dicts."""
    prefix = f"repo:{cfg['repo']}:environment:"
    problems = []
    checked = 0
    for app in cfg["apps"]:
        name = app["displayName"]
        actual = get_actual(app["objectId"])
        by_subject = {c.get("subject"): c for c in actual}
        # This-repo credentials actually present on the app.
        present_envs = set()
        for c in actual:
            subj = c.get("subject", "")
            if subj.startswith(prefix):
                present_envs.add(subj[len(prefix):])

        for env in app["environments"]:
            checked += 1
            want = expected_subject(cfg, env)
            cred = by_subject.get(want)
            if not cred:
                problems.append(
                    f"{name}: expected credential for environment '{env}' with subject "
                    f"'{want}' is MISSING (renamed env, deleted cred, or a malformed subject)."
                )
                continue
            if cred.get("issuer") != cfg["issuer"]:
                problems.append(f"{name}/{env}: issuer '{cred.get('issuer')}' != '{cfg['issuer']}'")
            if cred.get("audiences") != cfg["audiences"]:
                problems.append(f"{name}/{env}: audiences {cred.get('audiences')} != {cfg['audiences']}")

        # This-repo creds present but not expected (drift / typo'd subjects show up here).
        for env in sorted(present_envs - set(app["environments"])):
            problems.append(
                f"{name}: UNEXPECTED this-repo credential for '{env}' "
                f"(subject '{prefix}{env}') — not in the expected map; investigate."
            )

    if problems:
        print(f"Federated-credential audit FAILED ({checked} expected creds checked):")
        for p in problems:
            print("  -", p)
        return 1
    print(f"Federated-credential audit OK: {checked} expected creds present with exact subject/issuer/audiences.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--live", action="store_true", help="Audit against live Azure via `az` (operator).")
    ap.add_argument("--actual-dir", help="Audit against saved creds-<objectId>.json dumps in this dir.")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rc = self_check(cfg, args.config)
    if rc:
        return rc
    if args.live:
        return audit(cfg, az_list)
    if args.actual_dir:
        def from_dir(oid):
            p = os.path.join(args.actual_dir, f"creds-{oid}.json")
            return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
        return audit(cfg, from_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
