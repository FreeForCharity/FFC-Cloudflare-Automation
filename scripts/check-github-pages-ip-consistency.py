#!/usr/bin/env python3
"""GitHub Pages DNS-target drift guard (issue #778).

`scripts/cloudflare-api-common.ps1` is the single source of truth for the
GitHub Pages apex IP sets and the www CNAME target (issue #778 consolidated the
PowerShell callers onto its `Get-GhPages*` functions, and the file says in so
many words: "Do NOT copy these values into other scripts").

One consumer cannot obey that rule: `scripts/preflight-cutover.mjs` is Node, so
it cannot call the PowerShell library and necessarily carries its OWN copy of
the same IP sets (`GH_PAGES_IPV4` / `GH_PAGES_IPV6`). That copy is exactly the
kind of silent-drift hazard #778 set out to kill — if GitHub ever rotates a
Pages IP and only one of the two files is updated, the DNS-writing engine and
the read-only cutover preflight would disagree about what "served by Pages"
means, and 121's go/no-go verdict would drift from what 120 actually writes.

This guard makes that impossible to do silently: it parses the canonical sets
out of the PowerShell library and the mirrored sets out of the Node preflight
and fails when they diverge. It is the cross-language stand-in for the "consume
the Get-* functions" rule the PowerShell side already enforces structurally.

Exit codes: 0 = the mirror is in sync (or --self-test passed), 1 = drift /
malformed source / self-test failure.

    python3 scripts/check-github-pages-ip-consistency.py            # real check
    python3 scripts/check-github-pages-ip-consistency.py --self-test  # parser self-test only
"""
from __future__ import annotations

import argparse
import re
import sys

PS_LIB = "scripts/cloudflare-api-common.ps1"
JS_PREFLIGHT = "scripts/preflight-cutover.mjs"

_QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")


def _quoted_items(block: str) -> list[str]:
    """Every single/double-quoted string literal inside a block, in order."""
    return _QUOTED.findall(block)


def parse_ps_targets(text: str) -> tuple[list[str], list[str], str]:
    """Canonical (ipv4, ipv6, www_target) from the PowerShell library."""
    ipv4 = re.search(r"\$script:GhPagesIps\s*=\s*@\((.*?)\)", text, re.S)
    ipv6 = re.search(r"\$script:GhPagesIpv6s\s*=\s*@\((.*?)\)", text, re.S)
    www = re.search(r"\$script:GhPagesWwwTarget\s*=\s*'([^']+)'", text)
    if not ipv4:
        raise ValueError(f"{PS_LIB}: could not find $script:GhPagesIps = @( ... )")
    if not ipv6:
        raise ValueError(f"{PS_LIB}: could not find $script:GhPagesIpv6s = @( ... )")
    if not www:
        raise ValueError(f"{PS_LIB}: could not find $script:GhPagesWwwTarget = '...'")
    return _quoted_items(ipv4.group(1)), _quoted_items(ipv6.group(1)), www.group(1)


def parse_js_targets(text: str) -> tuple[list[str], list[str]]:
    """Mirrored (ipv4, ipv6) from the Node preflight's `new Set([ ... ])`."""
    ipv4 = re.search(r"GH_PAGES_IPV4\s*=\s*new Set\(\[(.*?)\]\)", text, re.S)
    ipv6 = re.search(r"GH_PAGES_IPV6\s*=\s*new Set\(\[(.*?)\]\)", text, re.S)
    if not ipv4:
        raise ValueError(f"{JS_PREFLIGHT}: could not find GH_PAGES_IPV4 = new Set([ ... ])")
    if not ipv6:
        raise ValueError(f"{JS_PREFLIGHT}: could not find GH_PAGES_IPV6 = new Set([ ... ])")
    return _quoted_items(ipv4.group(1)), _quoted_items(ipv6.group(1))


def _norm(items: list[str]) -> list[str]:
    """Order- and case-insensitive canonical form (IPv6 casing is cosmetic)."""
    return sorted(s.lower() for s in items)


