"""Unit tests for the merge-queue job-timeout guard (#1565).

The tree is clean by construction after the fix, so a guard that returned `[]`
unconditionally would pass every "the tree is fine" assertion in this file. The
load-bearing tests are therefore the ones that feed the guard the shapes it is
supposed to reject:

* `test_the_pre_fix_722_job_is_a_finding` -- the exact `validate` job 722
  shipped before this change (no `timeout-minutes` at all). If this ever passes
  as clean, the guard has stopped working and nothing else here would say so.
* `test_the_default_360_is_a_finding_not_a_pass` -- the one that matters most.
  `timeout-minutes: 360` IS GitHub's default, so a presence-only guard accepts
  it and reports a bounded tree for the unbounded case it was written for. This
  is the repo's recurring "presence mistaken for validity" shape and the reason
  the guard checks the number.
* `test_a_bounded_job_is_not_a_finding` -- the negative control. Without it,
  "every mutation is reported" is satisfied by a guard that reports
  unconditionally, which discriminates nothing (#1565 AC3 asks for both
  directions explicitly).

`test_the_guard_discriminates_at_the_ceiling` pins the boundary from both sides
with values one apart, because a ceiling asserted only from the failing side is
compatible with a guard that rejects everything.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "check-merge-queue-job-timeouts.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

_spec = importlib.util.spec_from_file_location("check_merge_queue_job_timeouts", GUARD)
guard = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(guard)


def _job(**fields) -> dict:
    """A minimal job mapping; `fields` carries whatever the case is about."""
    base = {"name": "A job", "runs-on": "ubuntu-latest", "steps": [{"run": "true"}]}
    base.update(fields)
    return base


# --- the pre-fix shapes ----------------------------------------------------


def test_the_pre_fix_722_job_is_a_finding():
    """722's `validate` as it shipped before #1565: no `timeout-minutes`.

    Without this the module asserts only that a clean tree is clean.
    """
    problem = guard.timeout_problem(
        _job(permissions={"contents": "read", "pull-requests": "read"})
    )
    assert problem is not None, "a job with no timeout-minutes must be a finding"
    assert "360" in problem, (
        f"the message must name the default it inherits, so the reader knows "
        f"what the cost is; got: {problem}"
    )


def test_the_default_360_is_a_finding_not_a_pass():
    """`timeout-minutes: 360` is the default wearing a declaration.

    This is the case a presence-only guard waves through, and it is the whole
    argument for checking the value. If this test fails, the guard reports a
    bounded tree for exactly the defect #1565 describes.
    """
    problem = guard.timeout_problem(_job(**{"timeout-minutes": 360}))
    assert problem is not None, (
        "timeout-minutes: 360 is GitHub's own default and bounds nothing — a "
        "guard that accepts it has been satisfied by the defect"
    )
    assert str(guard.MAX_TIMEOUT_MINUTES) in problem, (
        f"the message must name the ceiling so the fix is obvious; got: {problem}"
    )


def test_a_zero_or_negative_timeout_is_a_finding():
    """`0` is falsy and `-1` is nonsense; neither bounds a job."""
    for value in (0, -1):
        problem = guard.timeout_problem(_job(**{"timeout-minutes": value}))
        assert problem is not None, f"timeout-minutes: {value} must be a finding"
        assert "positive" in problem, problem


def test_a_boolean_timeout_is_not_read_as_one_minute():
    """`bool` is an `int` subclass in Python, so `true` scores as 1 unchecked.

    A guard that accepts `timeout-minutes: true` has accepted a typo as a
    one-minute bound, which would then fail every run for an unrelated reason.
    """
    problem = guard.timeout_problem(_job(**{"timeout-minutes": True}))
    assert problem is not None, "timeout-minutes: true must be a finding"
    assert "not an integer" in problem, problem


def test_an_expression_timeout_is_reported_rather_than_assumed_sound():
    """`${{ }}` resolves on the runner, so this file cannot evaluate it."""
    problem = guard.timeout_problem(_job(**{"timeout-minutes": "${{ inputs.t }}"}))
    assert problem is not None, "an expression timeout must be a finding"
    assert "literal" in problem, problem


def test_a_reusable_workflow_call_is_reported_as_unverifiable():
    """GitHub rejects `timeout-minutes` on a `uses:` job, so the bound lives in
    the callee and this guard does not follow the reference.

    Reported on purpose: an over-report is a red run someone reads, an
    under-report is a green one nobody does. Population is 0 today.
    """
    problem = guard.timeout_problem({"uses": "./.github/workflows/999-other.yml"})
    assert problem is not None, "a `uses:` job must be reported as unverifiable"
    assert "uses" in problem, problem


# --- the negative controls --------------------------------------------------


def test_a_bounded_job_is_not_a_finding():
    """The control. Without it, every assertion above is satisfied by a guard
    that reports unconditionally."""
    assert guard.timeout_problem(_job(**{"timeout-minutes": 30})) is None


def test_the_guard_discriminates_at_the_ceiling():
    """One apart, and they must disagree — a ceiling asserted only from the
    failing side is compatible with a guard that rejects everything."""
    at = guard.MAX_TIMEOUT_MINUTES
    assert guard.timeout_problem(_job(**{"timeout-minutes": at})) is None, (
        f"{at} is the ceiling and must be accepted"
    )
    assert guard.timeout_problem(_job(**{"timeout-minutes": at + 1})) is not None, (
        f"{at + 1} is above the ceiling and must be rejected"
    )


def test_a_step_level_timeout_is_not_counted_as_a_job_bound():
    """A step timeout bounds one step; the job can hang in any other, in runner
    setup, or in a `post` action. Conflating them is a guard that passes on a
    job that is still unbounded."""
    problem = guard.timeout_problem(
        _job(steps=[{"run": "true", "timeout-minutes": 5}])
    )
    assert problem is not None, (
        "a step-level timeout must not satisfy the job-level requirement"
    )


# --- `on:` parsing, which decides whether a workflow is examined at all -----


def test_the_on_block_is_read_through_the_yaml_boolean_key():
    """`on:` is the boolean True in YAML 1.1.

    A reader that checks only the string key sees no triggers on any workflow in
    the tree and would skip all four merge-queue workflows while printing a
    clean result — a total fail-open with no visible symptom.
    """
    doc = yaml.safe_load("on:\n  merge_group:\n  pull_request:\njobs: {}\n")
    assert True in doc, "precondition: PyYAML still parses `on:` as the bool True"
    names, error = guard.trigger_names(doc)
    assert error is None, error
    assert guard.MERGE_QUEUE_TRIGGER in names, names


def test_all_three_legal_on_shapes_are_recognised():
    for text in (
        "on: merge_group\n",
        "on: [pull_request, merge_group]\n",
        "on:\n  merge_group:\n",
    ):
        names, error = guard.trigger_names(yaml.safe_load(text))
        assert error is None, f"{text!r}: {error}"
        assert guard.MERGE_QUEUE_TRIGGER in names, f"{text!r} -> {names}"


def test_an_unreadable_on_block_is_an_error_not_an_empty_trigger_list():
    """Fail closed. 'No triggers, nothing to check' is the fail-open answer: a
    guard that cannot read `on:` cannot know whether the workflow gates the
    queue (ledger L02)."""
    names, error = guard.trigger_names({"on": 42})
    assert error is not None and names == [], (names, error)
    names, error = guard.trigger_names({"jobs": {}})
    assert error is not None, "a workflow with no `on:` block must be reported"


# --- the tree, and the freeze ----------------------------------------------


def test_the_tree_is_clean_and_four_workflows_gate_the_queue():
    """Cardinality stated, not just the verdict: a count of 0 gating workflows
    would also produce 0 findings, and a guard examining nothing reads as a
    pass. #1605 is the open issue about exactly this."""
    findings, hard_errors, gating = guard.scan_all()
    assert hard_errors == [], hard_errors
    assert findings == [], findings
    assert gating == 4, (
        f"expected 4 merge-queue-gating workflows (722, 723, 727, 728); "
        f"found {gating}. If a workflow gained or lost `merge_group:`, "
        f"re-measure its runtime and give it a bound before changing this."
    )


