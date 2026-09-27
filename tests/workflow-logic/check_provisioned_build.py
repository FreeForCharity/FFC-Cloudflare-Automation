"""Check a provisioned site's BUILT pages (used by workflow 748).

    python3 tests/workflow-logic/check_provisioned_build.py --charity riverbend-pantry --out site/out

A template's own CI can pass over a site that is wrong: before
FreeForCharity/FFC-Cloudflare-Automation#1391 was fixed, every Single Page
check was green while the charity's Donate section embedded Free For Charity's
endowment form. So this reads what a visitor gets, the static export, and
fails when:

- the charity's Donate or Volunteer link is missing: its own URL when the
  sample gives one (`expect` "url"), else a mailto: to its contact address
  with the donate / volunteer subject (`expect` "mailto");
- any page links or embeds one of Free For Charity's own donation, volunteer,
  Facebook or application-form targets. FFC's own donation-policy page is
  exempt: it is FFC's document, published on every site by design.
"""

from __future__ import annotations

import argparse
import html
import pathlib
import re
import sys
import urllib.parse

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_sample_charities import load  # noqa: E402

# Free For Charity's own third-party targets (the Single Page template's
# `siteConfig.integrations`). None may appear on a charity's pages.
FFC_TARGETS = (
    "free-for-charity-endowment-fund",
    "free-for-charity-state-college",
    "facebook.com/freeforcharity",
    "forms.office.com/r/vePxGq6JqG",
)
# Pages that are FFC's own documents, published on every site by design.
EXEMPT_PAGE = re.compile(r"(^|/)free-for-charity-donation-policy(/|\.html$)")
ATTR_RE = re.compile(r"""\b(?:href|src)\s*=\s*("([^"]*)"|'([^']*)')""", re.I)


def page_urls(out: pathlib.Path) -> dict[str, list[str]]:
    """Every href/src value on every built HTML page, entity-decoded."""
    pages: dict[str, list[str]] = {}
    for f in sorted(out.rglob("*.html")):
        text = f.read_text(encoding="utf-8", errors="replace")
        rel = f.relative_to(out).as_posix()
        pages[rel] = [html.unescape(m.group(2) or m.group(3) or "") for m in ATTR_RE.finditer(text)]
    return pages


def has_mailto(urls: list[str], email: str, subject_prefix: str) -> bool:
    for u in urls:
        if not u.lower().startswith("mailto:"):
            continue
        addr, _, query = u[len("mailto:") :].partition("?")
        subject = urllib.parse.parse_qs(query).get("subject", [""])[0]
        if addr.lower() == email.lower() and subject.startswith(subject_prefix):
            return True
    return False


def findings(charity: dict, out: pathlib.Path) -> list[str]:
    pages = page_urls(out)
    if not pages:
        return [f"no built HTML pages under {out}"]
    problems: list[str] = []
    for page, urls in pages.items():
        if EXEMPT_PAGE.search(page):
            continue
        for u in urls:
            for target in FFC_TARGETS:
                if target in u:
                    problems.append(f"{page}: links Free For Charity's own target {u}")
    every = [u for urls in pages.values() for u in urls]
    inputs, expect = charity["inputs"], charity["expect"]
    email = inputs["footer_email"].strip()
    for kind, key, subject in (
        ("donate", "donation_url", "Donating to "),
        ("volunteer", "volunteer_url", "Volunteering with "),
    ):
        if expect[kind] == "url":
            want = inputs[key].strip()
            if want not in every:
                problems.append(f"no page links the charity's own {kind} URL {want}")
        elif not has_mailto(every, email, subject):
            problems.append(f"no page offers a {kind} mailto: to {email} (subject '{subject}...')")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--charity", required=True)
    ap.add_argument("--out", required=True, help="the site's static export directory")
    a = ap.parse_args()
    matches = [c for c in load() if c["id"] == a.charity]
    if not matches:
        print(f"::error::no sample charity with id {a.charity!r}")
        return 2
    problems = findings(matches[0], pathlib.Path(a.out))
    for p in problems:
        print(f"::error::{p}")
    if not problems:
        print(f"{a.charity}: built pages link its own donate and volunteer targets and none of FFC's")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
