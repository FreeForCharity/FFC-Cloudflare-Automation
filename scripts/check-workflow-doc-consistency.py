#!/usr/bin/env python3
"""Consistency guard between the workflow files and the operator safety table.

Fails when:
  1. any `.github/workflows/*.yml` begins with a UTF-8 byte-order mark (BOM),
     which hides the `name:` line from `^name:` parsers;
  2. an operator-facing workflow (display name starts with `NN.`) has no row in
     docs/workflow-safety-and-approvals.md;
  3. the safety table references a display number that no workflow declares
     (a stale / wrong-number row).

Repo-internal / CI workflows are intentionally excluded from the operator table.
Run from the repo root: `python3 scripts/check-workflow-doc-consistency.py`.
"""
import glob
import re
import sys

WF_GLOB = ".github/workflows/*.yml"
DOC = "docs/workflow-safety-and-approvals.md"

# Display numbers intentionally NOT in the operator safety table (CI / repo plumbing).
EXCLUDE = {"703", "720", "721", "722", "723", "724", "725", "727", "728"}


def two(n):
    return str(int(n)).zfill(3)


def main():
    errors = []

    # 1) No BOM at the start of any workflow YAML.
    for f in sorted(glob.glob(WF_GLOB)):
        with open(f, "rb") as fh:
            if fh.read(3) == b"\xef\xbb\xbf":
                errors.append(f"{f}: starts with a UTF-8 BOM; strip it.")

    # 2) Display numbers declared by workflow name: fields (BOM-tolerant).
    wf_nums = {}
    for f in sorted(glob.glob(WF_GLOB)):
        txt = open(f, encoding="utf-8-sig").read()
        m = re.search(r"^name:\s*['\"]?(\d{2,3})\.", txt, re.M)
        if m:
            wf_nums[two(m.group(1))] = f

    # 3) Display numbers covered by the safety table (expand A-B / A-B en-dash ranges).
    covered = set()
    for line in open(DOC, encoding="utf-8"):
        m = re.match(r"\|\s*(\d{2,3})(?:\s*[–-]\s*(\d{2,3}))?\s*\|", line)
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        for n in range(lo, hi + 1):
            covered.add(two(n))

    # Coverage: every non-excluded workflow must have a table row.
    for num, f in sorted(wf_nums.items()):
        if num in EXCLUDE:
            continue
        if num not in covered:
            errors.append(f"workflow {num} ({f}) has no row in {DOC}")

    # Phantom rows: every table number must map to a real workflow.
    for num in sorted(covered):
        if num not in wf_nums:
            errors.append(f"{DOC} has a row for workflow {num} but no workflow declares it")

    if errors:
        print("Workflow / safety-doc consistency check FAILED:")
        for e in errors:
            print("  -", e)
        return 1
    print(
        f"Workflow / safety-doc consistency OK: "
        f"{len(wf_nums)} workflows, {len(covered)} table rows covered."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
