"""Guard: a gated workflow that takes `dry_run` must surface it in `run-name` (#1107).

The Conductor's auto-approval rule (docs/workflow-safety-and-approvals.md,
"Conductor auto-approval policy") turns on whether *this dispatch* set
`dry_run=true`. Nothing available to an approver answers that:

- the input's `default:` describes the form, not the run;
- the REST run object carries **no** `workflow_dispatch` inputs at all;
- the job is `waiting`, so there are no logs — that is what a gate is.

`run-name` is the one author-controlled string that reaches the approval screen,
and it is free. On 2026-08-07 a `106 / cloudflare-prod-write` approval appeared
whose `dry_run` was unreadable from every one of those surfaces, so the rule's
answer was "hold" — correct and useless, since it is also the answer for a pure
rehearsal (L177).

**Parsed, never grepped.** The sweep in #1107 used `^run-name:\\s*(.+)$` and
reported 16 offenders. The real number was 15: `229-whmcs-client-field-populate`
already complied, on a `run-name:` whose value begins on the *following* line,
which that regex cannot see. A single-line regex over YAML answers a question
about the wrong population — the same shape as L172. `yaml.safe_load` sees the
folded scalar whole, so the count is a count of workflows rather than of lines
that happen to fit.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS

MARKER = "dry_run"


def _dispatch_inputs(doc: dict) -> dict:
    """Inputs a caller can set, across both dispatch surfaces.

    PyYAML parses the bare key `on:` as the boolean `True` (YAML 1.1 reads it as
    the y/n family), so `doc.get("on")` alone silently returns nothing for every
    workflow in this repo. `workflow_call` counts because 729 is reusable and
    still dispatchable directly, which is the path that reaches its gate.
    """
    on = doc.get(True, doc.get("on"))
    found: dict = {}
    if isinstance(on, dict):
        for event in ("workflow_dispatch", "workflow_call"):
            block = on.get(event)
            if isinstance(block, dict) and isinstance(block.get("inputs"), dict):
                found.update(block["inputs"])
    return found


def _gated_environments(doc: dict) -> list[str]:
    """Every `environment:` a job in this workflow declares.

    Any environment at all, not just the reviewer-gated ones: the marker costs
    nothing on an ungated lane, and an environment that gains reviewers in
    Settings must not silently create a new undecidable gate. This guard is
    about what the approver can read, not about which lanes currently pause.

    `environment:` has two spellings — a bare string, and a mapping carrying
    `name` and `url`. `721-deploy-pages.yml`'s `deploy` job uses the mapping
    form today, so returning the raw value would put a dict in a `list[str]`.
    Only truthiness is read below, and a dict is truthy, so nothing would have
    failed — which is the reason to normalise now rather than when something
    does. Mirrors `_job_environment_names` in `test_gated_env_hygiene.py`.
    """
    jobs = doc.get("jobs") or {}
    names = []
    for job in jobs.values():
        if not isinstance(job, dict):
            continue
        env = job.get("environment")
        if isinstance(env, dict):
            name = env.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        elif env:
            names.append(str(env))
    return names


def _workflows() -> list[tuple[pathlib.Path, dict]]:
    out = []
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if isinstance(doc, dict):
            out.append((path, doc))
    return out


def undecidable_gates(workflows) -> list[str]:
    """Names of gated workflows taking `dry_run` that do not surface it."""
    bad = []
    for path, doc in workflows:
        if MARKER not in _dispatch_inputs(doc):
            continue
        if not _gated_environments(doc):
            continue
        if MARKER not in str(doc.get("run-name") or ""):
            bad.append(path.name)
    return bad


def test_every_gated_dry_run_workflow_surfaces_it_in_run_name():
    bad = undecidable_gates(_workflows())
    assert not bad, (
        f"{len(bad)} gated workflow(s) take a dry_run input and do not surface it in "
        f"run-name, so an approver cannot apply the auto-approval rule to them: "
        f"{', '.join(bad)}. Append `(dry_run=${{{{ inputs.dry_run }}}})` to the "
        f"existing run-name (see docs/workflow-safety-and-approvals.md)."
    )


def test_the_guard_has_a_population_to_check():
    """A guard over an empty set passes for the wrong reason.

    If `_dispatch_inputs` or `_gated_environments` stops matching — a PyYAML
    change, a key rename, a glob that misses `.yaml` — the assertion above goes
    green while checking nothing. This pins the denominator instead.
    """
    workflows = _workflows()
    population = [
        p.name
        for p, d in workflows
        if MARKER in _dispatch_inputs(d) and _gated_environments(d)
    ]
    assert len(workflows) >= 100, f"only {len(workflows)} workflow files parsed"
    assert len(population) >= 25, (
        f"only {len(population)} gated workflows with a dry_run input were found "
        f"(29 on main at the time of #1107) — the extraction is probably broken, "
        f"not the fleet."
    )


def test_a_multiline_run_name_is_read_whole():
    """The defect that made #1107's own sweep report 16 instead of 15.

    `229-whmcs-client-field-populate.yml` complies on a continuation line. A
    line-oriented check reports it as an offender; this fixture is that exact
    shape, so a future rewrite of `undecidable_gates` back to a regex fails
    here rather than in a run summary.
    """
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name:\n"
        "  'Something long about ${{ inputs.target }}, dry_run=${{ inputs.dry_run }}'\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      dry_run:\n"
        "        type: boolean\n"
        "jobs:\n"
        "  go:\n"
        "    environment: whmcs-prod\n"
        "    runs-on: ubuntu-latest\n"
    )
    assert undecidable_gates([(pathlib.Path("fixture.yml"), doc)]) == []


def test_the_guard_reports_a_workflow_that_hides_it():
    """Polarity: prove the check can fail, not merely that it passes today."""
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name: 'Enforce Standard: ${{ inputs.domain }}'\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      dry_run:\n"
        "        type: boolean\n"
        "jobs:\n"
        "  go:\n"
        "    environment: cloudflare-prod-write\n"
        "    runs-on: ubuntu-latest\n"
    )
    assert undecidable_gates([(pathlib.Path("fixture.yml"), doc)]) == ["fixture.yml"]


def test_the_mapping_form_of_environment_is_a_gate_too():
    """`environment: {name: …, url: …}` is the other legal spelling.

    `721-deploy-pages.yml` uses it. Reading `job["environment"]` raw put a dict
    where a name belongs; the only consumer was a truthiness test, so it passed
    — and a check that is right for a reason it does not state is one refactor
    from being wrong. Asserts the *name*, not merely that something truthy came
    back, so a regression to the raw value fails here.
    """
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name: 'Deploy ${{ inputs.target }}'\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      dry_run:\n"
        "        type: boolean\n"
        "jobs:\n"
        "  go:\n"
        "    environment:\n"
        "      name: github-prod\n"
        "      url: https://example.org\n"
        "    runs-on: ubuntu-latest\n"
    )
    assert _gated_environments(doc) == ["github-prod"]
    assert undecidable_gates([(pathlib.Path("fixture.yml"), doc)]) == ["fixture.yml"]


def test_an_ungated_dry_run_workflow_is_not_reported():
    """No environment means no approval screen, so there is nothing to inform."""
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name: 'No gate here'\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      dry_run:\n"
        "        type: boolean\n"
        "jobs:\n"
        "  go:\n"
        "    runs-on: ubuntu-latest\n"
    )
    assert undecidable_gates([(pathlib.Path("fixture.yml"), doc)]) == []


def test_the_safety_doc_states_the_convention():
    """The tier above a test is a rule someone can read before writing the file."""
    doc = (
        pathlib.Path(__file__).resolve().parents[2]
        / "docs"
        / "workflow-safety-and-approvals.md"
    ).read_text(encoding="utf-8")
    assert "MUST surface it in `run-name`" in doc, (
        "docs/workflow-safety-and-approvals.md no longer states the run-name "
        "convention this module enforces (#1107 AC3)."
    )
    assert pathlib.Path(__file__).name in doc, (
        "the safety doc should name this module as the enforcement, so a reader "
        "who changes the rule knows what will fail."
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
