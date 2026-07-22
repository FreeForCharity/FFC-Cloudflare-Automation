#!/usr/bin/env python3
"""Cloudflare bulk-script library-consumption guard (issue #778).

Issue #778 consolidated the Cloudflare REST plumbing that used to live,
duplicated, inside the bulk scripts (`Invoke-Cf`, `Resolve-CfZone`, the Pages IP
literals, ...) into ONE shared library, `scripts/cloudflare-api-common.ps1`, and
routed the bulk scripts through its `Invoke-CfApi` / `Resolve-CfZone*` /
`Get-GhPages*` functions. Its acceptance criteria include, verbatim:

    - One `Invoke-Cf` (shared lib) across 112/119/120; zero copies of the Pages
      IP list outside the lib.

Nothing kept that true. A later edit to a bulk script could re-introduce a
private `function Invoke-Cf { ... }` or paste a Pages IP literal back in, and
CI would say nothing — the exact silent drift the consolidation set out to kill
(120 once re-implemented the apex flip itself and dropped the www CNAME).

This guard makes the regression impossible to land silently. For each bulk
consumer (the scripts behind workflows 112 / 119 / 120) it asserts:

  1. the script dot-sources `cloudflare-api-common.ps1` (so the shared functions
     are actually in scope);
  2. it does NOT define its own copy of any consolidated plumbing function
     (`Invoke-Cf`, `Invoke-CfApi`, `Resolve-CfZone`, `Get-GhPages*`, ...);
  3. it contains NO GitHub Pages IP literal — the canonical set (parsed out of
     the library, so this guard adds no third copy) must be reached only through
     `Get-GhPagesIps` / `Get-GhPagesIpv6s`.

It also checks the other direction: the library must still DEFINE the functions
the consumers rely on, so a rename in the library can't quietly strand them.

The engine `Update-CloudflareDns.ps1` is deliberately NOT a consumer here: per
#778 it keeps its own richer private `Invoke-CfApi` for now, so banning private
plumbing in it would be wrong. This guard is scoped to the three bulk scripts
the acceptance criterion names.

Exit codes: 0 = every consumer routes through the library (or --self-test
passed), 1 = a regression / malformed source / self-test failure.

    python3 scripts/check-cloudflare-bulk-lib-consumption.py             # real check
    python3 scripts/check-cloudflare-bulk-lib-consumption.py --self-test  # parser self-test only
"""
from __future__ import annotations

import argparse
import re
import sys

PS_LIB = "scripts/cloudflare-api-common.ps1"

# The bulk scripts named by #778's acceptance criterion (workflows 120/119/112).
CONSUMERS = [
    "scripts/bulk-cutover-to-github-pages.ps1",
    "scripts/bulk-staging-cname-github-pages.ps1",
    "scripts/bulk-replace-a-record-ip.ps1",
]

# Functions the library must define so dot-sourcing it actually gives the bulk
# scripts what they call. A rename here without updating the consumers would
# strand them at runtime; this catches it in CI instead.
REQUIRED_LIB_FUNCS = [
    "Invoke-CfApi",
    "Get-CfEnvTokens",
    "Resolve-CfZone",
    "Resolve-CfZoneId",
    "Get-CfDnsRecords",
    "Get-GhPagesIps",
    "Get-GhPagesIpv6s",
    "Get-GhPagesWwwTarget",
]

# Plumbing that was consolidated into the library. A consumer defining any of
# these has re-grown a private copy — exactly what #778 removed.
BANNED_CONSUMER_FUNCS = [
    "Invoke-Cf",
    "Invoke-CfApi",
    "Invoke-CfProbe",
    "Resolve-CfZone",
    "Resolve-CfZoneId",
    "Get-CfDnsRecords",
    "Get-GhPagesIps",
    "Get-GhPagesIpv6s",
    "Get-GhPagesWwwTarget",
    "Get-ApexARecords",
]

_QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")
# PowerShell is case-insensitive; a dot-source is `. <path>` at line start.
_DOTSOURCE = re.compile(r"(?im)^\s*\.\s+.*cloudflare-api-common\.ps1")


def parse_ps_ip_literals(text: str) -> list[str]:
    """The canonical Pages IPv4+IPv6 literals from the library (no new copy)."""
    ipv4 = re.search(r"\$script:GhPagesIps\s*=\s*@\((.*?)\)", text, re.S)
    ipv6 = re.search(r"\$script:GhPagesIpv6s\s*=\s*@\((.*?)\)", text, re.S)
    if not ipv4:
        raise ValueError(f"{PS_LIB}: could not find $script:GhPagesIps = @( ... )")
    if not ipv6:
        raise ValueError(f"{PS_LIB}: could not find $script:GhPagesIpv6s = @( ... )")
    literals = _QUOTED.findall(ipv4.group(1)) + _QUOTED.findall(ipv6.group(1))
    if not literals:
        raise ValueError(f"{PS_LIB}: parsed an empty Pages IP set")
    return literals


def _defines(func: str, text: str) -> bool:
    """True if `text` has a `function <func>` definition (case-insensitive)."""
    return re.search(rf"(?im)^\s*function\s+{re.escape(func)}\b", text) is not None


