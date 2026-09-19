"""Guard: a `pull_request:` trigger must not be filtered on the base ref (#1325).

GitHub matches a `pull_request:` `branches:` filter against the **base** ref of
the pull request, not the head. So a workflow declaring

    on:
      pull_request:
        branches: [main]

does not run at all on a PR opened against anything other than `main` -- a
stacked PR on another agent's feature branch, which this repo's agents open
routinely.

WHY THIS IS THE FALSE-CLEAN SHAPE AGAIN
    The checks that would have run are ABSENT, not failing. GitHub renders a
    PR's check list from the contexts that exist, so the page shows an all-green
    list and nothing on it distinguishes "validated" from "never examined".
    Same shape as L240: a check that never read the file, reporting success.

    Measured 2026-09-16 on two open PRs stacked on `claude/sweet-hawking-vi51y5`:

        #1312 (head 930c7ff)   Phantom Revert Guard, label-sync, sweep
        #1323 (head 533b1ea)   Phantom Revert Guard, label-sync, sweep

    -- no `Validate Repository`, no `Validate AI agent hooks`, no CodeQL, while
    the three sibling PRs based on `main` carried all three, green. The only
    variable was the base ref.

    Both affected PRs modify `.claude/hooks/guard_bash.py`, the command guard
    itself, and `728-ai-agent-hooks-validate.yml` -- the workflow whose entire
    job is to test that file -- was one of the three that did not run. A change
    to the security guard was reviewed with its dedicated suite silently absent.

SECONDARY EFFECT, AND WHY THE FILTER CANNOT JUST BE LEFT ALONE
    `Validate Repository` is a REQUIRED check. A PR that can never acquire it
    can never enter the merge queue, and therefore can never leave the open set
    -- which holds the cloud worker's PR cap open indefinitely.

    That is the argument against the other permitted remedy (a loud unfiltered
    check that merely NAMES the missing set): naming it does not let the PR
    merge. This repo took remedy (a): run the same validation set regardless of
    base ref. The cost is that a stacked PR now runs the ~5m validate job and
    CodeQL too. Stacked PRs are rare here; a sometimes-absent required check is
    the defect.

WHAT IS AND IS NOT THIS DEFECT
    A `branches:` filter on `push:` is NOT this defect and must not be reported.
    `push` creates no pull-request check context either way, and
    `push: branches: [main]` is exactly right: post-merge runs belong on `main`.
    A detector that cannot tell those apart would demand a change that is
    actively wrong, which is its own way of not being trusted.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS, load_workflow  # noqa: E402

# `on:` in YAML 1.1 is the boolean true. Reading only the string key makes every
# workflow look triggerless, which is a silent pass for every sweep below.
ON_KEYS = ("on", True)

# The triggers that create a check context on a pull request. `push` is
# deliberately absent -- see the module docstring.
PR_TRIGGERS = ("pull_request", "pull_request_target")

# Both spellings of a base-ref filter. `branches-ignore` suppresses the context
# on exactly the same axis and is the obvious way to reintroduce this defect
# while passing a guard that only knows the positive form (L17: match the thing,
# not one spelling of it).
BASE_FILTER_KEYS = ("branches", "branches-ignore")

# The workflows whose absence on a stacked PR is what #1325 measured. Asserted
# to still be in the scanned set, so the sweep cannot pass by scanning nothing.
VALIDATION_WORKFLOWS = (
    "722-ci.yml",
    "723-codeql-analysis.yml",
    "728-ai-agent-hooks-validate.yml",
)

HOOKS_DIR = ".claude/hooks/"
WORKFLOW_728 = "728-ai-agent-hooks-validate.yml"


def _on_block(doc) -> dict:
    """The workflow's trigger mapping, normalised, failing closed.

    `on: push` (a bare string) and `on: [push, pull_request]` (a list) are legal
    YAML carrying no filters, so they normalise to a mapping with empty configs
    rather than being skipped.
    """
    block = None
    for key in ON_KEYS:
        if isinstance(doc, dict) and key in doc:
            block = doc[key]
            break
    else:
        return {}
    if isinstance(block, str):
        return {block: {}}
    if isinstance(block, list):
        return {name: {} for name in block if isinstance(name, str)}
    if isinstance(block, dict):
        return block
    return {}


def base_filtered_pr_triggers(doc) -> dict[str, dict]:
    """Map trigger name -> the base-ref filter(s) it carries.

    Returns the filter VALUES, not a boolean, so a caller can report what it
    measured rather than only that something was wrong.
    """
    found: dict[str, dict] = {}
    for name, cfg in _on_block(doc).items():
        if name not in PR_TRIGGERS or not isinstance(cfg, dict):
            continue
        filters = {k: cfg[k] for k in BASE_FILTER_KEYS if k in cfg}
        if filters:
            found[name] = filters
    return found


def has_pr_trigger(doc) -> bool:
    return any(name in _on_block(doc) for name in PR_TRIGGERS)


def _workflow_paths() -> list[pathlib.Path]:
    return sorted(WORKFLOWS.glob("*.yml"))


def test_no_workflow_filters_a_pull_request_trigger_on_the_base_ref():
    """The sweep, over every workflow rather than the three #1325 named."""
    offenders = []
    for path in _workflow_paths():
        doc = load_workflow(path.name)
        for trigger, filters in sorted(base_filtered_pr_triggers(doc).items()):
            rendered = ", ".join(f"{k}: {v!r}" for k, v in sorted(filters.items()))
            offenders.append(f"{path.name}: `{trigger}:` carries {rendered}")
    assert not offenders, (
        "a `pull_request:` trigger is filtered on the BASE ref, so these workflows "
        "produce NO check context on a PR stacked on a feature branch -- absent, not "
        "failing, so the PR reads all-green with the check missing (#1325):\n  "
        + "\n  ".join(offenders)
        + "\nRemove the `branches:` filter from the `pull_request:` trigger. A filter on "
        "`push:` is correct and is not reported here."
    )


