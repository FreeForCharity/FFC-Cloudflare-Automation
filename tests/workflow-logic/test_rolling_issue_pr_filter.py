"""Guard: a script that lists open issues and CLOSES one must not select a PR.

`github.rest.issues.listForRepo` returns **pull requests as well as issues** —
they are the same object in GitHub's REST model, distinguished only by a
`pull_request` key. Every rolling-issue monitor in this repo finds its issue by
scanning that listing for an HTML-comment marker, and the clean-run path then
comments "✅ Recovered… Auto-closing." and closes whatever it found. So an
unfiltered lookup lets a scheduled workflow **close somebody's open pull
request**, on its own.

It is reachable, not theoretical: the marker is an HTML comment, hence invisible
in a rendered PR body, so a PR carries it without its author ever seeing it — a
PR editing one of the `*-lib.js` files and quoting its marker constant, a
Conductor lessons PR describing the rolling-issue pattern, an agent pasting a
rendered issue body in as evidence. A daily monitor needs that to coincide once.

**The rule was already known here and still propagated.** 739 carries the
comment *"Issues API returns PRs too; drop them everywhere"*, and it reached the
two workflows that enumerate issues to *report* — never the seven that
enumerate them in order to *close one*, the higher-consequence use. #979 was
told to follow 321 "exactly", and following it exactly is how the defect
spread. That is what this file exists to stop: the eighth copy.

This is a SHAPE check and knows it. It cannot tell a correct filter from a
broken one — `tests/workflow-logic/` carries a behavioural test per workflow
for that (each asserts a marked PR is never selected, and that a marked issue
is still found when a marked PR is listed ahead of it). What this adds is that
a NEW copy of the pattern cannot land unnoticed.

Per AGENTS.md §Merging, a guard that cannot be shown to fail is decoration, so
`test_the_scan_flags_the_pre_fix_form_of_a_real_workflow` reconstructs the
actual pre-#980 line in each shipped file and asserts the scan rejects it.

Refs #980, #979, #977, #752.
"""

from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS  # noqa: E402

# The six fixed in #980 plus the two that were already right. Named so a removal
# is visible: if one of these stops matching the "lists and closes" shape, the
# coverage assertion below fails rather than the file silently dropping out.
KNOWN_ROLLING_MONITORS = {
    "228-whmcs-fraud-review.yml",
    "321-azure-kv-credential-liveness.yml",
    "738-fleet-smoke-engine-drift-audit.yml",
    "740-scheduled-workflow-failure-alert.yml",
    "741-fleet-security-audit-coverage.yml",
    "743-fleet-security-header-audit.yml",
    "744-repo-public-feed-freshness.yml",
}


def closes_an_issue(script: str) -> bool:
    """Does this script close an issue it looked up? (the consequential half)"""
    return "issues.update" in script and (
        "state: 'closed'" in script or 'state: "closed"' in script
    )


def selects_from_a_listing(script: str) -> bool:
    return "issues.listForRepo" in script


def scan_script(script: str) -> list[str]:
    """Reasons this script's rolling-issue lookup is unsafe. Empty == fine."""
    if not (selects_from_a_listing(script) and closes_an_issue(script)):
        return []
    # A library lookup is the preferred form: it is unit-tested in one place
    # rather than re-derived per workflow, which is how this spread.
    if "findRollingIssue(" in script:
        return []
    # Otherwise the in-line selection must drop pull requests itself. The
    # selection is the `.find(...)` that matches the marker.
    selections = [
        ln.strip()
        for ln in script.splitlines()
        if ".find(" in ln and ".includes(" in ln and not ln.strip().startswith("//")
    ]
    if not selections:
        return [
            "lists open issues and closes one, but no marker lookup is visible — "
            "if the selection moved, teach this guard where it went (#980)"
        ]
    return [
        f"selects the rolling issue without dropping pull requests: {ln}"
        for ln in selections
        if "pull_request" not in ln
    ]


