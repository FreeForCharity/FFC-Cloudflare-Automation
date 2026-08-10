"""Guard: the hooks suite must be evaluatable by the merge queue (#1084).

`.claude/hooks/test_hooks.py` is run by exactly one workflow,
`728-ai-agent-hooks-validate.yml`. For four days that workflow had
`pull_request` + `push: branches: [main]` and **no `merge_group:`**, so the
merge queue never ran the hooks suite at all. The first run to see a merged
result was the post-merge `push` one -- after the damage.

That is not hypothetical. #1084 measured it on two open, green, cleanly
mergeable PRs:

    main                93 passed, 0 failed
    main + #1018       115 passed, 0 failed
    main + #1039        99 passed, 0 failed
    main + #1018 + #1039   120 passed, 1 FAILED   <- clean merge, red union

Every input green, the merge clean, the result red. That is precisely the class
of defect a merge queue exists to catch, on the one check that was not
participating in it.

TWO PROPERTIES, AND WHY BOTH ARE NEEDED
    1. `merge_group:` -- without it the queue never evaluates the suite.
    2. no `paths:` filter -- a path-filtered workflow produces NO CHECK CONTEXT
       on a pull request that does not match. A context that is sometimes
       absent cannot be made a required status check: every non-hooks PR would
       block forever waiting on a check GitHub never creates. #1084 measured
       this too -- `Validate AI agent hooks` was present on #1018/#1039/#1062
       and simply absent on #1064/#825.

    Property 1 alone is visible-but-not-gating. Property 2 alone reports on
    every PR but still skips the merged result. Neither is sufficient, so both
    are asserted, and the class test below asserts them of *whichever* workflow
    runs the suite rather than of this filename -- moving the suite to a new
    workflow must not silently reopen the hole.

WHAT THIS MODULE DOES NOT DO
    Adding `Validate AI agent hooks` to ruleset 16768928 is an admin-only
    change (@clarkemoyer) and is what actually makes the check *block* a merge.
    Order matters and getting it wrong is a repo-wide outage: requiring the
    context BEFORE property 2 lands stops every non-hooks PR in the repo. This
    module is the machine-checked record that the two prerequisites are in
    place, so that step can be taken safely.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS, load_workflow  # noqa: E402

HOOKS_SUITE = ".claude/hooks/test_hooks.py"
WORKFLOW_728 = "728-ai-agent-hooks-validate.yml"

# `on:` in YAML 1.1 is the boolean true, which is why every reader of a workflow
# file has to check both keys. Getting this wrong reads as "the workflow has no
# triggers" -- a silent pass for every assertion below.
ON_KEYS = ("on", True)

# The two triggers a pull request and its merge group actually arrive on. A
# `paths:` filter on either one is what suppresses the check context; a filter
# on an unrelated trigger (`schedule`, `workflow_dispatch`) is not this defect.
GATING_TRIGGERS = ("pull_request", "pull_request_target", "merge_group")

PATH_FILTER_KEYS = ("paths", "paths-ignore")


def _on_block(doc) -> dict:
    """The workflow's trigger mapping, normalised.

    Fails closed on the shapes that are not a mapping: `on: push` (a bare
    string) and `on: [push, pull_request]` (a list) are legal YAML and carry no
    filters, so they normalise to a mapping with empty configs rather than
    being skipped.
    """
    for key in ON_KEYS:
        if isinstance(doc, dict) and key in doc:
            block = doc[key]
            break
    else:
        return {}
    if isinstance(block, str):
        return {block: {}}
    if isinstance(block, list):
        return {name: {} for name in block}
    if isinstance(block, dict):
        return block
    return {}


def path_filtered_triggers(doc) -> list[str]:
    """Gating triggers on this workflow that carry a `paths:`/`paths-ignore:`."""
    found = []
    for name, cfg in _on_block(doc).items():
        if name not in GATING_TRIGGERS or not isinstance(cfg, dict):
            continue
        if any(k in cfg for k in PATH_FILTER_KEYS):
            found.append(name)
    return sorted(found)


def has_merge_group(doc) -> bool:
    return "merge_group" in _on_block(doc)


def _runs_hooks_suite(path: pathlib.Path) -> bool:
    """True if this workflow's text invokes the hooks suite.

    Read as raw text, not through the parsed step list: the suite could be
    invoked from a composite `run:`, a `make` target, or a shell loop, and a
    structural walk that only understands one of those shapes would answer
    "nothing runs it" -- the reassuring direction.
    """
    return HOOKS_SUITE in path.read_text(encoding="utf-8")


def hooks_suite_workflows() -> list[pathlib.Path]:
    return sorted(p for p in WORKFLOWS.glob("*.yml") if _runs_hooks_suite(p))


def test_728_runs_in_the_merge_group():
    doc = load_workflow(WORKFLOW_728)
    assert has_merge_group(doc), (
        f"{WORKFLOW_728} has no `merge_group:` trigger, so the merge queue never runs "
        f"{HOOKS_SUITE} against the merged result. #1084 measured two individually-green "
        f"PRs whose union failed the suite 120/1 -- exactly what a merge queue exists to "
        f"catch, missed on the one check not participating in it."
    )


def test_728_reports_a_context_on_every_pull_request():
    doc = load_workflow(WORKFLOW_728)
    filtered = path_filtered_triggers(doc)
    assert not filtered, (
        f"{WORKFLOW_728} carries a path filter on {filtered}. A path-filtered workflow "
        f"produces NO check context on a PR that does not match, so `Validate AI agent "
        f"hooks` can never be added to the ruleset: every non-hooks PR would block on a "
        f"check GitHub never creates (#1084). The job runs in ~10s -- run it always."
    )


def test_whichever_workflow_runs_the_hooks_suite_is_queue_evaluated():
    """The class, not the instance: moving the suite must not reopen the hole."""
    runners = hooks_suite_workflows()
    assert runners, (
        f"no workflow invokes {HOOKS_SUITE}. The hooks suite is the only thing that "
        f"exercises .claude/hooks/ and .githooks/; if it moved, this guard must move "
        f"with it rather than passing vacuously on an empty set."
    )
    bad = []
    for path in runners:
        doc = load_workflow(path.name)
        if not has_merge_group(doc):
            bad.append(f"{path.name}: no merge_group trigger")
        filtered = path_filtered_triggers(doc)
        if filtered:
            bad.append(f"{path.name}: path-filtered on {filtered}")
    assert not bad, (
        "a workflow running the hooks suite is not queue-evaluatable: " + "; ".join(bad)
    )


def test_the_detectors_discriminate():
    """Positive controls: an assertion over a predicate that never fires is not a test.

    Both helpers answer "is this OK?" in the affirmative by default, so a bug in
    either one makes every test above pass on a broken tree (L47 one layer down,
    in the harness rather than the subject). Feed each the defect it exists to
    catch and require it to say so.
    """
    before_1084 = yaml.safe_load(
        """
