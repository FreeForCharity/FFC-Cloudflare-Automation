#!/usr/bin/env python3
"""List the EIN each FFC charity website shows, from shallow clones of the FFC-EX repos.

Usage: collect_site_eins.py <repos_dir> <output.json>

<repos_dir> holds one clone per repo, named after the repo (FFC-EX-<domain>).
Writes [{"repo", "domain", "eins": [...]}] for every repo, with an empty list when
the site shows no EIN. Read-only; public data only (each site already shows these).

An EIN counts when it sits next to an EIN label ("EIN", "Tax ID", "Employer
Identification") or in an `ein:` config key. FFC's own EIN (46-2471893) ships in
template files such as ADOPTERS.md, so it is ignored outside freeforcharity.org's
own repo. Placeholder EINs (all one digit, 12-3456789 and the like) are ignored.
"""

from __future__ import annotations

import json
import os
import re
import sys

FFC_EIN = "462471893"
FFC_REPO = "FFC-EX-freeforcharity.org"
CTX_RE = re.compile(
    r"(?i)(?:\bein\b|employer identification|tax[ -]?id|taxid)[^0-9\n]{0,40}(\d{2}-?\d{7})"
)
KEY_RE = re.compile(r"(?i)[\"']?\bein\b[\"']?\s*[:=]\s*[\"'](\d{2}-?\d{7})[\"']")
SKIP_DIRS = {"node_modules", ".git", ".next", "out", "dist", "build", "coverage"}
TEXT_EXT = {".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".mdx", ".html", ".yml", ".yaml", ".txt", ".csv"}
PLACEHOLDER = {"123456789", "987654321", "121234567", "112345678"}


def is_placeholder(ein: str) -> bool:
    return ein in PLACEHOLDER or len(set(ein)) == 1


def eins_in_text(text: str) -> set[str]:
    found = set()
    for rx in (CTX_RE, KEY_RE):
        for m in rx.finditer(text):
            digits = re.sub(r"\D", "", m.group(1))
            if len(digits) == 9 and not is_placeholder(digits):
                found.add(digits)
    return found


def repo_eins(base: str, repo: str) -> list[str]:
    hits: set[str] = set()
    for dp, dns, fns in os.walk(base):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                continue
            fp = os.path.join(dp, fn)
            try:
                if os.path.getsize(fp) > 2_000_000:
                    continue
                with open(fp, encoding="utf-8", errors="replace") as fh:
                    hits |= eins_in_text(fh.read())
            except OSError:
                continue
    if repo != FFC_REPO:
        hits.discard(FFC_EIN)
    return sorted(hits)


def domain_of(repo: str) -> str:
    return repo[len("FFC-EX-"):].lower() if repo.startswith("FFC-EX-") else ""


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    root, out = argv[1], argv[2]
    rows = []
    for repo in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        base = os.path.join(root, repo)
        if os.path.isdir(base):
            rows.append({"repo": repo, "domain": domain_of(repo), "eins": repo_eins(base, repo)})
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rows, fh, indent=2)
        fh.write("\n")
    print(f"{len(rows)} repo(s), {sum(1 for r in rows if r['eins'])} showing an EIN")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