def compare(
    ps_v4: list[str],
    ps_v6: list[str],
    ps_www: str,
    js_v4: list[str],
    js_v6: list[str],
    js_text: str,
) -> list[str]:
    """Return a list of drift problems (empty = in sync)."""
    problems: list[str] = []
    if not ps_v4:
        problems.append(f"{PS_LIB}: canonical IPv4 set is empty")
    if _norm(ps_v4) != _norm(js_v4):
        problems.append(
            "IPv4 apex set drift between "
            f"{PS_LIB} ({sorted(ps_v4)}) and {JS_PREFLIGHT} ({sorted(js_v4)})"
        )
    if _norm(ps_v6) != _norm(js_v6):
        problems.append(
            "IPv6 apex set drift between "
            f"{PS_LIB} ({sorted(ps_v6)}) and {JS_PREFLIGHT} ({sorted(js_v6)})"
        )
    # The Node preflight derives the Pages origin/www host as a literal; if the
    # org's Pages host is ever renamed in the library it must change here too.
    if ps_www.lower() not in js_text.lower():
        problems.append(
            f"www/Pages host '{ps_www}' (canonical in {PS_LIB}) not found in "
            f"{JS_PREFLIGHT} — the two disagree on the GitHub Pages host"
        )
    return problems


def self_test() -> int:
    """Exercise the parsers and the drift branch against fixtures (no repo I/O)."""
    ps_fixture = """
$script:GhPagesIps = @(
    '185.199.108.153',
    '185.199.109.153'
)
$script:GhPagesIpv6s = @(
    '2606:50c0:8000::153',
    '2606:50c0:8001::153'
)
$script:GhPagesWwwTarget = 'freeforcharity.github.io'
"""
    js_ok = """
export const GH_PAGES_IPV4 = new Set([
  '185.199.109.153',
  '185.199.108.153',
]);
export const GH_PAGES_IPV6 = new Set([
  '2606:50C0:8000::153',
  '2606:50c0:8001::153',
]);
const origin = 'https://freeforcharity.github.io/repo/';
"""
    js_drift = js_ok.replace("185.199.108.153", "185.199.108.999")

    errors: list[str] = []
    ps_v4, ps_v6, ps_www = parse_ps_targets(ps_fixture)
    if _norm(ps_v4) != ["185.199.108.153", "185.199.109.153"]:
        errors.append(f"PS IPv4 parse wrong: {ps_v4}")
    if len(ps_v6) != 2 or ps_www != "freeforcharity.github.io":
        errors.append(f"PS IPv6/www parse wrong: {ps_v6} / {ps_www}")

    js_v4, js_v6 = parse_js_targets(js_ok)
    # Reordered + mixed-case fixtures must be treated as IN SYNC.
    if compare(ps_v4, ps_v6, ps_www, js_v4, js_v6, js_ok):
        errors.append("false-positive: matching (reordered/mixed-case) sets flagged as drift")
    # A single changed octet MUST be caught.
    jd_v4, jd_v6 = parse_js_targets(js_drift)
    if not compare(ps_v4, ps_v6, ps_www, jd_v4, jd_v6, js_drift):
        errors.append("false-negative: a changed IPv4 octet was NOT flagged")
    # A renamed www host MUST be caught.
    if not compare(ps_v4, ps_v6, ps_www, js_v4, js_v6, js_ok.replace("freeforcharity", "example")):
        errors.append("false-negative: a renamed Pages host was NOT flagged")

    if errors:
        print("Pages-IP consistency guard self-test FAILED:")
        for e in errors:
            print("  -", e)
        return 1
    print("Pages-IP consistency guard self-test OK.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-test", action="store_true", help="Run parser self-test only (no repo I/O).")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    # Always self-test first so a broken parser can never mask real drift.
    rc = self_test()
    if rc:
        return rc

    try:
        with open(PS_LIB, encoding="utf-8") as fh:
            ps_text = fh.read()
        with open(JS_PREFLIGHT, encoding="utf-8") as fh:
            js_text = fh.read()
        ps_v4, ps_v6, ps_www = parse_ps_targets(ps_text)
        js_v4, js_v6 = parse_js_targets(js_text)
    except (OSError, ValueError) as exc:
        print(f"Pages-IP consistency guard FAILED: {exc}")
        return 1

    problems = compare(ps_v4, ps_v6, ps_www, js_v4, js_v6, js_text)
    if problems:
        print("Pages-IP consistency guard FAILED (GitHub Pages targets drifted):")
        for p in problems:
            print("  -", p)
        print(
            f"\nFix: {PS_LIB} is the single source of truth (issue #778). Update "
            f"the mirrored sets in {JS_PREFLIGHT} to match it."
        )
        return 1
    print(
        f"Pages-IP consistency guard OK: {JS_PREFLIGHT} mirrors the canonical "
        f"{len(ps_v4)} IPv4 + {len(ps_v6)} IPv6 Pages targets from {PS_LIB}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