def test_the_validation_workflows_are_actually_in_the_scanned_set():
    """Non-vacuity: the sweep must not pass because it scanned nothing.

    A glob that matches no file, or a validation workflow that quietly lost its
    `pull_request:` trigger entirely, would satisfy the sweep above while
    reproducing the exact outcome it exists to prevent -- no check on the PR.
    """
    scanned = {p.name for p in _workflow_paths()}
    assert len(scanned) > 50, (
        f"the workflow glob matched only {len(scanned)} files; the sweep above would "
        f"pass vacuously. Expected this repo's full workflow set."
    )
    missing = [name for name in VALIDATION_WORKFLOWS if name not in scanned]
    assert not missing, f"validation workflows not found under {WORKFLOWS}: {missing}"

    triggerless = [
        name for name in VALIDATION_WORKFLOWS if not has_pr_trigger(load_workflow(name))
    ]
    assert not triggerless, (
        f"these workflows have no `pull_request:` trigger at all: {triggerless}. That is "
        f"not 'no base filter' -- it is the same end state #1325 measured (no check on "
        f"the PR), reached a different way."
    )


def test_728_validates_hooks_on_every_pull_request_whatever_the_base():
    """AC: hooks validation follows the FILE, not the base branch.

    `.claude/hooks/**` is guarded by exactly one workflow. It must run on a PR
    that changes those files no matter what that PR is based on -- which, since
    a `paths:` filter would suppress the context entirely (#1084), means running
    unfiltered on both axes.
    """
    doc = load_workflow(WORKFLOW_728)
    on = _on_block(doc)
    assert "pull_request" in on, f"{WORKFLOW_728} has no `pull_request:` trigger"

    filters = base_filtered_pr_triggers(doc)
    assert not filters, (
        f"{WORKFLOW_728} filters its pull-request trigger on the base ref ({filters}), so "
        f"a PR based on a feature branch changing {HOOKS_DIR} is reviewed with the hooks "
        f"suite never run. #1312 and #1323 were both such PRs, and both changed "
        f"{HOOKS_DIR}guard_bash.py itself."
    )

    cfg = on.get("pull_request") or {}
    path_filters = (
        {k: cfg[k] for k in ("paths", "paths-ignore") if k in cfg} if isinstance(cfg, dict) else {}
    )
    assert not path_filters, (
        f"{WORKFLOW_728} carries a path filter ({path_filters}). A path-filtered workflow "
        f"produces no check context on a non-matching PR, so the context can never be "
        f"required -- every non-hooks PR would block on a check GitHub never creates "
        f"(#1084). The job runs in ~10s; run it always."
    )


