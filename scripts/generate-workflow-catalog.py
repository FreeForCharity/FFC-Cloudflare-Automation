#!/usr/bin/env python3
"""Generate the workflow catalog from the workflow files themselves.

Parses every .github/workflows/*.yml plus docs/workflow-safety-and-approvals.md and emits:
  1. docs/workflow-catalog.json  — machine-readable catalog (consumed by the public
     automation page on ffcadmin.org and by AI agents picking a workflow);
  2. the AUTO-GENERATED CATALOG section of .github/workflows/README.md (between the
     <!-- catalog:begin --> / <!-- catalog:end --> markers).

Run from the repo root:  python3 scripts/generate-workflow-catalog.py
CI treats a dirty regeneration as a failure (run with --check).
"""
import glob
import json
import re
import sys

WF_GLOB = ".github/workflows/*.yml"
SAFETY_DOC = "docs/workflow-safety-and-approvals.md"
OUT_JSON = "docs/workflow-catalog.json"
README = ".github/workflows/README.md"

CATEGORIES = {
    "1": ("Cloudflare / DNS / Domain", "CF"),
    "2": ("WHMCS", "WHMCS"),
    "3": ("Microsoft (M365 / Azure / Graph)", "MS"),
    "4": ("Zeffy", "ZEFFY"),
    "5": ("Google", "GOOGLE"),
    "6": ("WPMUDEV", "WPMUDEV"),
    "7": ("GitHub (Website + Repo)", "GH"),
    "8": ("Reserved", "-"),
    "9": ("Reserved", "-"),
}


def parse_safety_table():
    """number -> {level, approvalEnv, guard} from the safety doc table."""
    rows = {}
    try:
        lines = open(SAFETY_DOC, encoding="utf-8").read().split("\n")
    except FileNotFoundError:
        return rows
    for ln in lines:
        m = re.match(
            r"^\|\s*(\d{3})(?:\s*[–-]\s*(\d{3}))?\s*\|([^|]+)\|([^|]+)\|([^|]+)\|([^|]*)\|?\s*$",
            ln,
        )
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        level = m.group(4).strip()
        env = m.group(5).strip()
        guard = m.group(6).strip()
        for n in range(lo, hi + 1):
            rows[n] = {"level": level, "approvalEnv": env, "guard": guard}
    return rows


def parse_workflow(path):
    txt = open(path, encoding="utf-8-sig").read()
    m = re.search(r"^name:\s*['\"]?(\d{3})\.\s*(.*?)['\"]?\s*$", txt, re.M)
    if not m:
        return None
    number = int(m.group(1))
    display = m.group(2)
    # tag like [CF+M365] at the end of the display name
    tm = re.search(r"\[([A-Za-z0-9+\-]+)\]\s*$", display)
    apis = tm.group(1).split("+") if tm else []
    title = re.sub(r"\s*\[[A-Za-z0-9+\-]+\]\s*$", "", display).strip()
    # triggers = top-level keys of the on: block
    triggers = []
    om = re.search(r"^on:\s*\n((?:[ \t]+.*\n?)+)", txt, re.M)
    if om:
        triggers = re.findall(r"^  ([a-z_]+):", om.group(1), re.M)
    # environments used
    envs = sorted(set(re.findall(r"^\s+environment:\s*([A-Za-z0-9_\-]+)\s*$", txt, re.M)))
    # description: first contiguous comment block after the name/run-name lines
    desc = ""
    dm = re.search(r"\n\n((?:#[^\n]*\n)+)", txt)
    if dm:
        desc = " ".join(l.lstrip("# ").strip() for l in dm.group(1).strip().split("\n"))
    return {
        "number": number,
        "title": title,
        "apis": apis,
        "file": path.replace("\\", "/").split("/")[-1],
        "triggers": sorted(set(triggers)),
        "environments": envs,
        "description": desc,
    }


def build():
    safety = parse_safety_table()
    items = []
    for f in sorted(glob.glob(WF_GLOB)):
        w = parse_workflow(f)
        if not w:
            continue
        s = safety.get(w["number"], {})
        w["safetyLevel"] = s.get("level", "")
        w["approvalEnv"] = s.get("approvalEnv", "")
        w["guard"] = s.get("guard", "")
        cat, code = CATEGORIES.get(str(w["number"])[0], ("Unknown", "?"))
        w["category"] = cat
        w["categoryCode"] = code
        items.append(w)
    items.sort(key=lambda x: x["number"])
    return {
        "_generated": "scripts/generate-workflow-catalog.py — do not hand-edit",
        "scheme": {
            "convention": "3-digit category-first: first digit = the API/system the workflow targets",
            "categories": {k: v[0] for k, v in CATEGORIES.items()},
            "tagRule": "the [TAG] lists ALL APIs the workflow CALLS (not services configured-for, not plumbing like KV auth or issue comments)",
        },
        "workflows": items,
    }


def render_markdown(cat):
    out = ["<!-- catalog:begin -->", ""]
    out.append("## Complete workflow catalog (auto-generated)")
    out.append("")
    out.append("> Regenerate with `python3 scripts/generate-workflow-catalog.py` — do not hand-edit")
    out.append("> this section. Machine-readable version: `docs/workflow-catalog.json`.")
    out.append("")
    cur = None
    for w in cat["workflows"]:
        c = w["category"]
        if c != cur:
            out.append(f"### {str(w['number'])[0]}xx — {c}")
            out.append("")
            out.append("| # | Workflow | File | Triggers | Safety | Approval env |")
            out.append("| --- | --- | --- | --- | --- | --- |")
            cur = c
        trig = ", ".join(w["triggers"]) or "—"
        level = w["safetyLevel"] or "(repo plumbing)"
        env = w["approvalEnv"] or "—"
        tag = "+".join(w["apis"])
        out.append(
            f"| {w['number']} | {w['title']} [{tag}] | `{w['file']}` | {trig} | {level} | {env} |"
        )
        if c != cur:
            out.append("")
    out.append("")
    out.append("<!-- catalog:end -->")
    return "\n".join(out)


def main():
    check = "--check" in sys.argv
    cat = build()
    js = json.dumps(cat, indent=2) + "\n"
    md = render_markdown(cat)

    readme = open(README, encoding="utf-8").read()
    if "<!-- catalog:begin -->" in readme:
        new_readme = re.sub(
            r"<!-- catalog:begin -->.*?<!-- catalog:end -->", md, readme, flags=re.S
        )
    else:
        new_readme = readme.rstrip() + "\n\n" + md + "\n"

    try:
        old_js = open(OUT_JSON, encoding="utf-8").read()
    except FileNotFoundError:
        old_js = ""

    if check:
        dirty = []
        if old_js != js:
            dirty.append(OUT_JSON)
        if new_readme != readme:
            dirty.append(README)
        if dirty:
            print("Catalog out of date; regenerate with scripts/generate-workflow-catalog.py:")
            for d in dirty:
                print("  -", d)
            return 1
        print(f"Catalog up to date ({len(cat['workflows'])} workflows).")
        return 0

    open(OUT_JSON, "w", encoding="utf-8", newline="\n").write(js)
    open(README, "w", encoding="utf-8", newline="\n").write(new_readme)
    print(f"Wrote {OUT_JSON} + README catalog section ({len(cat['workflows'])} workflows).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
