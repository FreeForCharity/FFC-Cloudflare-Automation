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

Two limits worth stating rather than discovering. A pre-filter anywhere in the
script satisfies every selection in it, so a script that filters one listing and
then hand-rolls an unguarded match against a *different* listing would pass; and
the scan is line-based, so a `.find(` whose marker test wraps onto the next line
is not recognised as a selection. Both are accepted: the behavioural tests are
what actually hold the invariant, and a shape check that tried to parse
JavaScript would be a worse thing to maintain than the defect it prevents.

Per AGENTS.md §Merging, a guard that cannot be shown to fail is decoration, so
`test_the_scan_flags_the_pre_fix_form_of_a_real_workflow` reconstructs the
actual pre-#980 line in each shipped file and asserts the scan rejects it.

Refs #980, #979, #977, #752.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT, WORKFLOWS  # noqa: E402

# The six fixed in #980, plus 744 — the one that already selected safely AND
# closes, so it matches the shape this guard looks for — plus 739 since #1269.
# 737 is still deliberately absent: it enumerates issues to *report* and never
# closes one, so it does not match "lists and closes" and asserting it does would
# fail for the wrong reason.
#
# 739 was listed here as an example of that same exemption until #1269 gave its
# Conductor-silence verdict a rolling issue, which closes on recovery. Worth
# noting how that reached this file: the exemption was stated as a fact about
# what 739 does, so when what 739 does changed, the comment became false and the
# fail-closed branch in `scan_script` is what said so — the guard reported the
# selection it could no longer see rather than passing. An exemption keyed to a
# condition the checker still evaluates (L181) behaves exactly like this.
#
# Named so a removal is visible: if one of these stops matching the shape, the
# coverage assertion below fails rather than the file silently dropping out.
KNOWN_ROLLING_MONITORS = {
    "228-whmcs-fraud-review.yml",
    "321-azure-kv-credential-liveness.yml",
    "738-fleet-smoke-engine-drift-audit.yml",
    "739-process-health-metrics.yml",
    "740-scheduled-workflow-failure-alert.yml",
    "741-fleet-security-audit-coverage.yml",
    "743-fleet-security-header-audit.yml",
    "744-repo-public-feed-freshness.yml",
}

# Library helpers a script may use INSTEAD of an inline filter. The scan cannot
# see inside them, so each name here is a claim — and
# `test_every_library_selector_actually_drops_prs` below reads every definition
# of it under `scripts/` and asserts it negates `.pull_request`. Before #1269
# this was a bare `"findRollingIssue(" not in script` substring test, which
# trusted the name alone; a second selector made that trust worth checking
# rather than widening.
LIBRARY_SELECTORS = ("findRollingIssue", "findSilenceIssue")

# A call to one of those, with an optional member prefix (`lib.findRollingIssue(`
# is how all six callers spell it today) and a left boundary so that a different
# identifier merely ENDING in the name — `myFindSilenceIssue(` — is not a match.
# An unqualified call is accepted too: rejecting one would be a fail-closed
# finding rather than a silent pass, but there is no reason to make a legitimate
# `const { findRollingIssue } = require(…)` into one.
SELECTOR_CALL = re.compile(
    r"(?:^|[^\w$.])(?:[A-Za-z_$][\w$]*\s*\.\s*)?(?:"
    + "|".join(LIBRARY_SELECTORS)
    + r")\s*\("
)


def closes_an_issue(script: str) -> bool:
    """Does this script close an issue it looked up? (the consequential half)"""
    return "issues.update" in script and (
        "state: 'closed'" in script or 'state: "closed"' in script
    )


def selects_from_a_listing(script: str) -> bool:
    return "issues.listForRepo" in script


# `!i.pull_request` / `!item.pull_request` — the NEGATED test. Matching a bare
# `pull_request` would accept `.find(i => i.pull_request && …)`, which selects
# pull requests exclusively: the defect inverted, not fixed.
DROPS_PRS = re.compile(r"!\s*[A-Za-z_$][\w$]*\.pull_request")


def _code_lines(script: str) -> list[str]:
    return [
        ln.strip() for ln in script.splitlines() if not ln.strip().startswith("//")
    ]


