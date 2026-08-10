"""Guard: the published catalog's trigger list must match the workflows (#1084).

`docs/workflow-catalog.json` is served publicly at ffcadmin.org/automation/ and
is the file AGENTS.md tells every agent to read before picking a workflow. Its
whole job is to say what each workflow does and **how it is invoked**.

Its `triggers` field was derived by a regex over the raw YAML:

    ^on:\\s*\\n((?:[ \\t]+.*\\n?)+)

which stops at the first BLANK LINE. Any trigger below one was invisible, and
the row still rendered as a complete, confident answer. Measured on `main`
before the fix, three of 105 workflows disagreed with their own parsed `on:`
block, two of them already shipping:

    701-website-provision.yml    published `issues`
                                 actually  issues, repository_dispatch, workflow_dispatch
    729-repo-add-collaborator.yml published `workflow_dispatch`
                                 actually  workflow_call, workflow_dispatch

729 is the sharper one: `workflow_call` means another workflow can invoke it,
and it is a **`Writes (live default)`** workflow on `github-prod`. A reader
auditing what can reach that gate from the catalog alone would not have seen it.

WHY THIS MODULE AND NOT THE `--check` FLAG
    CI already runs `generate-workflow-catalog.py --check`, and it was green
    throughout. It compares the committed catalog against a fresh run of the
    same generator, so a generator bug is present on both sides of that
    comparison and cancels out exactly. This module compares the catalog to the
    workflows themselves -- a second, independent derivation -- which is the
    only way that class of defect shows up.
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT, WORKFLOWS  # noqa: E402

CATALOG = REPO_ROOT / "docs" / "workflow-catalog.json"


def _declared_triggers(doc) -> list[str]:
    """Trigger names from a parsed workflow, independent of the generator.

    Written out rather than imported from `scripts/generate-workflow-catalog.py`
    on purpose: a check that reuses the code under test agrees with it by
    construction, including when both are wrong. That is precisely how the
    `--check` flag stayed green over this defect.
    """
    for key in ("on", True):
        if isinstance(doc, dict) and key in doc:
            block = doc[key]
            break
    else:
        return []
    if isinstance(block, str):
        return [block]
    if isinstance(block, list):
        return sorted(str(x) for x in block)
    if isinstance(block, dict):
        return sorted(str(k) for k in block)
    return []


def _catalog_rows() -> dict[str, dict]:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    return {row["file"]: row for row in data["workflows"]}


def test_every_workflow_publishes_its_real_triggers():
    rows = _catalog_rows()
    mismatches = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        row = rows.get(path.name)
        if row is None:
            # A workflow with no `NNN.` name prefix is deliberately uncatalogued.
            continue
        actual = _declared_triggers(yaml.safe_load(path.read_text(encoding="utf-8")))
        published = sorted(row.get("triggers", []))
        if published != sorted(actual):
            mismatches.append(f"{path.name}: catalog={published} actual={sorted(actual)}")
    assert not mismatches, (
        "docs/workflow-catalog.json misreports how these workflows are invoked -- "
        "regenerate with `python3 scripts/generate-workflow-catalog.py`:\n  "
        + "\n  ".join(mismatches)
    )


def test_the_catalog_covers_every_numbered_workflow():
    """A missing row is a mismatch this file would otherwise skip past.

    The loop above `continue`s on an absent row, which is correct for an
    intentionally-uncatalogued workflow and would otherwise be a silent hole:
    delete a row and every remaining assertion still passes. Count instead.
    """
    rows = _catalog_rows()
    numbered = [
        p.name
        for p in sorted(WORKFLOWS.glob("*.yml"))
        if p.name[:3].isdigit() and p.name[3] == "-"
    ]
    missing = [n for n in numbered if n not in rows]
    assert not missing, f"numbered workflows with no catalog row: {missing}"
    assert len(numbered) >= 100, (
        f"only {len(numbered)} numbered workflows found -- the glob is not "
        f"reaching the workflow directory, so this module proves nothing"
    )


def test_the_comparison_discriminates():
    """Positive control: the trigger reader must not answer [] for everything.

    Every assertion above is of the form "these two agree". Two empty lists
    agree. Feed the reader each legal `on:` shape and require the right answer,
    including the YAML-1.1 `on:`-is-True case that silently empties it.
    """
    assert _declared_triggers(yaml.safe_load("on:\n  push:\n    branches: [main]\n")) == ["push"]
    assert _declared_triggers(yaml.safe_load("on: [push, pull_request]")) == [
        "pull_request",
        "push",
    ]
    assert _declared_triggers(yaml.safe_load("on: workflow_dispatch")) == ["workflow_dispatch"]

    # The defect itself: a blank line inside the `on:` block. The old regex
    # returned ['pull_request'] here; a parsed read must see all three.
    with_blank_lines = yaml.safe_load(
        """
on:
  pull_request:
    branches: [main]

  merge_group:

  push:
    branches: [main]
"""
    )
    assert _declared_triggers(with_blank_lines) == ["merge_group", "pull_request", "push"], (
        "a blank line inside the on: block must not hide the triggers below it -- "
        "that is the #1084 defect this module exists for"
    )

    assert _declared_triggers({}) == [], "a document with no on: block has no triggers"
    assert _declared_triggers(None) == [], "an unparsed document must not invent triggers"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:500]}")
    sys.exit(1 if failures else 0)