def test_each_merge_queue_workflow_is_bounded_in_the_tree_itself():
    """Read from the files rather than from `scan_all`'s verdict, so a guard
    that silently stopped scanning cannot satisfy this."""
    expected = {
        "722-ci.yml": "validate",
        "723-codeql-analysis.yml": "analyze",
        "727-phantom-revert-guard.yml": "phantom-revert-guard",
        "728-ai-agent-hooks-validate.yml": "validate-hooks",
    }
    for name, job_id in expected.items():
        doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
        names, error = guard.trigger_names(doc)
        assert error is None, f"{name}: {error}"
        assert guard.MERGE_QUEUE_TRIGGER in names, f"{name} no longer gates the queue"
        value = doc["jobs"][job_id].get("timeout-minutes")
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"{name}:{job_id} timeout-minutes is {value!r}"
        )
        assert 0 < value <= guard.MAX_TIMEOUT_MINUTES, f"{name}:{job_id} -> {value}"


def test_the_freeze_is_empty_so_the_guard_fails_on_any_new_instance():
    assert guard.KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS == {}, (
        "the freeze is empty by measurement — every merge-queue job in the tree "
        "is bounded. An entry added here needs a stated reason in the diff."
    )


def test_a_stale_freeze_entry_is_reported():
    """`compare()` is otherwise unexercised while the freeze is empty, which
    would make it code nobody has run."""
    original = guard.KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS
    try:
        guard.KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS = {"no-such-workflow.yml": ("x",)}
        errors = guard.compare({})
        assert errors and "no such" in errors[0], errors

        # An entry naming a real workflow whose job is now bounded is also stale.
        guard.KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS = {"722-ci.yml": ("validate",)}
        errors = guard.compare({})
        assert errors and "stale" in errors[0], errors

        # ...but an entry that still describes a finding is NOT reported.
        assert guard.compare({"722-ci.yml": ["validate"]}) == []
    finally:
        guard.KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS = original