def check_lib(text: str) -> list[str]:
    """Problems with the library itself (missing functions the consumers need)."""
    problems: list[str] = []
    for func in REQUIRED_LIB_FUNCS:
        if not _defines(func, text):
            problems.append(
                f"{PS_LIB}: expected `function {func}` is missing — bulk scripts "
                f"that call it would break (rename it in the consumers too)"
            )
    return problems


def check_consumer(path: str, text: str, banned_ips: list[str]) -> list[str]:
    """Problems with one bulk consumer (must route through the library)."""
    problems: list[str] = []

    if not _DOTSOURCE.search(text):
        problems.append(
            f"{path}: does not dot-source {PS_LIB} "
            f"(expected a line like `. (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')`)"
        )

    for func in BANNED_CONSUMER_FUNCS:
        if _defines(func, text):
            problems.append(
                f"{path}: defines a private `function {func}` — this plumbing was "
                f"consolidated into {PS_LIB} (#778); call the shared function instead"
            )

    # Case-insensitive: IPv6 literals are hex and a pasted copy may differ only
    # in casing (e.g. 2606:50C0:...), which must still be caught.
    lowered = text.lower()
    for ip in banned_ips:
        if ip.lower() in lowered:
            problems.append(
                f"{path}: contains GitHub Pages IP literal '{ip}' — the canonical "
                f"set lives only in {PS_LIB}; reach it via Get-GhPagesIps / "
                f"Get-GhPagesIpv6s (#778)"
            )

    return problems


def self_test() -> int:
    """Exercise the checks against fixtures (no repo I/O)."""
    lib_ok = """
$script:GhPagesIps = @(
    '185.199.108.153',
    '185.199.109.153'
)
$script:GhPagesIpv6s = @(
    '2606:50c0:8000::153'
)
function Invoke-CfApi { }
function Get-CfEnvTokens { }
function Resolve-CfZone { }
function Resolve-CfZoneId { }
function Get-CfDnsRecords { }
function Get-GhPagesIps { }
function Get-GhPagesIpv6s { }
function Get-GhPagesWwwTarget { }
"""
    consumer_ok = """
. (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')
$zone = Resolve-CfZone -Domain $d
$ips = @(Get-GhPagesIps)
"""
    consumer_no_dotsource = consumer_ok.replace(
        ". (Join-Path $PSScriptRoot 'cloudflare-api-common.ps1')", "# (lib not sourced)"
    )
    consumer_private_func = consumer_ok + "\nfunction Invoke-Cf { param($m) }\n"
    consumer_ip_literal = consumer_ok + "\n$apex = '185.199.108.153'\n"
    # IPv6 pasted with different hex casing must still be rejected.
    consumer_ipv6_mixedcase = consumer_ok + "\n$aaaa = '2606:50C0:8000::153'\n"
    lib_missing_func = lib_ok.replace("function Resolve-CfZone { }", "")

    banned = parse_ps_ip_literals(lib_ok)
    errors: list[str] = []

    if banned != ["185.199.108.153", "185.199.109.153", "2606:50c0:8000::153"]:
        errors.append(f"IP-literal parse wrong: {banned}")
    if check_lib(lib_ok):
        errors.append("false-positive: a complete library was flagged")
    if check_consumer("ok.ps1", consumer_ok, banned):
        errors.append("false-positive: a compliant consumer was flagged")
    if not check_consumer("x.ps1", consumer_no_dotsource, banned):
        errors.append("false-negative: a consumer NOT sourcing the lib was allowed")
    if not check_consumer("x.ps1", consumer_private_func, banned):
        errors.append("false-negative: a private `function Invoke-Cf` was allowed")
    if not check_consumer("x.ps1", consumer_ip_literal, banned):
        errors.append("false-negative: a pasted Pages IP literal was allowed")
    if not check_consumer("x.ps1", consumer_ipv6_mixedcase, banned):
        errors.append("false-negative: a mixed-case IPv6 Pages literal was allowed")
    if not check_lib(lib_missing_func):
        errors.append("false-negative: a library missing a required function was allowed")

    if errors:
        print("Cloudflare bulk-lib consumption guard self-test FAILED:")
        for e in errors:
            print("  -", e)
        return 1
    print("Cloudflare bulk-lib consumption guard self-test OK.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="Run parser self-test only (no repo I/O).")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    # Always self-test first so a broken check can never mask a real regression.
    rc = self_test()
    if rc:
        return rc

    try:
        with open(PS_LIB, encoding="utf-8") as fh:
            lib_text = fh.read()
        banned_ips = parse_ps_ip_literals(lib_text)
    except (OSError, ValueError) as exc:
        print(f"Cloudflare bulk-lib consumption guard FAILED: {exc}")
        return 1

    problems = check_lib(lib_text)
    for path in CONSUMERS:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            problems.append(f"{path}: could not read ({exc})")
            continue
        problems.extend(check_consumer(path, text, banned_ips))

    if problems:
        print("Cloudflare bulk-lib consumption guard FAILED (a bulk script drifted off the shared library):")
        for p in problems:
            print("  -", p)
        print(
            f"\nFix: {PS_LIB} is the single source of truth for the Cloudflare REST "
            f"plumbing and the Pages IP set (issue #778). Bulk scripts must dot-source "
            f"it and call its functions rather than re-implementing them."
        )
        return 1

    print(
        f"Cloudflare bulk-lib consumption guard OK: {len(CONSUMERS)} bulk script(s) "
        f"route through {PS_LIB} (no private plumbing, no Pages IP literals)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