def scan_script(script: str) -> list[str]:
    """Reasons this script's rolling-issue lookup is unsafe. Empty == fine."""
    if not (selects_from_a_listing(script) and closes_an_issue(script)):
        return []
    lines = _code_lines(script)
    # A pre-filter on the listing is as safe as filtering inside the `.find(`,
    # and it is the form 737 already uses:
    #   (await listOpen(…)).filter((i) => !i.pull_request)
    # Rejecting it would push authors away from correct code, which is how a
    # guard earns a `continue-on-error` instead of a fix.
    prefiltered = any(".filter(" in ln and DROPS_PRS.search(ln) for ln in lines)
    # The selection is the `.find(...)` that matches the marker. Every one is
    # checked even when a library call is also present: a script can call
    # `lib.findRollingIssue(open)` in one place and still hand-roll a second,
    # unguarded match — which would reintroduce the defect while the library
    # call made the workflow look fixed. This is exactly what the per-workflow
    # tests assert with `"includes(lib.MARKER)" not in script`.
    selections = [ln for ln in lines if ".find(" in ln and ".includes(" in ln]
    reasons = [
        f"selects the rolling issue without dropping pull requests: {ln}"
        for ln in selections
        if not (DROPS_PRS.search(ln) or prefiltered)
    ]
    # A CALL to an allow-listed library selector is a selection too — one that is
    # safe by construction, since `test_every_library_selector_actually_drops_prs`
    # reads the helper and asserts it negates `.pull_request`. Matched as a call on
    # a comment-stripped line, not as a substring of the whole script: a mention in
    # prose ("we used to use findRollingIssue()") must not suppress the fail-closed
    # branch below, which is the one thing here that cannot be allowed to fail
    # open. Same line-based limits as the `.find(` scan above, deliberately — a
    # trailing comment or a string literal on a code line still counts, and making
    # this stricter than the selection scan would be a second, divergent idea of
    # what "code" means.
    library_selections = [ln for ln in lines if SELECTOR_CALL.search(ln)]
    if not selections and not library_selections:
        # Fail closed: if the marker match moved somewhere this scan cannot see,
        # say so. Failing open here would make every future copy invisible.
        reasons.append(
            "lists open issues and closes one, but no marker lookup is visible — "
            "if the selection moved, teach this guard where it went (#980)"
        )
    return reasons


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


