#!/usr/bin/env python3
"""One-off research probe: which public sources can verify a nonprofit from its EIN?

Read-only. Stdlib only. Run from GitHub Actions because cloud agent containers are
refused at these hosts. Prints a Markdown report to stdout.
"""
import csv
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile

UA = "Mozilla/5.0 (X11; Linux x86_64) FFC-ein-provider-probe/1.0"
EINS = {
    "462471893": "Free For Charity (control)",
    "530196605": "American Red Cross (large, old)",
    "991579845": "new applicant EIN from the thread",
    "460650798": "applicant in coverage report",
    "474450032": "applicant in coverage report",
    "510172118": "applicant in coverage report",
    "833315573": "EIN found in repo fixtures",
}
OUT = []


def p(line=""):
    OUT.append(line)
    print(line, flush=True)


def fetch(url, max_bytes=None, timeout=60, headers=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(max_bytes) if max_bytes else r.read()
            return r.status, dict(r.headers), body, time.time() - t, r.geturl()
    except urllib.error.HTTPError as e:
        try:
            body = e.read(4000)
        except Exception:
            body = b""
        return e.code, dict(e.headers or {}), body, time.time() - t, url
    except Exception as e:  # noqa: BLE001
        return None, {}, str(e).encode(), time.time() - t, url


def mentions(body, ein):
    txt = body.decode("utf-8", "replace")
    dashed = f"{ein[:2]}-{ein[2:]}"
    return (ein in txt) or (dashed in txt)


def title(body):
    m = re.search(rb"<title[^>]*>(.*?)</title>", body or b"", re.S | re.I)
    return m.group(1).decode("utf-8", "replace").strip()[:90] if m else ""


# ---------------------------------------------------------------- ProPublica
p("## ProPublica Nonprofit Explorer API v2")
for ein, label in EINS.items():
    s, h, b, dt, _ = fetch(f"https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json")
    if s == 200:
        d = json.loads(b)
        o = d.get("organization", {})
        filings = d.get("filings_with_data") or []
        p(
            f"- {ein} ({label}): HTTP 200 name={o.get('name')!r} subsection={o.get('subsection_code')} "
            f"ruling={o.get('ruling_date')} ntee={o.get('ntee_code')} updated={o.get('updated_at')} "
            f"filings_with_data={len(filings)} latest_tax_prd={filings[0].get('tax_prd') if filings else None}"
        )
    else:
        p(f"- {ein} ({label}): HTTP {s} {b[:120]!r}")
    time.sleep(0.4)

# ---------------------------------------------------------------- IRS bulk
p("\n## IRS bulk downloads (headers)")
BULK = {
    "pub78": "https://apps.irs.gov/pub/epostcard/data-download-pub78.zip",
    "revocation": "https://apps.irs.gov/pub/epostcard/data-download-revocationExemptList.zip",
    "epostcard_990N": "https://apps.irs.gov/pub/epostcard/data-download-epostcard.zip",
    "bmf_eo1": "https://www.irs.gov/pub/irs-soi/eo1.csv",
    "bmf_eo2": "https://www.irs.gov/pub/irs-soi/eo2.csv",
    "bmf_eo3": "https://www.irs.gov/pub/irs-soi/eo3.csv",
    "bmf_eo4": "https://www.irs.gov/pub/irs-soi/eo4.csv",
}
blobs = {}
for k, u in BULK.items():
    s, h, b, dt, _ = fetch(u, timeout=300)
    p(f"- {k}: HTTP {s} bytes={len(b) if s == 200 else 0} last-modified={h.get('Last-Modified')} t={dt:.1f}s {u}")
    if s == 200:
        blobs[k] = b


def zip_lines(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    name = z.namelist()[0]
    return name, z.read(name).decode("latin-1").splitlines()


p("\n## Lookups in IRS bulk data")
idx = {}
for k in ("pub78", "revocation", "epostcard_990N"):
    if k not in blobs:
        continue
    name, lines = zip_lines(blobs[k])
    p(f"- {k}: file {name}, {len(lines)} rows; sample row: {lines[1][:140] if len(lines) > 1 else lines[0][:140]!r}")
    idx[k] = {}
    for ln in lines:
        e = ln.split("|", 1)[0].strip()
        if e in EINS:
            idx[k].setdefault(e, []).append(ln[:200])
revoked_sample = None
if "revocation" in blobs:
    _, rl = zip_lines(blobs["revocation"])
    for ln in reversed(rl):
        parts = ln.split("|")
        if len(parts) > 8 and re.fullmatch(r"\d{9}", parts[0]):
            revoked_sample = parts[0]
            p(f"- revoked control picked from end of list: {ln[:200]!r}")
            break

bmf = {}
bmf_header = None
for k in ("bmf_eo1", "bmf_eo2", "bmf_eo3", "bmf_eo4"):
    if k not in blobs:
        continue
    r = csv.reader(io.StringIO(blobs[k].decode("latin-1")))
    hdr = next(r)
    bmf_header = hdr
    for row in r:
        if row and (row[0] in EINS or row[0] == revoked_sample):
            bmf[row[0]] = dict(zip(hdr, row))
p(f"- BMF header: {bmf_header}")

for ein, label in list(EINS.items()) + ([(revoked_sample, "revoked control")] if revoked_sample else []):
    b = bmf.get(ein)
    bmf_txt = (
        f"BMF: {b.get('NAME')} {b.get('CITY')} {b.get('STATE')} subsection={b.get('SUBSECTION')} "
        f"ruling={b.get('RULING')} deductibility={b.get('DEDUCTIBILITY')} foundation={b.get('FOUNDATION')} "
        f"status={b.get('STATUS')} tax_period={b.get('TAX_PERIOD')} filing_req={b.get('FILING_REQ_CD')}"
        if b
        else "BMF: not found"
    )
    p(f"- {ein} ({label}): {bmf_txt}")
    for k in ("pub78", "revocation", "epostcard_990N"):
        hits = idx.get(k, {}).get(ein)
        p(f"    - {k}: {'FOUND ' + repr(hits[:1]) if hits else 'not found'}")

# ---------------------------------------------------------------- web pages
p("\n## Public pages a site or reviewer could link (signed out)")
PAGES = [
    ("IRS TEOS details", "https://apps.irs.gov/app/eos/details/?ein={ein}"),
    ("IRS TEOS allSearch", "https://apps.irs.gov/app/eos/allSearch?ein1={ein}&names=&resultsPerPage=25&indexOfFirstRow=0&dispatchMethod=searchAll&city=&state=All+States&country=US&postDateFrom=&postDateTo=&exemptTypeCode=al&deductibility=all&sortColumn=orgName&isDescending=false"),
    ("IRS TEOS new", "https://www.irs.gov/charities-non-profits/tax-exempt-organization-search?ein={ein}"),
    ("ProPublica page", "https://projects.propublica.org/nonprofits/organizations/{ein}"),
    ("Charity Navigator", "https://www.charitynavigator.org/ein/{ein}"),
    ("Every.org", "https://www.every.org/ein/{ein}"),
    ("Every.org partner API no key", "https://partners.every.org/v0.2/nonprofit/{ein}"),
    ("Candid GuideStar", "https://www.guidestar.org/profile/{d}"),
    ("Candid app", "https://app.candid.org/profile/{d}"),
    ("CauseIQ", "https://www.causeiq.com/search/organizations/?q={ein}"),
    ("Charity Navigator API no key", "https://api.charitynavigator.org/graphql"),
    ("CharityAPI no key", "https://api.charityapi.org/api/organizations/{ein}"),
    ("OrgHunter no key", "http://data.orghunter.com/v1/charitysearch?ein={ein}"),
    ("990 Finder", "https://990finder.foundationcenter.org/990results.aspx?990_type=&fn=&st=&zp=&ei={ein}"),
]
for ein in ["462471893", "991579845", "530196605"]:
    d = f"{ein[:2]}-{ein[2:]}"
    for lbl, tpl in PAGES:
        u = tpl.format(ein=ein, d=d)
        s, h, b, dt, final = fetch(u, max_bytes=600000, timeout=40)
        snip = repr(b[:100]) if s and s != 200 else ""
        p(
            f"- {ein} {lbl}: HTTP {s} ein_on_page={mentions(b, ein) if s else '-'} "
            f"final={final[:110]} title={title(b)!r} snippet={snip}"
        )
        time.sleep(0.3)

# ---------------------------------------------------------------- Candid seal widget for comparison
p("\n## Badge/widget hosts")
for lbl, u in [
    ("Candid seal widget host", "https://widgets.guidestar.org/"),
    ("Charity Navigator badge doc", "https://www.charitynavigator.org/about-us/our-methodology/ratings/rating-badges/"),
]:
    s, h, b, dt, final = fetch(u, max_bytes=200000)
    p(f"- {lbl}: HTTP {s} final={final} title={title(b)!r}")

with open("probe-report.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(OUT) + "\n")
sys.exit(0)
