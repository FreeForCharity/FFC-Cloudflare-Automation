#!/usr/bin/env python3
"""Workflow cross-reference integrity guard.

A workflow (or a helper script it calls) can dispatch or depend on a *sibling*
workflow by file name. When workflows get renumbered, those hard-coded names go
stale and the reference only breaks at runtime — silently, often on a schedule
no one watches. That is exactly how `703-sites-list-generate.yml` failed on every
run (it dispatched `7-`/`4-`/`13-` files that had been renumbered to
`201-`/`108-`/`601-`; see issue #630).

This guard scans the workflow files and the `scripts/` tree for references to
numbered workflow files and fails when:

  1. a referenced workflow file does not exist under `.github/workflows/`
     (a dangling reference — the #630 failure class); or
  2. a file referenced via an explicit dispatch (`gh workflow run <file>` or
     `--workflow <file>`) exists but does NOT declare `on: workflow_dispatch`
     (it cannot actually be dispatched — the `AADSTS`-style 422 at runtime).

It also emits non-fatal warnings for artifacts that are consumed
(`gh run download --name X` / `download-artifact name: X`) but produced by no
workflow in the repo.

Run from the repo root: `python3 scripts/check-workflow-references.py`.
"""
import glob
import os
import re
import sys

WF_DIR = ".github/workflows"
# Numbered workflow file names, e.g. 703-sites-list-generate.yml, 12-foo.yml.
WF_FILE_RE = re.compile(r"\b(\d{1,4}-[a-z0-9][a-z0-9-]*\.ya?ml)\b", re.I)
# Explicit dispatch invocations — the referenced file must be dispatchable.
DISPATCH_RE = re.compile(
    r"""(?:gh\s+workflow\s+run|--workflow=?)\s+["']?(\d{1,4}-[a-z0-9][a-z0-9-]*\.ya?ml)""",
    re.I,
)
# Files we scan for references (workflow YAML + helper scripts).
SCAN_GLOBS = [
    f"{WF_DIR}/*.yml",
    f"{WF_DIR}/*.yaml",
    "scripts/*.ps1",
    "scripts/*.py",
    "scripts/*.sh",
    "scripts/*.mjs",
]
SKIP_SELF = "check-workflow-references.py"


def workflow_files():
    """Map of existing workflow file basenames -> path."""
    out = {}
    for f in glob.glob(f"{WF_DIR}/*.yml") + glob.glob(f"{WF_DIR}/*.yaml"):
        out[os.path.basename(f)] = f
    return out


def declares_dispatch(path):
    txt = open(path, encoding="utf-8-sig").read()
    # Match `workflow_dispatch:` (or `workflow_dispatch` under an `on:` list).
    return re.search(r"^\s*workflow_dispatch\s*:?\s*$", txt, re.M) is not None


def scan_files():
    seen = []
    for pattern in SCAN_GLOBS:
        for f in glob.glob(pattern):
            if os.path.basename(f) == SKIP_SELF:
                continue
            seen.append(f)
    return sorted(set(seen))


def main():
    existing = workflow_files()
    errors = []

    for f in scan_files():
        self_base = os.path.basename(f)
        for i, line in enumerate(open(f, encoding="utf-8-sig"), 1):
            # Skip pure comment lines to avoid flagging changelog/notes.
            stripped = line.lstrip()
            if stripped.startswith("#") or stripped.startswith("//"):
                continue

            # (1) Any numbered-workflow-filename reference must resolve.
            for m in WF_FILE_RE.finditer(line):
                ref = m.group(1)
                if ref == self_base:
                    continue
                if ref not in existing:
                    errors.append(
                        f"{f}:{i}: references workflow '{ref}' which does not "
                        f"exist under {WF_DIR}/ (stale/renumbered reference)."
                    )

            # (2) Explicit dispatch targets must be dispatchable.
            for m in DISPATCH_RE.finditer(line):
                ref = m.group(1)
                if ref in existing and not declares_dispatch(existing[ref]):
                    errors.append(
                        f"{f}:{i}: dispatches '{ref}' but that workflow does not "
                        f"declare `on: workflow_dispatch`."
                    )

    # Non-fatal: artifacts consumed but produced by nothing in the repo.
    produced, consumed = set(), {}
    for f in glob.glob(f"{WF_DIR}/*.yml") + glob.glob(f"{WF_DIR}/*.yaml"):
        txt = open(f, encoding="utf-8-sig").read()
        for m in re.finditer(r"upload-artifact@[^\n]*\n(?:[^\n]*\n){0,6}?\s*name:\s*([A-Za-z0-9_.\-]+)", txt):
            produced.add(m.group(1))
        for m in re.finditer(r"(?:download-artifact@[^\n]*\n(?:[^\n]*\n){0,6}?\s*name:\s*|gh\s+run\s+download[^\n]*--name\s+[\"']?)([A-Za-z0-9_.\-]+)", txt):
            consumed.setdefault(m.group(1), f)
    warnings = [
        f"{src}: consumes artifact '{name}' that no workflow produces (informational)."
        for name, src in sorted(consumed.items())
        if name not in produced
    ]

    if warnings:
        print("Workflow reference guard — warnings:")
        for w in warnings:
            print("  ~", w)

    if errors:
        print("Workflow reference guard FAILED:")
        for e in errors:
            print("  -", e)
        return 1

    print(
        f"Workflow reference guard OK: {len(existing)} workflows, "
        f"all cross-references resolve and dispatch targets are dispatchable."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
