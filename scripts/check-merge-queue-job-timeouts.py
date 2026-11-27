#!/usr/bin/env python3
"""Guard: every job in a `merge_group:` workflow declares a BOUNDED
`timeout-minutes` (#1565).

A workflow that declares `merge_group:` is evaluated by the merge queue, so its
jobs pace every merge in the repository. GitHub's default job timeout is
**360 minutes**. A job with no `timeout-minutes` therefore holds the queue for
up to six hours on a hang, nothing lands behind it, and no alert fires.

WHY IT IS SILENT, WHICH IS THE WHOLE REASON THIS IS A GUARD
    A wedged queue and a healthy one are the same picture. `mergeQueueEntry`
    reads `AWAITING_CHECKS` either way, and CLAUDE.md's own merge-queue section
    tells the reader -- correctly, for the healthy case -- that
    `AWAITING_CHECKS` is not a stall and to budget ~17 minutes before reading
    anything into it. So the one state that *is* a stall is indistinguishable
    from the state the operator has been taught to wait out.

    #1565 was filed off a FALSE alarm and says so: during Conductor run 220 a
    merge-group `722` looked ~40 minutes into its longest step, the conclusion
    "this is hung" was already written, and reading the clock showed 9 minutes
    -- dead normal. The PR merged. That is the measurement: the operator's own
    ability to tell "slow" from "hung" by eye is poor, and a timeout is what
    does that job instead of a human.

PRESENCE IS NOT VALIDITY, SO THIS CHECKS THE NUMBER TOO
    `timeout-minutes: 360` satisfies a presence-only check and changes nothing
    -- it IS the default. A guard that accepts it would report a green tree for
    the exact defect it was written for, which is this repo's recurring
    "presence mistaken for validity" shape (AGENTS.md names it in the
    lessons-ledger preamble). So a declared timeout must also be a positive
    integer at or under MAX_TIMEOUT_MINUTES.

    The ceiling is argued from measurement rather than taste. Over the 30 most
    recent successful runs of each merge-queue workflow (2026-11-23):

        722 `validate`        11.9 - 19.9 min   (median 15.3)
        723 `analyze`          0.8 -  1.3 min   (median  1.1)
        728 `validate-hooks`   0.3 -  0.5 min   (median  0.4)

    The slowest merge-queue gate in the repo finishes in under 20 minutes. A
    ceiling of 60 is 3x that worst case -- loose enough that no healthy run can
    reach it, tight enough that a hang is red in an hour instead of six. It
    bounds the WORST case a future author can declare; it is not a target.

WHAT COUNTS AS A VIOLATION
    A job in a workflow whose top-level `on:` names `merge_group`, where the job

      * declares no `timeout-minutes` at all                    -> finding
      * declares one that is not a positive int <= the ceiling  -> finding
      * declares one as an `${{ }}` expression                   -> finding
      * is a reusable-workflow call (`uses:` at job level)       -> finding

    The last one is an OVER-REPORT ON PURPOSE and the population is 0 today.
    GitHub does not accept `timeout-minutes` on a `uses:` job, so the bound has
    to live in the called workflow, and this guard does not follow the
    reference. Reporting it says "a human must confirm the callee is bounded"
    rather than quietly treating an unverifiable job as fine. An over-report is
    a red run someone reads; an under-report is a green one nobody does.

WHAT IS DELIBERATELY OUT OF SCOPE
    Workflows that do NOT declare `merge_group:`. 126 workflows exist and 23
    declare a timeout somewhere, so a repo-wide requirement is a much larger
    argument and a separate one (#1565 scope note). This guard covers only the
    jobs that can hold the merge queue, which is where the six-hour bound
    actually costs something.

    Step-level `timeout-minutes` is not a substitute and is not counted. A step
    timeout bounds one step; a job can hang in any of the others, in the
    runner's own setup, or in a `post` action after the last step.

FAIL CLOSED
    A workflow that will not parse, an `on:` block that is not one of the three
    legal shapes, a `jobs:` that is not a mapping, a job that is not a mapping,
    and a stale freeze entry are each REPORTED. A guard that goes quiet on what
    it cannot read renders as a pass -- ledger L02.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# The trigger that puts a workflow in front of the merge queue.
MERGE_QUEUE_TRIGGER = "merge_group"

# The ceiling a declared timeout must respect. See PRESENCE IS NOT VALIDITY in
# the docstring: 3x the slowest measured merge-queue gate (19.9 min).
MAX_TIMEOUT_MINUTES = 60

# Empty, and that is the finding to preserve: every merge-queue job in the tree
# is bounded, so this guard fails on ANY new instance. Keys are workflow
# basenames, values the job ids excused within them. An entry here is a reasoned
# exception a reviewer can see in a diff -- not a tuning -- and `compare()`
# reports one that has stopped describing the tree.
KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS: dict[str, tuple[str, ...]] = {}


def trigger_names(doc: dict) -> tuple[list[str], str | None]:
    """Top-level trigger names, plus an error when `on:` is unreadable.

    `on:` is the boolean True in YAML 1.1, so both keys have to be tried -- a
    reader that checks only the string sees a workflow with no triggers at all
    and would skip every merge-queue workflow in the tree while reporting a
    clean run.

    Unlike `generate-workflow-catalog.py`'s version, an unrecognised shape is
    an ERROR here rather than an empty list. A generator may reasonably emit a
    row with no triggers; a guard that cannot read `on:` cannot know whether the
    workflow gates the queue, and answering "no triggers, nothing to check" is
    the fail-open direction.
    """
    block = None
    found = False
    for key in ("on", True):
        if key in doc:
            block = doc[key]
            found = True
            break
    if not found:
        return [], "declares no `on:` block."
    if isinstance(block, str):
        return [block], None
    if isinstance(block, list):
        return [str(x) for x in block], None
    if isinstance(block, dict):
        return [str(k) for k in block], None
    return [], f"`on:` is {type(block).__name__}, not a string, list or mapping."


def timeout_problem(job: dict) -> str | None:
    """Why this job's `timeout-minutes` does not bound it, or None if it does.

    Returns a reason phrased for the reader who has to fix it, because one
    message covering four distinct defects is a guard that fires correctly and
    explains wrongly (ledger L307) -- and the explanation is what gets acted on.
    """
    if "uses" in job:
        return (
            "is a reusable-workflow call (`uses:`), where GitHub does not accept "
            "`timeout-minutes`. The bound has to be declared on the jobs of the "
            "called workflow, and this guard does not follow the reference, so it "
            "cannot confirm one exists"
        )

    if "timeout-minutes" not in job:
        return (
            f"declares no `timeout-minutes`, so it inherits GitHub's default of "
            f"360 minutes and can hold the merge queue for six hours on a hang"
        )

    value = job["timeout-minutes"]

    # An expression is resolved by the runner, not by this file. Reported rather
    # than assumed sound: `${{ inputs.t }}` could be anything, including empty,
    # and an unevaluated string is exactly the input a presence check waves
    # through.
    if isinstance(value, str):
        return (
            f"declares `timeout-minutes: {value}`, a string this guard cannot "
            f"evaluate (an `${{{{ }}}}` expression resolves on the runner). "
            f"Declare a literal integer"
        )

    # bool is an int subclass in Python and `timeout-minutes: true` is not a
    # bound. Checked before the int branch or it would read as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        return (
            f"declares `timeout-minutes: {value!r}` ({type(value).__name__}), "
            f"which is not an integer number of minutes"
        )

    if value <= 0:
        return (
            f"declares `timeout-minutes: {value}`, which is not a positive "
            f"number of minutes"
        )

    if value > MAX_TIMEOUT_MINUTES:
        return (
            f"declares `timeout-minutes: {value}`, above this repo's ceiling of "
            f"{MAX_TIMEOUT_MINUTES}. The slowest measured merge-queue gate here "
            f"finishes in under 20 minutes, so a bound this loose leaves the "
            f"queue wedged for most of the window it was added to close "
            f"(360 is the default it would be replacing in name only)"
        )

    return None


def scan_all() -> tuple[list[str], list[str], int]:
    """Findings, hard errors, and the number of merge-queue workflows scanned."""
    findings: list[str] = []
    hard_errors: list[str] = []
    gating = 0

    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        name = path.name
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            hard_errors.append(f"{name}: will not parse as YAML ({exc}).")
            continue
        if not isinstance(doc, dict):
            hard_errors.append(f"{name}: top level is not a mapping.")
            continue

        triggers, error = trigger_names(doc)
        if error:
            hard_errors.append(f"{name}: {error}")
            continue
        if MERGE_QUEUE_TRIGGER not in triggers:
            continue

        gating += 1

        jobs = doc.get("jobs")
        if jobs is None:
            hard_errors.append(
                f"{name}: declares `{MERGE_QUEUE_TRIGGER}:` but has no `jobs:`."
            )
            continue
        if not isinstance(jobs, dict):
            hard_errors.append(f"{name}: `jobs:` is not a mapping.")
            continue

        for job_id, job in jobs.items():
            where = f"{name}:{job_id}"
            if not isinstance(job, dict):
                hard_errors.append(f"{where}: job is not a mapping.")
                continue
            problem = timeout_problem(job)
            if problem:
                findings.append(f"{where}: {problem}.")

    return findings, hard_errors, gating


def current_map(findings: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for finding in findings:
        parts = finding.split(":", 2)
        out.setdefault(parts[0], []).append(parts[1])
    return out


def compare(current: dict[str, list[str]]) -> list[str]:
    """Report anything in the freeze that has stopped describing the tree."""
    errors: list[str] = []
    for name, job_ids in KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS.items():
        if not (WORKFLOWS / name).is_file():
            errors.append(
                f"{name}: listed in KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS but no such "
                f"workflow exists. The freeze has stopped describing the tree."
            )
            continue
        gone = sorted(set(job_ids) - set(current.get(name, ())))
        if gone:
            errors.append(
                f"{name}: KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS still lists job(s) "
                f"{', '.join(gone)}, which are now bounded. Delete the stale "
                f"entry — a freeze nobody prunes stops being a list of known "
                f"exceptions."
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    findings, hard_errors, gating = scan_all()

    excused = {
        f"{name}:{job_id}"
        for name, job_ids in KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS.items()
        for job_id in job_ids
    }
    live = [f for f in findings if f.split(": ", 1)[0] not in excused]
    errors = hard_errors + compare(current_map(findings))

    if live or errors:
        print("::error::merge-queue job without a bounded `timeout-minutes`\n")
        for error in errors:
            print(f"  {error}")
        for finding in live:
            print(f"  {finding}")
        print(
            "\nA workflow declaring `merge_group:` is evaluated by the merge "
            "queue, so its jobs pace every merge in this repository. GitHub's "
            "default job timeout is 360 minutes, and a wedged queue is "
            "indistinguishable from a healthy one from the PR page — "
            "`AWAITING_CHECKS` either way (#1565). Declare a literal "
            f"`timeout-minutes:` of 1-{MAX_TIMEOUT_MINUTES} on the job, as "
            "727-phantom-revert-guard.yml does. A step-level timeout is not a "
            "substitute: it bounds one step, and a job can hang in any other, "
            "in runner setup, or in a `post` action."
        )
        return 1

    print(
        f"merge-queue job timeouts OK: {gating} workflow(s) declare "
        f"`{MERGE_QUEUE_TRIGGER}:` and every job in them declares a literal "
        f"`timeout-minutes` of 1-{MAX_TIMEOUT_MINUTES}. The freeze "
        f"(KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS) holds "
        f"{len(KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS)} entr"
        f"{'y' if len(KNOWN_UNBOUNDED_MERGE_QUEUE_JOBS) == 1 else 'ies'}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
