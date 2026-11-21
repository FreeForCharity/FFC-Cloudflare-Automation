#!/usr/bin/env python3
"""Research probe: check every EIN FFC holds for its charities against the IRS
(Pub 78, revocation list, EO BMF) and ProPublica. Read-only.

Inputs (prepared by the workflow):
  work/repos/<repo>/        shallow clones of public FFC-EX-* repos
  work/repos-failed.txt     repos that could not be cloned (private or missing)
  work/artifacts/**.json    downloaded WHMCS-derived artifacts (232 verdicts, pending list)
Outputs: probe-ffc-eins.md (Markdown report)
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile

UA = "FFC-ein-check/1.0"
OUT = []
EIN_RE = re.compile(r"\b(\d{2})-?(\d{7})\b")
CTX_RE = re.compile(
    r"(?i)(?:\bein\b|employer identification|tax[ -]?id|taxid)[^0-9\n]{0,40}(\d{2}-?\d{7})"
)
SKIP_DIRS = {"node_modules", ".git", ".next", "out", "dist", "build", "coverage", "public/_next"}
TEXT_EXT = {".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".mdx", ".html", ".yml", ".yaml", ".txt", ".csv"}
PLACEHOLDER = {"123456789", "000000000", "999999999", "987654321", "121234567", "112345678"}


def p(s=""):
    OUT.append(s)
    print(s, flush=True)


def fetch(url, timeout=300):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:  # noqa: BLE001
        return None, str(e).encode()


# ------------------------------------------------------------ collect from repos
found = {}  # ein -> list of (source, detail)


def add(ein, source, detail):
    if ein in PLACEHOLDER or len(set(ein)) == 1:
        return
    found.setdefault(ein, []).append((source, detail))


repo_root = "work/repos"
repos = sorted(os.listdir(repo_root)) if os.path.isdir(repo_root) else []
repos_no_ein = []
for repo in repos:
    base = os.path.join(repo_root, repo)
    hits = set()
    for dp, dns, fns in os.walk(base):
        rel = os.path.relpath(dp, base)
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in fns:
            if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                continue
            fp = os.path.join(dp, fn)
            try:
                if os.path.getsize(fp) > 2_000_000:
                    continue
                txt = open(fp, encoding="utf-8", errors="replace").read()
            except Exception:  # noqa: BLE001
                continue
            for m in CTX_RE.finditer(txt):
                e = re.sub(r"\D", "", m.group(1))
                hits.add((e, os.path.join(rel, fn)))
            # config keys like ein: '12-3456789' or "ein": "..."
            for m in re.finditer(r"(?i)[\"']?\bein\b[\"']?\s*[:=]\s*[\"'](\d{2}-?\d{7})[\"']", txt):
                hits.add((re.sub(r"\D", "", m.group(1)), os.path.join(rel, fn)))
    eins = {}
    for e, f in hits:
        eins.setdefault(e, set()).add(f)
    if not eins:
        repos_no_ein.append(repo)
    for e, fs in eins.items():
        add(e, "repo", f"{repo} ({', '.join(sorted(fs)[:2])})")

failed = []
if os.path.exists("work/repos-failed.txt"):
    failed = [l.strip() for l in open("work/repos-failed.txt") if l.strip()]

# ------------------------------------------------------------ collect from WHMCS-derived artifacts
art_hits = 0


def walk(obj, ctx):
    global art_hits
    if isinstance(obj, dict):
        label = None
        for k in ("charity", "companyname", "organization", "orgName", "name", "domain", "orderid", "order_id", "orderId", "clientid", "client_id"):
            if k in obj and isinstance(obj[k], (str, int)) and str(obj[k]).strip():
                label = (label + " / " if label else "") + f"{k}={obj[k]}"
        ctx2 = label or ctx
        for k, v in obj.items():
            if re.search(r"(?i)(^|_)ein($|_|value)|einValue|^ein$", k) and isinstance(v, (str, int)):
                s = re.sub(r"\D", "", str(v))
                if len(s) == 9:
                    add(s, "whmcs", f"{ctx2} [{k}]")
                    art_hits += 1
            else:
                walk(v, ctx2)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, ctx)


art_files = []
for dp, _, fns in os.walk("work/artifacts"):
    for fn in fns:
        if fn.endswith(".json"):
            fp = os.path.join(dp, fn)
            art_files.append(fp)
            try:
                walk(json.load(open(fp, encoding="utf-8")), fn)
            except Exception as e:  # noqa: BLE001
                p(f"- could not parse {fp}: {e}")

# ------------------------------------------------------------ IRS files
def zip_lines(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    return z.read(z.namelist()[0]).decode("latin-1").splitlines()


s, b = fetch("https://apps.irs.gov/pub/epostcard/data-download-pub78.zip")
pub78 = {}
for ln in zip_lines(b) if s == 200 else []:
    parts = ln.split("|")
    if len(parts) >= 6:
        pub78[parts[0].strip()] = parts
s, b = fetch("https://apps.irs.gov/pub/epostcard/data-download-revocation.zip")
revoked = {}
for ln in zip_lines(b) if s == 200 else []:
    parts = ln.split("|")
    if len(parts) >= 11 and re.fullmatch(r"\d{9}", parts[0].strip()):
        revoked.setdefault(parts[0].strip(), []).append(parts)
bmf = {}
for n in (1, 2, 3, 4):
    s, b = fetch(f"https://www.irs.gov/pub/irs-soi/eo{n}.csv")
    if s != 200:
        p(f"- BMF eo{n}.csv HTTP {s}")
        continue
    r = csv.reader(io.StringIO(b.decode("latin-1")))
    hdr = next(r)
    for row in r:
        if row and row[0] in found:
            bmf[row[0]] = dict(zip(hdr, row))

# ------------------------------------------------------------ ProPublica + verdicts
rows = []
for ein in sorted(found):
    s, b = fetch(f"https://projects.propublica.org/nonprofits/api/v2/organizations/{ein}.json", timeout=30)
    pp = "not found"
    pp_name = ""
    if s == 200:
        o = json.loads(b).get("organization", {})
        pp_name = o.get("name") or ""
        pp = f"subsection {o.get('subsection_code')}"
        if pp_name == "Unknown Organization":
            pp = "placeholder (no record)"
    elif s != 404:
        pp = f"HTTP {s}"
    time.sleep(0.3)
    in78 = ein in pub78
    rev = revoked.get(ein)
    rev_txt = "not listed"
    rev_active = False
    if rev:
        last = rev[-1]
        reinst = last[11].strip() if len(last) > 11 else ""
        rev_txt = f"REVOKED {last[9].strip()} (posted {last[10].strip()})" + (f", reinstated {reinst}" if reinst else "")
        rev_active = not reinst
    bm = bmf.get(ein)
    bmf_txt = f"{bm['NAME']} ({bm['CITY']}, {bm['STATE']}) sub {bm['SUBSECTION']} deduct {bm['DEDUCTIBILITY']}" if bm else "not in BMF"
    irs_name = (pub78[ein][1] if in78 else (bm["NAME"] if bm else (rev[-1][1] if rev else "")))
    problem = []
    if rev_active:
        problem.append("revoked")
    if not in78:
        problem.append("not in Pub 78")
    if not bm:
        problem.append("not in BMF")
    elif bm["SUBSECTION"] != "03":
        problem.append(f"BMF subsection {bm['SUBSECTION']}")
    if s == 200 and pp.startswith("subsection") and "3" not in pp.split()[-1]:
        problem.append("ProPublica not 3")
    srcs = "; ".join(f"{a}: {d}" for a, d in found[ein])[:300]
    rows.append((bool(problem), ein, irs_name or pp_name, "yes" if in78 else "NO", rev_txt, bmf_txt, pp, ", ".join(problem) or "ok", srcs))

rows.sort(key=lambda r: (not r[0], r[2]))
p(f"## Summary\n- repos scanned: {len(repos)}; repos with no EIN found: {len(repos_no_ein)}; repos not cloneable: {len(failed)}")
p(f"- WHMCS artifact files read: {len(art_files)} ({art_hits} EIN fields)")
p(f"- distinct EINs: {len(found)}; with a problem: {sum(1 for r in rows if r[0])}")
p(f"- IRS files: pub78 rows {len(pub78)}, revocation EINs {len(revoked)}")
p("\n## Results (problems first)\n")
p("| EIN | IRS / ProPublica name | Pub 78 | Revocation list | BMF | ProPublica | Verdict | Where FFC holds it |")
p("| --- | --- | --- | --- | --- | --- | --- | --- |")
for r in rows:
    p(f"| {r[1][:2]}-{r[1][2:]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} | {r[6]} | {r[7]} | {r[8]} |")
p("\n## Repos with no EIN found\n")
p(", ".join(repos_no_ein))
p("\n## Repos that could not be cloned\n")
p(", ".join(failed))
with open("probe-ffc-eins.md", "w", encoding="utf-8") as fh:
    fh.write("\n".join(OUT) + "\n")
