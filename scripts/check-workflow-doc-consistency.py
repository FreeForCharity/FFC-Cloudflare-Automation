#!/usr/bin/env python3
"""Consistency guard between the workflow files and the operator safety table.

Fails when:
  1. any `.github/workflows/*.yml` begins with a UTF-8 byte-order mark (BOM),
     which hides the `name:` line from `^name:` parsers;
  2. an operator-facing workflow (display name starts with `NN.`) has no row in
     docs/workflow-safety-and-approvals.md;
  3. the safety table references a display number that no workflow declares
     (a stale / wrong-number row);
  4. two workflow files declare the SAME display number. This one is a
     detection gap as much as a numbering rule: the number→file map below used
     to be a plain dict assignment, so the second file for a number silently
     overwrote the first and the loser became invisible to checks 2 and 3 *and*
     to the EXCLUDE/gated-environment assertion. The count stayed right (98
     numbers for 99 files), which is why nothing looked wrong. Two open PRs
     both claiming display number 229 — #904 (domain order add) and #825
     (client field populate) — is what surfaced it; because their filenames
     differ, git merges both cleanly and no conflict ever appears.

Repo-internal / CI workflows are intentionally excluded from the operator table.
Run from the repo root: `python3 scripts/check-workflow-doc-consistency.py`.
"""
import glob
import re
import sys

WF_GLOB = ".github/workflows/*.yml"
DOC = "docs/workflow-safety-and-approvals.md"

# Display numbers intentionally NOT in the operator safety table (CI / repo plumbing).
EXCLUDE = {"721", "722", "723", "724", "725", "727", "728"}

# Environments with required reviewers (approval gates). Source of truth: the
# "Environment approval gates" layer in docs/workflow-safety-and-approvals.md,
# audited by workflow 730. A workflow that pauses at one of these gates is
# operator-facing by definition and must NOT be in EXCLUDE — the gate approver
# needs a safety-table row to judge the run against (703 hid this way until
# 2026-07-20). Update alongside the doc after any Settings → Environments change.
GATED_ENVS = {
    "cloudflare-prod",
    "cloudflare-prod-write",
    "github-prod",
    "m365-prod",
    "whmcs-prod",
    "wpmudev-prod",
}


def declared_environments(txt):
    """Environment names a workflow YAML declares (inline and block forms)."""
    envs = set(re.findall(r"^\s*environment:\s*['\"]?([A-Za-z0-9_-]+)['\"]?\s*$", txt, re.M))
    envs.discard("name")  # block form: `environment:` followed by `name:`
    for m in re.finditer(r"^\s*environment:\s*$", txt, re.M):
        rest = txt[m.end() :]
        n = re.search(r"^\s*name:\s*['\"]?([A-Za-z0-9_-]+)", rest, re.M)
        if n:
            envs.add(n.group(1))
    return envs


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
    # Collect every file per number — NOT `wf_nums[num] = f`, which drops all
    # but the last file for a duplicated number (see failure mode 4 above).
    wf_files = {}
    for f in sorted(glob.glob(WF_GLOB)):
        txt = open(f, encoding="utf-8-sig").read()
        m = re.search(r"^name:\s*['\"]?(\d{2,3})\.", txt, re.M)
        if m:
            wf_files.setdefault(two(m.group(1)), []).append(f)

    # 2a) Display numbers must be unique. Reported before the coverage checks
    # because a duplicate makes their verdict unreliable, not merely incomplete.
    for num, files in sorted(wf_files.items()):
        if len(files) > 1:
            errors.append(
                f"display number {num} is declared by {len(files)} workflows: "
                f"{', '.join(files)} — renumber all but one. Distinct filenames "
                "mean git merges both without a conflict, and every check keyed "
                "on the display number can only see one of them."
            )

    # One representative file per number for the checks below. Safe because a
    # duplicate has already been recorded as an error above.
    wf_nums = {num: files[0] for num, files in wf_files.items()}

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

    # Excluded workflows must not sit behind an approval gate — a gated run needs
    # a safety-table row for the approver to judge it against.
    # Every file for the number, not just a representative: with a duplicate,
    # checking only one file can clear an excluded-but-gated workflow because
    # its twin happened to sort first.
    for num in sorted(EXCLUDE & set(wf_files)):
        for f in wf_files[num]:
            gated = declared_environments(open(f, encoding="utf-8-sig").read()) & GATED_ENVS
            if gated:
                errors.append(
                    f"workflow {num} ({f}) is in EXCLUDE but declares gated "
                    f"environment(s) {sorted(gated)}; add a row to {DOC} instead"
                )

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
