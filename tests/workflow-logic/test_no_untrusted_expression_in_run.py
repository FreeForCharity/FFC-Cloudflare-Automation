"""Guard: no `${{ }}` in a `run:` block of a workflow an outsider can trigger.

The runner substitutes an expression into the script **text** and only then hands
that text to bash/pwsh. So a value carrying a quote does not arrive as data — it
closes the string literal, and whatever follows is parsed as code. When the value
came out of an issue body, the author of that code is whoever opened the issue.

`701. Website - Provision` was the live instance. Trigger `issues: [assigned]` on
a **public** repo; the `resolve` job parses ~20 fields out of the issue body with
`normalizeInput`, which trims and maps the "_No response_" sentinel to empty and
does nothing else; the `repo` and `content` jobs then interpolated those fields
into `pwsh` string literals in the same steps that hold the org-wide write PAT in
`$env:GH_TOKEN`:

    $charityName = "${{ needs.resolve.outputs.charity_name }}"

An organization name of `x"; <code>; "` renders as three statements. The
`*_json` fields were worse-hidden: `JSON.stringify` escapes `"` but not `'`, and
they sat in SINGLE-quoted literals, so an apostrophe in an address or a board
member's name was enough — an injection and a plain correctness bug at once.

## Why this shape of rule

The repo already knew the rule: `105`'s "Manage DNS Record" step carries the
comment *"Untrusted/user-supplied values are passed via env (never interpolated
directly into the script body) so they cannot break out of the string and inject
PowerShell"* — while the two sibling steps in the same file did it the unsafe
way. Prose next to one of three steps is not a guard, which is the whole reason
this file exists.

The rule here is **absolute**: in a workflow reachable from an untrusted event,
a `run:` block contains no `${{ }}` at all. Not a denylist of taint carriers
(`needs.*`, `github.event.*`, `steps.*.outputs.*`), because a denylist has to
anticipate every carrier and stays silent on the one nobody listed. Not an
allowlist of "safe" contexts either, because `env.NAME` is only as safe as
NAME's definition and that turns a one-line rule into a taint analysis.

Absolute is affordable *because it costs nothing*: the remedy is always the same
one line (`env:` + `$env:NAME` / `"$NAME"`), it is what these workflows already
do everywhere else, and after the fixes in this commit the tree has **zero**
exceptions. A rule with no exception list cannot rot into a stale one.

## The two directions

A guard that only checks the tree can go vacuous — if the trigger detection
breaks, every workflow silently leaves the untrusted set and the sweep passes by
inspecting nothing. So `test_the_scan_can_actually_see_the_defect` runs the same
scanner over a synthetic workflow carrying the exact `701` line, and
`test_the_untrusted_trigger_set_is_not_empty` pins that real workflows are still
being found. `test_the_scan_leaves_correct_code_alone` covers the false-positive
surface: expressions in `env:`/`with:`/`if:`/`name:` are the *remedy*, and a
guard that flagged those would have to be switched off to land the fix for the
thing it guards.
"""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS

# Events whose payload is authored outside the repo's write-access set. An issue
# body, an issue title, a comment, a fork's PR — all of it is typed by whoever
# showed up. `repository_dispatch` is included because its `client_payload`
# crosses a repo boundary: 701 accepts one from FFCadmin, so a compromise there
# reaches this repo's runners with the same shape of value.
#
# `pull_request_target`, `discussion*`, `fork` and `watch` are listed although
# nothing uses them today — the cost of naming an unused event is nothing, and
# the cost of omitting one is that the first workflow to adopt it is unguarded.
UNTRUSTED_EVENTS = frozenset(
    {
        "issues",
        "issue_comment",
        "repository_dispatch",
        "pull_request_target",
        "discussion",
        "discussion_comment",
        "fork",
        "watch",
    }
)

_EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}", re.DOTALL)


def _triggers(workflow: dict) -> set[str]:
    """The event names a workflow is registered for.

    `on` is the YAML 1.1 boolean `True` after `yaml.safe_load` — the classic
    Norway problem, and getting it wrong here would empty the untrusted set and
    make this whole module pass while reading nothing. Both spellings are
    checked, and `test_the_untrusted_trigger_set_is_not_empty` fails loudly if
    the lookup ever stops finding workflows.
    """
    on = workflow.get(True, workflow.get("on"))
    if isinstance(on, dict):
        return {str(k) for k in on}
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(k) for k in on}
    return set()


def _run_steps(workflow: dict):
    """Yield (job_id, step_index, step_name, run_script) for every `run:` step."""
    for job_id, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            script = step.get("run")
            if isinstance(script, str):
                yield job_id, index, str(step.get("name") or f"step {index}"), script


def _violations(path: pathlib.Path) -> list[str]:
    """Expressions interpolated into a run script of an untrusted-reachable file.

    Returns [] for a workflow no outsider can trigger — this rule is about who
    authors the bytes, so a dispatch-only workflow (write access required to
    fire it at all) is out of scope and must not be flagged.
    """
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        return []
    reachable = _triggers(workflow) & UNTRUSTED_EVENTS
    if not reachable:
        return []

    found = []
    for job_id, _index, name, script in _run_steps(workflow):
        for match in _EXPRESSION.finditer(script):
            expression = match.group(1)
            found.append(
                f"{path.name}: job '{job_id}', step '{name}' interpolates "
                f"`${{{{ {expression} }}}}` into its run script. This workflow is "
                f"reachable from {sorted(reachable)}, so the value may be "
                "attacker-authored, and the runner substitutes it into the "
                "script TEXT before the shell parses it — a quote in the value "
                "ends the literal and the rest runs as code. Pass it through "
                "`env:` instead and read it as `$env:NAME` (pwsh) or \"$NAME\" "
                "(bash)."
            )
    return found


