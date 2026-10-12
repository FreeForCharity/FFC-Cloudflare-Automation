#!/usr/bin/env python3
"""Find where a charity already exists before the charity lane touches it.

The Onboarding Conductor runs this before it queues a `[CHARITY-LANE]` item or
provisions a repo (epic #1618), so the pipeline never builds a second site for a
charity that is already in flight or live. It searches, for an org name and/or a
domain:

  repos    FreeForCharity repos whose name matches (FFC-EX-<domain> and any repo
           whose name contains a candidate slug)
  sites    ffcadmin's docs/sites_list.csv (the live portfolio), by domain
  issues   issues and PRs in every FreeForCharity repo: the org name as a phrase,
           the domain, and each candidate slug (REST search API)
  zones    Cloudflare zones for the domain and slug.<tld> variants, when a
           CLOUDFLARE_API_TOKEN_FFC is present (the cloudflare-prod-read env)

Fuzzy names: an org name yields candidate slugs: the words joined, the words
without legal suffixes (inc, foundation, ...), the full acronym, and every
"acronym of the first k words + the rest joined" form. "Hollywood Sunset Free
Clinic" therefore yields `hsfreeclinic`, which is how that charity's repo is named.

Output is JSON (and a markdown summary with --markdown) listing every match by
source with a strength: `exact` (same domain or repo), `strong` (a full slug equals
a repo/domain stem), or `weak` (a phrase hit in an issue, a slug inside a longer
name). Exit 0 always for a completed search; 1 on an error.

Runs locally under your `gh` auth and in workflow 233 under the job token. Public
data only, nothing is written.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

ORG = "FreeForCharity"
SITES_CSV = f"https://raw.githubusercontent.com/{ORG}/FFC-IN-ffcadmin.org/main/docs/sites_list.csv"
LEGAL_WORDS = {
    "inc", "incorporated", "llc", "corp", "corporation", "co", "ltd", "foundation", "fund",
    "org", "organization", "organisation", "the", "of", "and", "for", "a", "an", "nfp", "npo",
}
MIN_SLUG = 5


# ---------------------------------------------------------------- pure logic


def words(name: str) -> list[str]:
    name = name.replace("&amp;", " and ").replace("&", " and ")
    return [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]


def domain_stem(domain: str) -> str:
    d = domain.lower().strip().removeprefix("https://").removeprefix("http://").split("/")[0]
    d = d.removeprefix("www.")
    return re.sub(r"[^a-z0-9]", "", d.rsplit(".", 1)[0]) if "." in d else re.sub(r"[^a-z0-9]", "", d)


def repo_stem(repo: str) -> str:
    r = repo.lower()
    for prefix in ("ffc-ex-", "ffc-in-", "ffc-"):
        if r.startswith(prefix):
            r = r[len(prefix):]
            break
    if re.search(r"\.(org|com|net|us|info|church|foundation|club|io)$", r):
        r = r.rsplit(".", 1)[0]
    return re.sub(r"[^a-z0-9]", "", r)


def slugs(org: str) -> list[str]:
    """Candidate slugs for an org name, longest first, de-duplicated."""
    ws = words(org)
    core = [w for w in ws if w not in LEGAL_WORDS] or ws
    out = []
    for seq in (ws, core):
        if not seq:
            continue
        out.append("".join(seq))
        out.append("".join(w[0] for w in seq))
        for k in range(1, len(seq)):
            out.append("".join(w[0] for w in seq[:k]) + "".join(seq[k:]))
    seen, uniq = set(), []
    for s in sorted(out, key=len, reverse=True):
        if s not in seen and len(s) >= 3:
            seen.add(s)
            uniq.append(s)
    return uniq


def match_stem(stem: str, cands: list[str], dom_stem: str | None) -> str | None:
    """Strength of the best match between one repo/domain stem and the candidates."""
    if not stem:
        return None
    if dom_stem and stem == dom_stem:
        return "exact"
    for c in cands:
        if len(c) >= MIN_SLUG and c == stem:
            return "strong"
    for c in cands:
        if len(c) >= MIN_SLUG + 2 and (c in stem or (len(stem) >= MIN_SLUG + 2 and stem in c)):
            return "weak"
    return None


def match_repos(repos: list[dict], cands: list[str], dom_stem: str | None) -> list[dict]:
    hits = []
    for r in repos:
        strength = match_stem(repo_stem(r["name"]), cands, dom_stem)
        if strength:
            hits.append({"source": "repos", "strength": strength, "name": r["name"],
                         "archived": bool(r.get("archived") or r.get("isArchived")),
                         "url": f"https://github.com/{ORG}/{r['name']}"})
    return hits


def match_sites(rows: list[dict], cands: list[str], dom_stem: str | None) -> list[dict]:
    hits = []
    for row in rows:
        dom = (row.get("Domain") or "").strip()
        strength = match_stem(domain_stem(dom), cands, dom_stem)
        if strength:
            hits.append({"source": "sites", "strength": strength, "domain": dom,
                         "status": row.get("Status", ""), "repo": row.get("Repo URL", ""),
                         "server": row.get("Server In Use", "")})
    return hits


RANK = {"exact": 0, "strong": 1, "weak": 2}


def summarize(org: str, domain: str | None, cands: list[str], hits: list[dict]) -> dict:
    hits = sorted(hits, key=lambda h: (RANK[h["strength"]], h["source"]))
    best = hits[0]["strength"] if hits else None
    return {
        "org": org,
        "domain": domain,
        "candidates": cands,
        "verdict": "ALREADY_KNOWN" if best in ("exact", "strong") else ("POSSIBLE_MATCH" if best else "NO_MATCH"),
        "matches": hits,
    }


def to_markdown(res: dict) -> str:
    irs = res.get("irs")
    lines = [f"### {res['org'] or res['domain']}: **{res['verdict']}**",
             f"IRS (ProPublica): {irs['name']}, {irs['city']} {irs['state']} (EIN {irs['ein']})" if irs else "",
             f"slugs tried: `{'`, `'.join(res['candidates'][:8])}`" if res["candidates"] else "",
             f"web search next: `{res['web_search_query']}`" if res.get("web_search_query") else ""]
    for h in res["matches"]:
        label = h.get("name") or h.get("domain") or h.get("title") or h.get("zone")
        url = h.get("url") or h.get("repo") or ""
        extra = " (archived)" if h.get("archived") else (f" ({h['state']})" if h.get("state") else "")
        lines.append(f"- {h['strength']} · {h['source']} · {label}{extra} {url}".rstrip())
    return "\n".join(l for l in lines if l)


# ---------------------------------------------------------------- sources


def gh_api(path: str) -> object:
    r = subprocess.run(["gh", "api", path], capture_output=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {r.stderr.strip()[:300]}")
    return json.loads(r.stdout or "null")


def list_repos() -> list[dict]:
    repos, page = [], 1
    while True:
        batch = gh_api(f"orgs/{ORG}/repos?per_page=100&type=all&page={page}")
        repos += [{"name": b["name"], "archived": b.get("archived", False)} for b in batch]
        if len(batch) < 100:
            return repos
        page += 1


def load_sites() -> list[dict]:
    with urllib.request.urlopen(SITES_CSV, timeout=20) as resp:
        return list(csv.DictReader(io.StringIO(resp.read().decode("utf-8"))))


def flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def issue_strength(term: str, title: str, body: str) -> str | None:
    """GitHub search tokenizes a quoted phrase ("Galatians 62" matches any #62),
    so keep a hit only when the whole term really appears: in the title is
    `strong`, in the body `weak`, nowhere `None`."""
    t = flat(term)
    if len(t) < MIN_SLUG:
        return None
    if t in flat(title):
        return "strong"
    if t in flat(body):
        return "weak"
    return None


def search_issues(terms: list[str]) -> list[dict]:
    hits, seen = [], set()
    for term in terms:
        q = urllib.parse.quote(f'org:{ORG} "{term}"')
        res = gh_api(f"search/issues?q={q}&per_page=30")
        for it in res.get("items", []):
            strength = issue_strength(term, it.get("title", ""), it.get("body") or "")
            if not strength or it["html_url"] in seen:
                continue
            seen.add(it["html_url"])
            hits.append({"source": "issues", "strength": strength, "title": it["title"], "state": it["state"],
                         "url": it["html_url"], "term": term})
    return hits


def search_zones(names: list[str]) -> list[dict]:
    token = os.environ.get("CLOUDFLARE_API_TOKEN_FFC")
    if not token:
        return []
    hits = []
    for name in names:
        req = urllib.request.Request(
            f"https://api.cloudflare.com/client/v4/zones?name={urllib.parse.quote(name)}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            for z in json.loads(resp.read().decode("utf-8")).get("result", []):
                hits.append({"source": "zones", "strength": "exact", "zone": z["name"], "state": z.get("status", "")})
    return hits


def propublica(ein: str) -> dict | None:
    """IRS name, city and state for an EIN, from ProPublica's free API; None if unknown."""
    digits = re.sub(r"[^0-9]", "", ein)
    if len(digits) != 9:
        return None
    try:
        with urllib.request.urlopen(
            f"https://projects.propublica.org/nonprofits/api/v2/organizations/{digits}.json", timeout=20
        ) as resp:
            o = json.loads(resp.read().decode("utf-8")).get("organization") or {}
    except Exception:  # noqa: BLE001 - unknown EIN or API down: no IRS facts
        return None
    if not o.get("name"):
        return None
    return {"ein": digits, "name": o["name"], "city": o.get("city", ""), "state": o.get("state", ""),
            "subsection": o.get("subsection_code")}


def web_query(org: str, irs: dict | None) -> str:
    """The query a session should run in a web search to find the charity's own site."""
    if irs:
        return f'"{irs["name"]}" {irs["city"]} {irs["state"]} website'.strip()
    return f'"{org}" nonprofit website'


def lookup(org: str, domain: str | None, sources: set[str], repos=None, sites=None, ein: str = "") -> dict:
    irs = propublica(ein) if ein else None
    cands = slugs(org) if org else []
    if irs:
        cands += [s for s in slugs(irs["name"]) if s not in cands]
    dom_stem = domain_stem(domain) if domain else None
    if dom_stem and dom_stem not in cands:
        cands = [dom_stem] + cands
    hits: list[dict] = []
    if "repos" in sources:
        hits += match_repos(repos if repos is not None else list_repos(), cands, dom_stem)
    if "sites" in sources:
        hits += match_sites(sites if sites is not None else load_sites(), cands, dom_stem)
    if "issues" in sources:
        terms = ([org] if org else []) + ([domain] if domain else []) + [c for c in cands[:4] if len(c) >= MIN_SLUG + 2]
        hits += search_issues(terms)
    if "zones" in sources and (domain or cands):
        names = [domain] if domain else []
        names += [f"{c}.org" for c in cands[:3] if len(c) >= MIN_SLUG]
        hits += search_zones(names)
    res = summarize(org, domain, cands, hits)
    res["irs"] = irs
    res["web_search_query"] = web_query(org, irs)
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", default="", help="organization name as the application gives it")
    ap.add_argument("--domain", default="", help="desired or existing domain, if any")
    ap.add_argument("--ein", default="", help="EIN from the application (adds the IRS name, city, state)")
    ap.add_argument("--sources", default="repos,sites,issues,zones")
    ap.add_argument("--markdown", action="store_true", help="print a markdown summary instead of JSON")
    args = ap.parse_args(argv)
    if not args.org and not args.domain:
        print("give --org and/or --domain", file=sys.stderr)
        return 1
    if args.domain and not re.fullmatch(r"[A-Za-z0-9.-]{3,253}", args.domain):
        print(f"invalid domain {args.domain!r}", file=sys.stderr)
        return 1
    try:
        res = lookup(args.org.strip()[:200], args.domain.strip().lower() or None, set(args.sources.split(",")),
                     ein=args.ein)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(to_markdown(res) if args.markdown else json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