def test_the_detector_discriminates():
    """Positive controls, asserting the VALUES returned, not just truthiness.

    Every test above answers "is this OK?" in the affirmative by default, so a
    detector that never fires makes all of them pass on a broken tree. Feed it
    the defect and require it to name what it found.
    """
    before_1325 = yaml.safe_load(
        """
on:
  pull_request:
    branches: [main]
  merge_group:
  push:
    branches: [main]
"""
    )
    assert base_filtered_pr_triggers(before_1325) == {"pull_request": {"branches": ["main"]}}, (
        "must report the pre-#1325 form AND the filter it measured -- and must NOT report "
        "`push`, whose `branches: [main]` is correct"
    )

    after_1325 = yaml.safe_load(
        """
on:
  pull_request:
  merge_group:
  push:
    branches: [main]
"""
    )
    assert base_filtered_pr_triggers(after_1325) == {}, "the shipped fix must read as unfiltered"

    # The other spelling. A guard that knows only `branches:` is reopened by one
    # word (L17).
    ignore_form = yaml.safe_load("on:\n  pull_request:\n    branches-ignore: ['gh-pages']\n")
    assert base_filtered_pr_triggers(ignore_form) == {
        "pull_request": {"branches-ignore": ["gh-pages"]}
    }, "`branches-ignore:` suppresses the context on the same axis and must be reported"

    # `pull_request_target` is the same defect on a different trigger.
    target_form = yaml.safe_load("on:\n  pull_request_target:\n    branches: [main]\n")
    assert base_filtered_pr_triggers(target_form) == {
        "pull_request_target": {"branches": ["main"]}
    }, "`pull_request_target` creates a PR check context too"

    # A `paths:` filter is #1084's defect, not this one; reporting it here would
    # make the two indistinguishable in the failure message.
    paths_only = yaml.safe_load("on:\n  pull_request:\n    paths: ['docs/**']\n")
    assert base_filtered_pr_triggers(paths_only) == {}, (
        "a `paths:` filter is #1084, not #1325 -- test_728_hooks_validate_gating.py owns it"
    )

    # The fixture must actually exercise the on/True key question.
    assert True in before_1325 or "on" in before_1325, "fixture must exercise the on/True key"


def test_an_unreadable_trigger_block_is_not_silently_ok():
    """Fail closed on the shapes that are legal YAML but not a mapping."""
    assert base_filtered_pr_triggers(yaml.safe_load("on: pull_request")) == {}, (
        "a bare string trigger carries no filters"
    )
    assert has_pr_trigger(yaml.safe_load("on: pull_request")), (
        "a bare string trigger must still read as HAVING the trigger, or the non-vacuity "
        "check above could be satisfied by a shape it simply cannot read"
    )
    assert has_pr_trigger(yaml.safe_load("on: [push, pull_request]")), (
        "a list trigger must read as having the trigger"
    )
    assert base_filtered_pr_triggers(yaml.safe_load("on: [push, pull_request]")) == {}, (
        "a list trigger carries no filters"
    )
    assert not has_pr_trigger(yaml.safe_load("on: push")), (
        "`push` alone must not read as a pull-request trigger"
    )
    assert base_filtered_pr_triggers({}) == {}, "an empty document must not crash the scan"
    assert base_filtered_pr_triggers(None) == {}, "an unparsed document must not crash the scan"
    assert not has_pr_trigger(None), "an unparsed document must not read as triggered"


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