def test_every_library_selector_actually_drops_prs():
    """A selector on the allow-list is a claim; this is what checks it.

    `scan_script` accepts `lib.findRollingIssue(open)` without seeing the filter,
    so the allow-list is the one place this guard trusts a NAME rather than the
    code in front of it. Every definition of each name is read — five libraries
    define `findRollingIssue` — because one unfiltered copy is enough to
    reintroduce the defect while every caller still looks fixed.
    """
    checked = 0
    for name in LIBRARY_SELECTORS:
        definitions = [
            (path, path.read_text(encoding="utf-8"))
            for path in sorted((REPO_ROOT / "scripts").glob("*.js"))
        ]
        defining = [(p, t) for p, t in definitions if f"function {name}(" in t]
        # Anchor first: a rename that moved the helper must fail loudly here
        # rather than leave a name on the allow-list that guards nothing.
        assert defining, f"no scripts/*.js defines {name}() — is the allow-list stale?"
        for path, text in defining:
            start = text.index(f"function {name}(")
            end = text.find("\n}", start)
            assert end != -1, f"could not delimit {name}() in {path.name}"
            assert DROPS_PRS.search(text[start:end]), (
                f"{path.name}:{name}() is on the selector allow-list but does not "
                "negate .pull_request"
            )
            checked += 1
    assert checked >= len(LIBRARY_SELECTORS), checked


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
        # 739 (#1269) exercises the library-selector path on shipped source: the
        # scan sees no inline filter at all here, so what must hold is that
        # replacing the helper with a hand-rolled match is rejected.
        (
            "739-process-health-metrics.yml",
            "const existing = lib.findSilenceIssue(open);",
            "const existing = open.find((i) => i.body.includes(lib.SILENCE_MARKER));",
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


def test_a_hand_rolled_match_is_flagged_even_when_a_library_call_is_present():
    """A library call must not launder a second, unguarded match beside it.

    Returning early on `findRollingIssue(` would let exactly this through, while
    the library call made the workflow look fixed — the same "looks fixed" shape
    the per-workflow tests block with `"includes(lib.MARKER)" not in script`.
    Raised by Copilot on #982.
    """
    script = _synthetic(
        "const existing = lib.findRollingIssue(open);\n"
        "const legacy = open.find((i) => i.body && i.body.includes(lib.MARKER));"
    )
    reasons = scan_script(script)
    assert reasons and "legacy" in reasons[0], reasons


def test_the_pre_filter_form_used_by_737_is_accepted():
    """Filtering the listing is as safe as filtering inside the `.find(`.

    737 already ships this form. Rejecting correct code is how a guard earns a
    `continue-on-error` rather than a fix. Raised by Copilot on #982.
    """
    script = (
        "const open = (await github.paginate(github.rest.issues.listForRepo, "
        "{ owner, repo, state: 'open' })).filter((i) => !i.pull_request);\n"
        "const existing = open.find((i) => i.body && i.body.includes(lib.MARKER));\n"
        + CLOSE_TAIL
    )
    assert scan_script(script) == []


def test_a_filter_that_keeps_only_pull_requests_is_still_flagged():
    """`i.pull_request` is the defect inverted; only the NEGATED test is safe.

    A substring check for `pull_request` would accept both, so the guard would
    bless a script that closes nothing but pull requests.
    """
    for selection in [
        "const existing = open.find(i => i.pull_request && i.body.includes(marker));",
        "const bad = open.filter((i) => i.pull_request);\n"
        "const existing = open.find(i => i.body && i.body.includes(marker));",
    ]:
        assert scan_script(_synthetic(selection)), selection


def test_a_mentioned_library_selector_does_not_satisfy_the_guard():
    """Copilot's finding on #1275: a NAME is not a call.

    The allow-list check was a substring test over the whole script, so a comment
    naming the helper suppressed the fail-closed branch — and that branch is the
    one thing in this file that must never fail open, since it is what makes a
    future copy of the pattern visible at all.
    """
    for mention in [
        "// we used to call lib.findRollingIssue(open) here",
        "// see findSilenceIssue() in process-health-metrics-lib.js",
        "// TODO: switch to findRollingIssue(open)",
    ]:
        script = (
            "const open = await github.paginate(github.rest.issues.listForRepo, "
            "{ owner, repo, state: 'open' });\n"
            + mention
            + "\nconst existing = pickSomehow(open);\n"
            + CLOSE_TAIL
        )
        assert scan_script(script), mention


def test_an_identifier_merely_ending_in_a_selector_name_is_not_a_call():
    script = (
        "const open = await github.paginate(github.rest.issues.listForRepo, "
        "{ owner, repo, state: 'open' });\n"
        "const existing = myFindSilenceIssue(open);\n" + CLOSE_TAIL
    )
    assert scan_script(script), "a lookalike identifier must not satisfy the allow-list"


def test_a_real_library_selector_call_satisfies_the_guard():
    """Both the member form every caller uses and a destructured bare call."""
    for call in [
        "const existing = lib.findSilenceIssue(open);",
        "const existing = lib.findRollingIssue(open);",
        "const existing = findRollingIssue(open);",
        "const existing = lib . findSilenceIssue ( open );",
    ]:
        script = (
            "const open = await github.paginate(github.rest.issues.listForRepo, "
            "{ owner, repo, state: 'open' });\n" + call + "\n" + CLOSE_TAIL
        )
        assert scan_script(script) == [], call


def test_a_library_call_does_not_excuse_a_second_hand_rolled_match():
    """The two checks are independent: one safe selection does not bless another.

    A script can call the helper in one place and hand-roll an unguarded match in
    another, which would reintroduce the defect while the library call made the
    workflow look fixed.
    """
    script = (
        "const open = await github.paginate(github.rest.issues.listForRepo, "
        "{ owner, repo, state: 'open' });\n"
        "const existing = lib.findSilenceIssue(open);\n"
        "const other = open.find((i) => i.body && i.body.includes(marker));\n" + CLOSE_TAIL
    )
    assert scan_script(script), "an unguarded second match must still be reported"


def test_a_commented_out_filter_does_not_satisfy_the_guard():
    script = _synthetic(
        "// const open2 = open.filter((i) => !i.pull_request);\n"
        "const existing = open.find(i => i.body && i.body.includes(marker));"
    )
    assert scan_script(script)


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
