"""Guard: a gated workflow must name its TARGET-SELECTING inputs in `run-name`.

`test_dry_run_visible_in_run_name.py` (#1107) asks whether an approver can tell
*whether* a waiting run is a rehearsal. This asks the next question: can they
tell *what it would touch*?

The two are independent, and 120 is the worked example of passing the first
while failing the second. On 2026-10-08 run `36847830029` sat waiting with

    Bulk cutover staging->apex (dry_run=false, skip_dns=false, skip_cname=true)

so the existing guard was green -- all three booleans are right there. But 120
also declares `domains` (blank = the whole 13-domain fleet from
`config/ffc-ex-cutover-domains.json`) and `legacy_apex_ip` (blank = the HostPapa
default, and a wrong value leaves a site's old apex record beside the Pages
records, splitting the apex between two hosts). Neither reaches the title.

Nothing else reaches it either, which is the point:

- the input's `default:` describes the form, not the run;
- the REST run object carries **no** `workflow_dispatch` inputs at all;
- the job is `waiting`, so there are no logs -- that is what a gate is.

So the Conductor could tell @clarkemoyer that the run was live, and could not
tell him which domains it would cut over. A reviewer cannot consent to a blast
radius they cannot read, and `run-name` is the one author-controlled string that
reaches the approval screen. It is free.

**Scope vocabulary only, deliberately.** The blanket rule -- every input in
`run-name` -- is wrong, and measurably so: 68 gated workflows omit at least one
input, and 701 alone declares 21 including `mission`, `footer_phone` and
`footer_address`. Putting those in a run title would be useless and would leak
personal data into a public run list. Narrowing to the inputs that *select the
target set* takes the population to 36 and the offenders to 8, of which five are
the bulk/fleet workflows -- precisely the ones whose blast radius is widest.

`issue_number` and `client_id` are excluded on purpose. They are provenance and
billing identifiers, not target selectors, and every workflow carrying one
already names the domain it acts on.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# Imported rather than copied a third time: "gated" and "what a caller can set"
# must mean one thing across both run-name guards, or they can disagree about
# the same workflow. `test_check_provisioned_build.py` and
# `test_sample_charities.py` already import siblings, so this is the idiom.
from test_dry_run_visible_in_run_name import (
    _dispatch_inputs,
    _gated_environments,
    _workflows,
)

# Names that choose WHICH targets a run writes to. Matched whole and
# case-insensitively, because the PowerShell-era workflows capitalise
# (`Domains`, `RepoName`) and the newer ones do not.
SCOPE_INPUT = re.compile(r"^(domains?|repos?|reponame|targets?|zones?|sites?)$", re.I)

# Burn-down list, seeded with the offenders present when this guard landed.
# Each entry is a gated workflow whose target input an approver cannot read.
# Shrink it; never grow it. `test_the_allowlist_has_no_dead_entries` fails if an
# entry is fixed and left here, so burning one down forces its removal.
#
# 120 is deliberately NOT here: it is the workflow that motivated the guard, it
# had a live gate waiting when this was written, and a guard that exempts its
# own worked example proves nothing.
# Inputs that CANNOT reach `run-name`, because another guard forbids
# interpolating them at all. Keyed (workflow, input) and deliberately tiny: this
# is a statement that the two requirements genuinely conflict, not a place to
# park work.
#
# CI caught this the honest way. `inputs.domains` in 120 is a free-text dispatch
# input under a whole-file no-interpolation freeze (#1080), enforced by
# `test_120_cutover_gh_errors.py::test_the_domains_input_is_not_interpolated_anywhere_in_120`,
# whose only legal spelling in that file is the `IN_DOMAINS:` env mapping — and
# which additionally pins one mapping per consuming step, so even an extra legal
# spelling fails it. A readability guard does not get to weaken an injection
# guard, so the demand yields here.
#
# **The consequence is a real and unfixed operational gap, not a resolved one:**
# an approver of a live 120 run cannot read its domain list from anywhere. The
# mechanism for that is a reviewed `dry_run=true` preview, and on gate
# `36847830029` the paired dry run was cancelled 93 seconds after dispatch, so
# no preview ever existed. Tracked in #719's run-225 gate analysis.
NON_INTERPOLABLE = {
    ("120-bulk-cutover-to-github-pages.yml", "domains"),
}

KNOWN_UNREADABLE_TARGETS = {
    "119-bulk-staging-cname-github-pages.yml",  # domains, target
    "301-m365-domain-preflight.yml",  # domain
    "304-m365-dkim-enable.yml",  # domain
    "506-google-fleet-telemetry-reachability.yml",  # domains
    "720-create-repo.yml",  # RepoName
    "732-bulk-create-repos.yml",  # Domains
    "746-bulk-enable-pages.yml",  # Domains
}


def unreadable_targets(workflows) -> dict[str, list[str]]:
    """Gated workflows whose target-selecting inputs are absent from `run-name`.

    Keyed by filename, valued by the input names that do not appear. Returns the
    raw finding -- the allowlist is applied by the callers, so the dead-entry
    test can see an entry that has been fixed.
    """
    bad: dict[str, list[str]] = {}
    for path, doc in workflows:
        if not _gated_environments(doc):
            continue
        scope_inputs = [k for k in _dispatch_inputs(doc) if SCOPE_INPUT.match(k)]
        if not scope_inputs:
            continue
        run_name = str(doc.get("run-name") or "")
        missing = [
            k
            for k in scope_inputs
            if k not in run_name and (path.name, k) not in NON_INTERPOLABLE
        ]
        if missing:
            bad[path.name] = sorted(missing)
    return bad


def test_every_gated_workflow_names_its_target_inputs_in_run_name():
    found = unreadable_targets(_workflows())
    new = {k: v for k, v in found.items() if k not in KNOWN_UNREADABLE_TARGETS}
    assert not new, (
        f"{len(new)} gated workflow(s) take an input that selects what they write to "
        f"and do not surface it in run-name, so an approver cannot read the blast "
        f"radius of a run they are being asked to approve: "
        + "; ".join(f"{k} ({', '.join(v)})" for k, v in sorted(new.items()))
        + ". Append the effective value to the existing run-name -- render the "
        "default rather than a bare interpolation, e.g. "
        "`domains=${{ inputs.domains || 'fleet-default' }}`, so a blank input does "
        "not produce a blank field (see docs/workflow-safety-and-approvals.md)."
    )


def test_the_guard_has_a_population_to_check():
    """A denominator, so the assertion above cannot pass by matching nothing.

    If `_dispatch_inputs`, `_gated_environments` or `SCOPE_INPUT` stops matching
    -- a PyYAML change, a key rename, a workflow renumbering -- the guard goes
    green while checking zero workflows. 36 qualified when this landed; the floor
    is set below that so ordinary churn does not trip it, but a collapse does.
    """
    population = [
        path.name
        for path, doc in _workflows()
        if _gated_environments(doc)
        and any(SCOPE_INPUT.match(k) for k in _dispatch_inputs(doc))
    ]
    assert len(population) >= 25, (
        f"only {len(population)} gated workflow(s) were seen to declare a "
        f"target-selecting input, against 36 when this guard landed -- the matcher has "
        f"probably stopped matching rather than the repo having changed that much: "
        f"{', '.join(sorted(population))}"
    )


def test_the_allowlist_has_no_dead_entries():
    """A fixed workflow must leave the allowlist in the same PR that fixes it.

    Otherwise the list becomes a record of what was once broken, every later
    reader over-counts the debt, and a workflow can silently regress back into
    an entry that is still sitting there waiting for it.
    """
    found = unreadable_targets(_workflows())
    dead = sorted(KNOWN_UNREADABLE_TARGETS - set(found))
    assert not dead, (
        f"{len(dead)} allowlist entr(y/ies) no longer offend and must be deleted from "
        f"KNOWN_UNREADABLE_TARGETS: {', '.join(dead)}"
    )


def test_120_is_never_blanket_exempt():
    """The worked example stays measured for everything it CAN surface.

    120 is why this guard exists, and `domains` turned out to be structurally
    unable to reach its run-name (see NON_INTERPOLABLE). That is a carve-out for
    one input, not for the workflow: `legacy_apex_ip` is surfaced and must stay
    so, and any target input 120 gains later is demanded by default.
    """
    assert "120-bulk-cutover-to-github-pages.yml" not in KNOWN_UNREADABLE_TARGETS, (
        "120 is the workflow this guard was written for; allowlisting the whole "
        "file would leave the guard with no live subject. If one of its inputs "
        "cannot be interpolated, carve out THAT INPUT in NON_INTERPOLABLE."
    )
    run_name = ""
    for path, doc in _workflows():
        if path.name == "120-bulk-cutover-to-github-pages.yml":
            run_name = str(doc.get("run-name") or "")
    assert "inputs.legacy_apex_ip" in run_name, (
        "120's run-name must keep surfacing legacy_apex_ip -- a wrong value there "
        "leaves the site's old apex record beside the Pages records and splits the "
        "apex between two hosts, which is exactly the blast-radius fact an approver "
        "needs and the one input of the two that CAN be rendered."
    )


def test_a_non_interpolable_input_is_not_demanded():
    """The carve-out must actually suppress the demand, or 120 goes red again."""
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name: 'Bulk thing (dry_run=${{ inputs.dry_run }})'\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      domains:\n"
        "        type: string\n"
        "jobs:\n"
        "  go:\n"
        "    runs-on: ubuntu-latest\n"
        "    environment: github-prod\n"
    )
    pretend = pathlib.Path("120-bulk-cutover-to-github-pages.yml")
    assert unreadable_targets([(pretend, doc)]) == {}
    # ...and the carve-out is keyed on the PAIR, so the same input elsewhere is
    # still demanded. A workflow-wide or input-wide key would silently exempt
    # every `domains` in the repo.
    assert unreadable_targets([(pathlib.Path("other.yml"), doc)]) == {
        "other.yml": ["domains"]
    }


def test_the_carve_out_has_no_dead_entries():
    """A freeze that is lifted must take its carve-out with it."""
    live = set()
    for path, doc in _workflows():
        for k in _dispatch_inputs(doc):
            live.add((path.name, k))
    dead = sorted(NON_INTERPOLABLE - live)
    assert not dead, (
        f"{len(dead)} NON_INTERPOLABLE entr(y/ies) no longer name a declared input "
        f"and must be deleted: {dead}"
    )


def test_a_missing_target_input_is_reported():
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name: 'Bulk thing (dry_run=${{ inputs.dry_run }})'\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      domains:\n"
        "        type: string\n"
        "      dry_run:\n"
        "        type: boolean\n"
        "jobs:\n"
        "  go:\n"
        "    runs-on: ubuntu-latest\n"
        "    environment: github-prod\n"
    )
    assert unreadable_targets([(pathlib.Path("fixture.yml"), doc)]) == {
        "fixture.yml": ["domains"]
    }


def test_a_named_target_input_is_silent():
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name: \"Bulk thing (domains=${{ inputs.domains || 'fleet-default' }})\"\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      domains:\n"
        "        type: string\n"
        "jobs:\n"
        "  go:\n"
        "    runs-on: ubuntu-latest\n"
        "    environment: github-prod\n"
    )
    assert unreadable_targets([(pathlib.Path("fixture.yml"), doc)]) == {}


def test_an_ungated_workflow_is_left_alone():
    """The question is what an APPROVER can read. With no gate there is no approver."""
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name: 'Bulk thing'\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      domains:\n"
        "        type: string\n"
        "jobs:\n"
        "  go:\n"
        "    runs-on: ubuntu-latest\n"
    )
    assert unreadable_targets([(pathlib.Path("fixture.yml"), doc)]) == {}


def test_a_non_scope_input_is_not_demanded():
    """`mission` and `footer_phone` must never be pulled into a public run title."""
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name: 'Provision ${{ inputs.domain }}'\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      domain:\n"
        "        type: string\n"
        "      mission:\n"
        "        type: string\n"
        "      footer_phone:\n"
        "        type: string\n"
        "jobs:\n"
        "  go:\n"
        "    runs-on: ubuntu-latest\n"
        "    environment: github-prod\n"
    )
    assert unreadable_targets([(pathlib.Path("fixture.yml"), doc)]) == {}


def test_capitalised_input_names_are_matched():
    """`Domains` / `RepoName`: the PowerShell-era spelling must not slip through."""
    doc = yaml.safe_load(
        "name: fixture\n"
        "run-name: 'Create repo'\n"
        "on:\n"
        "  workflow_dispatch:\n"
        "    inputs:\n"
        "      RepoName:\n"
        "        type: string\n"
        "jobs:\n"
        "  go:\n"
        "    runs-on: ubuntu-latest\n"
        "    environment: github-prod\n"
    )
    assert unreadable_targets([(pathlib.Path("fixture.yml"), doc)]) == {
        "fixture.yml": ["RepoName"]
    }


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