# --- wiring -----------------------------------------------------------------


def test_the_guard_is_wired_into_ci():
    """A checker nobody runs is the failure mode this whole class is about."""
    ci = (WORKFLOWS / "722-ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-merge-queue-job-timeouts.py" in ci, (
        "check-merge-queue-job-timeouts.py must run in 722-ci.yml, or the rule "
        "is documentation"
    )


def test_the_guard_installs_its_own_yaml_dependency_in_ci():
    """The runner image only happens to preinstall PyYAML; without the install
    an image that drops it turns this guard into an ImportError, which is a red
    build that says nothing about timeouts."""
    ci = (WORKFLOWS / "722-ci.yml").read_text(encoding="utf-8")
    block = ci.split("Validate every merge-queue job declares a bounded timeout", 1)[1]
    block = block.split("scripts/check-merge-queue-job-timeouts.py", 1)[0]
    assert "pip install --quiet pyyaml" in block, (
        "the merge-queue-timeout step must install PyYAML defensively, like its "
        "siblings"
    )


def test_the_guard_runs_inside_the_job_it_bounds():
    """Self-referential on purpose: the step lives in 722's `validate`, which is
    itself one of the jobs the guard requires a bound on. If the step were moved
    to a workflow with no `merge_group:` trigger, the rule would stop covering
    the gate it was written for."""
    doc = yaml.safe_load((WORKFLOWS / "722-ci.yml").read_text(encoding="utf-8"))
    names, error = guard.trigger_names(doc)
    assert error is None, error
    assert guard.MERGE_QUEUE_TRIGGER in names
    bodies = [
        s.get("run", "") for s in doc["jobs"]["validate"]["steps"] if isinstance(s, dict)
    ]
    assert any("check-merge-queue-job-timeouts.py" in b for b in bodies), (
        "the guard must run in 722's `validate` job"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