def github_script_bodies(path: pathlib.Path) -> list[str]:
    """Every `actions/github-script` body in a workflow file."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        return []
    out = []
    for job in (doc.get("jobs") or {}).values():
        for step in (job or {}).get("steps") or []:
            if not isinstance(step, dict):
                continue
            if str(step.get("uses", "")).startswith("actions/github-script"):
                script = (step.get("with") or {}).get("script")
                if isinstance(script, str):
                    out.append(script)
    return out


def scan_repo() -> dict[str, list[str]]:
    findings: dict[str, list[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for script in github_script_bodies(path):
            reasons = scan_script(script)
            if reasons:
                findings.setdefault(path.name, []).extend(reasons)
    return findings


# --- the repo is clean -----------------------------------------------------


def test_no_workflow_closes_an_issue_it_selected_without_dropping_prs():
    findings = scan_repo()
    assert not findings, findings


def test_the_scan_actually_reaches_the_known_rolling_monitors():
    """A scan that matches nothing would pass the test above vacuously."""
    matched = {
        path.name
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for script in github_script_bodies(path)
        if selects_from_a_listing(script) and closes_an_issue(script)
    }
    missing = KNOWN_ROLLING_MONITORS - matched
    assert not missing, f"the scan no longer sees these rolling monitors: {sorted(missing)}"


# --- the scan can see the defect (a guard that cannot fail is decoration) ---


PRE_FIX_FORMS = [
    # 321/738/741/743 — the library-constant form, verbatim from pre-#980 main.
    "const existing = open.find((i) => i.body && i.body.includes(lib.MARKER));",
    # 228 — a local `marker` const, same shape.
    "const existing = open.find((i) => i.body && i.body.includes(marker));",
    # 740 — unparenthesised arrow.
    "const existing = open.find(i => i.body && i.body.includes(marker));",
]

CLOSE_TAIL = """
await github.rest.issues.update({
  owner, repo, issue_number: existing.number, state: 'closed',
});
"""


def _synthetic(selection: str) -> str:
    return (
        "const open = await github.paginate(github.rest.issues.listForRepo, "
        "{ owner, repo, state: 'open', per_page: 100 });\n" + selection + "\n" + CLOSE_TAIL
    )


def test_the_scan_flags_every_pre_fix_selection_form():
    for form in PRE_FIX_FORMS:
        reasons = _synthetic(form)
        assert scan_script(reasons), f"the guard does not see the defect in: {form}"


def test_the_scan_flags_the_pre_fix_form_of_a_real_workflow():
    """Mutation-check against shipped source, not a hand-written sample.

    Puts the real pre-#980 line back into each shipped file and asserts the scan
    rejects it — the AGENTS.md rule for reviewing a guard, applied to itself.
    """
    checked = 0
    for name, fixed, broken in [
        (
            "321-azure-kv-credential-liveness.yml",
            "const existing = lib.findRollingIssue(open);",
            "const existing = open.find((i) => i.body && i.body.includes(lib.MARKER));",
        ),
        (
            "740-scheduled-workflow-failure-alert.yml",
            "const existing = open.find(i => !i.pull_request && i.body && i.body.includes(marker));",
            "const existing = open.find(i => i.body && i.body.includes(marker));",
        ),
    ]:
        path = WORKFLOWS / name
        scripts = [s for s in github_script_bodies(path) if fixed in s]
        # If this fails the guard is testing nothing: the anchor moved, so assert
        # it is present BEFORE substituting (a refactor must fail loudly here).
        assert scripts, f"anchor not found in {name}: {fixed!r}"
        for script in scripts:
            assert not scan_script(script), (name, scan_script(script))
            mutated = script.replace(fixed, broken)
            assert mutated != script, name
            assert scan_script(mutated), f"the guard does not flag the pre-fix {name}"
            checked += 1
    assert checked >= 2, checked


def test_a_reporting_only_listing_is_not_flagged():
    """The guard is about the CLOSE path; 739-style reporting is out of scope."""
    script = (
        "const open = await github.paginate(github.rest.issues.listForRepo, "
        "{ owner, repo, state: 'open' });\n"
        "const existing = open.find((i) => i.body && i.body.includes(MARKER));\n"
        "core.notice(`open: ${open.length}`);"
    )
    assert scan_script(script) == []


def test_a_guarded_inline_selection_passes():
    script = _synthetic(
        "const existing = open.find(i => !i.pull_request && i.body && i.body.includes(marker));"
    )
    assert scan_script(script) == []


def test_a_library_lookup_passes():
    assert scan_script(_synthetic("const existing = lib.findRollingIssue(open);")) == []


def test_a_lost_selection_is_reported_rather_than_passing_silently():
    """If the marker match moves somewhere this scan cannot see, say so.

    Failing open here would make every future copy invisible — the exact shape
    of the bug this file guards.
    """
    script = (
        "const open = await github.paginate(github.rest.issues.listForRepo, "
        "{ owner, repo, state: 'open' });\n"
        "const existing = pickSomehow(open);\n" + CLOSE_TAIL
    )
    reasons = scan_script(script)
    assert reasons and "no marker lookup is visible" in reasons[0], reasons


def test_unparseable_yaml_is_not_silently_skipped():
    """Every shipped workflow must parse; a scan over 0 files proves nothing."""
    parsed = [p for p in sorted(WORKFLOWS.glob("*.yml")) if github_script_bodies(p) is not None]
    assert len(parsed) > 50, len(parsed)


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