def test_no_untrusted_workflow_interpolates_into_a_run_script():
    """The tree is clean. This is the rule; everything else defends it."""
    violations = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        violations.extend(_violations(path))
    assert not violations, "\n".join(violations)


def test_the_untrusted_trigger_set_is_not_empty():
    """The sweep is actually inspecting workflows.

    Without this, a change that broke `_triggers` (the `on:`/`True` key being
    the obvious candidate) would empty the untrusted set and turn the rule above
    into a test that reads no files and passes. Guarding a sweep's *input* is
    the cheapest way to keep it from going quietly vacuous.
    """
    reachable = {
        path.name
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if _triggers(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
        & UNTRUSTED_EVENTS
    }
    assert "701-website-provision.yml" in reachable, (
        "701 is triggered by `issues: [assigned]` and must be in the untrusted "
        f"set; the scan found {sorted(reachable)}. If 701's triggers really "
        "changed, update this test — but check first that `_triggers` still "
        "reads the `on:` key, because that is what usually broke."
    )
    assert len(reachable) >= 3, (
        f"only {len(reachable)} workflow(s) classed as untrusted-reachable "
        f"({sorted(reachable)}); at least 105, 113, 206 and 701 qualify. A "
        "sudden drop means the trigger detection broke, not that the repo got "
        "safer."
    )


def _scan_sample(text: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        sample = pathlib.Path(tmp) / "sample.yml"
        sample.write_text(text, encoding="utf-8")
        return _violations(sample)


# The exact line that shipped in 701, kept verbatim. A synthetic payload would
# drift from the defect; this one cannot.
_DEFECT_SAMPLE = """
name: '999. Sample'
on:
  issues:
    types: [assigned]
jobs:
  repo:
    runs-on: windows-latest
    steps:
      - name: Create repo from template + enable Pages
        shell: pwsh
        run: |
          $charityName = "${{ needs.resolve.outputs.charity_name }}"
"""


def test_the_scan_can_actually_see_the_defect():
    """A guard that cannot fail is not a guard.

    Run against the real pre-fix line from 701, not a hand-written stand-in.
    """
    violations = _scan_sample(_DEFECT_SAMPLE)
    assert len(violations) == 1, (
        f"expected the 701 line to be flagged exactly once, got {violations!r}"
    )
    assert "charity_name" in violations[0]
    assert "env:" in violations[0], (
        "the message must name the remedy — a violation a reader cannot act on "
        "gets suppressed rather than fixed"
    )


# Each entry is (label, workflow text) that MUST NOT be flagged. Three of the
# four are the remedy itself: if `env:`, `with:` or `if:` were matched, landing
# the fix for this defect would require disabling this guard.
_CLEAN_SAMPLES = [
    (
        "expressions in env: are the remedy",
        """
name: '999. Sample'
on:
  issues:
    types: [assigned]
jobs:
  repo:
    runs-on: ubuntu-latest
    steps:
      - name: Safe
        env:
          CHARITY_NAME: ${{ needs.resolve.outputs.charity_name }}
        run: |
          echo "$CHARITY_NAME"
""",
    ),
    (
        "expressions in with: are not script text",
        """
name: '999. Sample'
on:
  issue_comment:
    types: [created]
jobs:
  repo:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/thing
        with:
          value: ${{ github.event.comment.body }}
      - run: echo done
""",
    ),
    (
        "expressions in if: are evaluated, never substituted into a script",
        """
name: '999. Sample'
on:
  issues:
    types: [assigned]
jobs:
  repo:
    if: ${{ github.event.issue.number != 0 }}
    runs-on: ubuntu-latest
    steps:
      - run: echo done
""",
    ),
    (
        "a dispatch-only workflow is out of scope — no outsider can fire it",
        """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
  schedule:
    - cron: '0 6 * * *'
jobs:
  repo:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ inputs.domain }}"
""",
    ),
    (
        "a shell variable is not an expression",
        """
name: '999. Sample'
on:
  issues:
    types: [assigned]
jobs:
  repo:
    runs-on: ubuntu-latest
    steps:
      - run: |
          d="${RANDOM}${RANDOM}"
          echo "$env:GH_TOKEN is a pwsh variable, ${d} a bash one"
""",
    ),
]


def test_the_scan_leaves_correct_code_alone():
    """The false-positive surface, sampled deliberately.

    Ledger L27's review found four of nine findings were a guard misfiring on
    correct code — which is how a guard gets switched off rather than fixed. The
    first three samples here are the *remedy* for this very defect.
    """
    misfires = []
    for label, text in _CLEAN_SAMPLES:
        violations = _scan_sample(text)
        if violations:
            misfires.append(f"{label}: {violations!r}")
    assert not misfires, "\n".join(misfires)


def test_the_dispatch_only_carve_out_is_not_a_hole():
    """Adding an untrusted trigger to an otherwise-safe workflow flags it.

    The carve-out above is the one place this rule chooses not to look. Pinning
    the boundary means the carve-out is a decision under test rather than an
    assumption: the same script, plus `issues:`, must fail.
    """
    safe = _CLEAN_SAMPLES[3][1]
    assert not _scan_sample(safe)
    now_reachable = safe.replace(
        "on:\n  workflow_dispatch:",
        "on:\n  issues:\n    types: [labeled]\n  workflow_dispatch:",
    )
    violations = _scan_sample(now_reachable)
    assert len(violations) == 1, (
        "adding an `issues:` trigger must bring the same run block into scope; "
        f"got {violations!r}"
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
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