on:
  pull_request:
    branches: [main]
    paths:
      - '.claude/**'
  push:
    branches: [main]
    paths:
      - '.claude/**'
"""
    )
    assert not has_merge_group(before_1084), "must detect a missing merge_group trigger"
    assert path_filtered_triggers(before_1084) == ["pull_request"], (
        "must detect a paths: filter on pull_request -- and must NOT report `push`, "
        "which produces no PR check context either way"
    )

    after_1084 = yaml.safe_load(
        """
on:
  pull_request:
    branches: [main]
  merge_group:
  push:
    branches: [main]
"""
    )
    assert has_merge_group(after_1084), "the shipped fix must read as queue-evaluated"
    assert path_filtered_triggers(after_1084) == [], "the shipped fix must read as unfiltered"

    # `on:` parses as the boolean True in YAML 1.1. A reader that checks only the
    # string key sees no triggers at all and passes everything.
    assert True in before_1084 or "on" in before_1084, "fixture must exercise the on/True key"

    # A path filter on a non-gating trigger is not this defect.
    scheduled = yaml.safe_load(
        """
on:
  merge_group:
  pull_request:
    branches: [main]
  push:
    paths: ['docs/**']
"""
    )
    assert path_filtered_triggers(scheduled) == [], (
        "a paths: filter on `push` must not be reported -- `push` creates no pull-request "
        "check context, so filtering it cannot suppress a required one"
    )


def test_an_unreadable_trigger_block_is_not_silently_ok():
    """Fail closed: the shapes that are legal YAML but not a mapping.

    `on: push` and `on: [push, pull_request]` are both valid and both would make
    a naive `.get("paths")` walk raise or skip. They carry no filters and no
    merge_group, and must read that way rather than as "nothing to check".
    """
    assert not has_merge_group(yaml.safe_load("on: push")), (
        "a bare string trigger has no merge_group and must not read as if it did"
    )
    assert not has_merge_group(yaml.safe_load("on: [push, pull_request]")), (
        "a list trigger has no merge_group and must not read as if it did"
    )
    assert path_filtered_triggers(yaml.safe_load("on: [pull_request]")) == [], (
        "a list trigger carries no filters"
    )
    assert not has_merge_group({}), "an empty document must not read as queue-evaluated"
    assert not has_merge_group(None), "an unparsed document must not read as queue-evaluated"


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
