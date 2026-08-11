"""Unit tests for 306's two dispatch-input call sites (#1080 burn-down).

306 scans the org-owned Microsoft 365 shared mailboxes through Graph and emits a
PII-masked pipeline of charity leads. Its two free-text dispatch inputs,
`mailboxes` and `since_days`, used to be interpolated into the one pwsh body
that calls the scanner. Both now arrive through step-level `env:`.

WHERE THE CREDENTIAL SAT — INVISIBLE TO BOTH RECOMMENDED SWEEPS
    Ledger L213 says to read the step's own `env:` before its `run:`, because
    that is what an injected payload reaches without moving; #1141's review
    generalized it into "sweep the file for `secrets.` in a `run:` body". 306
    defeats both, and not by being subtle:

        - name: Acquire Microsoft Graph token
          run: |
            $token = (az account get-access-token … -o tsv)
            "GRAPH_ACCESS_TOKEN=$token" | Out-File -FilePath $env:GITHUB_ENV -Append

    A live Graph bearer token, minted from the OIDC login by the PRECEDING step
    and exported through `GITHUB_ENV`, so it is in the process environment of
    the injection point while appearing in no `env:` block anywhere in the file
    and matching no `secrets.` reference. This is the same blindness recorded
    for 110 and for 301's second site — but here it is the ONLY credential in
    the workflow, so a reviewer working from either sweep scores the file as
    holding nothing at all. `test_the_graph_token_reaches_the_step_only_through_github_env`
    pins that, because it is the reason this entry was worth taking.

THE PAYLOAD SHAPE IS NOT THE ONE THE EARLIER LANES USED, AND THE WRONG ONE
"PROVES" THERE WAS NO DEFECT
    Every lane before this one interpolated into a DOUBLE-quoted pwsh string,
    where `$( )` expands without any quote break-out. 306 used SINGLE quotes:

        -Mailboxes '${{ github.event.inputs.mailboxes }}' `

    pwsh does not expand anything inside single quotes, so the `$( )` payload
    that worked everywhere else is inert here — measured, it reaches the callee
    as the literal text `contact@freeforcharity.org$(Set-Content -Path `. A
    control built that way reports *the pre-fix body did not execute the
    payload*, which reads as evidence the interpolation was harmless, i.e. as a
    reason not to fix it. That is the flattering direction and nobody re-checks
    it. `test_the_subexpression_payload_is_inert_in_single_quotes` keeps that
    fact as an assertion rather than a footnote, and the real control uses a
    quote break-out that closes the string, steals, and then RE-ISSUES the
    legitimate call so the trailing continuation lines become its arguments —
    which is why the exploited run still exits 0 and logs a normal-looking call.

    Measured against the body as it shipped, `mailboxes` =
    `contact@freeforcharity.org'; Set-Content -Path <sentinel> -Value
    $env:GRAPH_ACCESS_TOKEN; pwsh -NoProfile -File scripts/… -Mailboxes
    'contact@freeforcharity.org`:

        * the Graph bearer token was written to the sentinel file;
        * the callee was then invoked with `Mailboxes=[contact@freeforcharity.org]`
          and every other argument intact;
        * the step exited **0**, under `m365-prod`, after the approval.

A DEFAULT IS NOT A CONSTRAINT
    Both inputs declare a `default:`, which is the thing that makes this entry
    read as safe on a skim — the dispatch form arrives pre-filled with a legal
    value. GitHub constrains `boolean` and `choice` and nothing else: the
    dispatcher may replace either box with any text at all, and neither input
    declares a type, so both default to `string`.
    `test_both_inputs_are_free_text` pins the declarations against the guard's
    own constrained set rather than restating it.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete an `env:` mapping and nothing is interpolated, so the checker
    is honestly green over a step that can no longer receive its input. Only a
    per-step assertion on the wiring notices, which is `_assert_wiring`.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow  # noqa: E402

import importlib.util  # noqa: E402

_GUARD_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "check-workflow-input-interpolation.py"
)
_spec = importlib.util.spec_from_file_location("interp_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "306-discover-uncaptured-comms.yml"
JOB = "discover"
STEP = "Discover uncaptured comms"
ENVIRONMENT = "m365-prod"

# The env vars the inputs must travel in, and the expressions they map from.
# 306 spells its inputs `github.event.inputs.*`, not `inputs.*` — the guard
# matches both, and so does this module, because the mapping must keep whichever
# spelling the file actually uses.
MAPPINGS = {
    "IN_MAILBOXES": "${{ github.event.inputs.mailboxes }}",
    "IN_SINCE_DAYS": "${{ github.event.inputs.since_days }}",
}
INPUT_NAMES = ("mailboxes", "since_days")

# The credential in reach of the injection point, and the step that puts it
# there. It is not a `secrets.*` value and not a step output — it is written to
# GITHUB_ENV by the preceding step, so it appears in no `env:` block at all.
TOKEN_VAR = "GRAPH_ACCESS_TOKEN"
TOKEN_STEP = "Acquire Microsoft Graph token"

# Deliberately NOT shaped like the credential it stands in for. A JWT-shaped
# literal (`eyJ…`) is treated as a token by secret scanners and by the tooling
# that renders CI output, so it comes back REDACTED — and the one place this
# value is ever printed is an assertion message on a failing run, which is the
# moment the reader needs to see whether the sentinel holds the credential or an
# empty string.
FAKE_TOKEN = "graph-bearer-placeholder-not-a-real-token"
SENTINEL = "STOLEN-306.txt"

LEGAL_MAILBOXES = "contact@freeforcharity.org"
LEGAL_SINCE_DAYS = "365"

# Every variable the harness must own outright. Anything inherited from the
# developer's shell could satisfy an assertion the workflow is supposed to.
CONTROLLED_VARS = ("IN_MAILBOXES", "IN_SINCE_DAYS", TOKEN_VAR)

# The body as it shipped BEFORE the burn-down, verbatim from origin/main, with
# the two substitution points marked. It is the positive control: without it,
# "the fixed body does not execute the payload" is a claim about a body that
# might never have executed anything.
PRE_FIX_BODY = """$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path artifacts/discovery | Out-Null
pwsh -NoProfile -File scripts/discover-uncaptured-comms.ps1 `
  -Mailboxes 'MAILBOXES_HERE' `
  -SinceDays 'SINCE_HERE' `
  -SitesListPath 'sites-list/sites_list.json' `
  -OutputFile 'artifacts/discovery/pipeline.csv'
if ($LASTEXITCODE -ne 0) { throw "Discovery failed (exit $LASTEXITCODE)." }
"""

# A permissive stand-in for the real script: it records what it was BOUND, which
# is the only discriminator that works here. A marker-string search over stdout
# is not one — pwsh echoes the offending source line back in a ParserError, so
# any substring predicate matches the payload text on a run that executed
# nothing.
STUB = """[CmdletBinding()]
param(
    [string]$AccessToken,
    [string]$Mailboxes,
    [string]$SinceDays,
    [string]$SitesListPath,
    [string]$OutputFile
)
Write-Output "CALLED Mailboxes=[$Mailboxes] SinceDays=[$SinceDays] Out=[$OutputFile]"
"""

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that
# omits it pins the wrong exit code and vouches for it (recorded on #1080's
# lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)


def _breakout_payload() -> str:
    """A payload valid in the position it lands in: a SINGLE-quoted argument.

    It closes the string, steals the credential, then re-issues the legitimate
    invocation so the body's remaining continuation lines bind to it — which is
    what keeps the exploited run at exit 0 with a normal-looking call in the log.
    """
    return (
        LEGAL_MAILBOXES + "'; "
        "Set-Content -Path '" + SENTINEL + "' -Value $env:" + TOKEN_VAR + "; "
        "pwsh -NoProfile -File scripts/discover-uncaptured-comms.ps1 "
        "-Mailboxes '" + LEGAL_MAILBOXES
    )


def _subexpression_payload() -> str:
    """The payload every EARLIER lane used: `$( )`, which needs double quotes."""
    return (
        LEGAL_MAILBOXES
        + "$(Set-Content -Path '"
        + SENTINEL
        + "' -Value $env:"
        + TOKEN_VAR
        + ")"
    )


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input this body reaches through a `${{ }}` expression.

    Deliberately the CHECKER's own two patterns rather than a substring test of
    this module's own devising. `306` spells its inputs `github.event.inputs.*`,
    and a plain `"inputs.mailboxes" in body` does catch that — but only because
    one spelling happens to contain the other, which is a coincidence a later
    edit can remove without noticing. It does NOT catch the spellings the
    expression language also allows and the checker also matches, e.g.
    `${{ inputs . mailboxes }}`. Reusing `_INPUT_REF` means a spelling the
    checker recognises cannot slip past the step-level assertion, and the two
    cannot drift apart the way a restated rule does.
    """
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    return found


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


def _assert_wiring(step: dict) -> None:
    """Both inputs travel in env, the body reads them, and neither is interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES these variables (ledger L199): delete the
    workflow's `env:` mapping and the step still sees them from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists — while a real dispatch would call the scanner with no -Mailboxes.
    """
    env = step.get("env") or {}
    body = step.get("run", "")
    for var, expression in MAPPINGS.items():
        assert env.get(var) == expression, (
            f"step {step.get('name')!r} in job {JOB!r} must map {var} to "
            f"{expression} — its env: mapping is {env!r}"
        )
        assert f"$env:{var}" in body, (
            f"step {step.get('name')!r} maps {var} but never reads $env:{var} — "
            f"the env: block is decoration and the value reaches nothing. "
            f"Body: {body!r}"
        )
    reintroduced = _interpolated_inputs(body) & set(INPUT_NAMES)
    assert not reintroduced, (
        f"step {step.get('name')!r} interpolates {sorted(reintroduced)} into its "
        f"script body again (#1080): under {ENVIRONMENT} that is dispatcher text "
        f"executed after the approval. Body: {body!r}"
    )


def _run(body: str, wrap: bool = True, **env_overrides: str):
    """Run a pwsh body the way the RUNNER runs it, in a temp cwd holding the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS,
    not merely its existence: a file written from an unset variable would score
    the same as one written from the live credential, and the claim under test
    is which credential the payload reached.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "discover-uncaptured-comms.ps1").write_text(
            STUB, encoding="utf-8"
        )
        script = tmp / "step.ps1"
        script.write_text(
            (RUNNER_PREAMBLE + body + RUNNER_EPILOGUE) if wrap else body,
            encoding="utf-8",
        )
        env = child_env(**env_overrides)
        # Only what the test sets may be visible: an inherited IN_MAILBOXES would
        # make the fail-closed tests pass for the wrong reason, and an inherited
        # token would let a theft assertion pass without the workflow supplying
        # anything.
        for var in CONTROLLED_VARS:
            if var not in env_overrides:
                env.pop(var, None)
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        stolen = tmp / SENTINEL
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return proc.stdout + proc.stderr, contents, proc.returncode


def _pre_fix(mailboxes: str, since_days: str = LEGAL_SINCE_DAYS) -> str:
    """The pre-fix body with GitHub's substitution performed, as GitHub does it.

    Asserted rather than assumed: a `.replace` that stopped matching would leave
    the marker in place and the control would measure an unexploited body while
    reporting that the payload did not run (ledger L47).
    """
    assert PRE_FIX_BODY.count("MAILBOXES_HERE") == 1
    assert PRE_FIX_BODY.count("SINCE_HERE") == 1
    rendered = PRE_FIX_BODY.replace("MAILBOXES_HERE", mailboxes).replace(
        "SINCE_HERE", since_days
    )
    assert "_HERE" not in rendered, f"substitution left a marker behind: {rendered!r}"
    return rendered


def _strip_guards(body: str) -> str:
    """Remove both emptiness checks, asserting they were there.

    Anchored on the smallest span unique to the guards, and the occurrence count
    is asserted BEFORE substituting (ledger L47): an anchor that stopped
    matching must fail loudly rather than silently leave the body unchanged and
    score the control as a pass.
    """
    assert body.count("if ([string]::IsNullOrWhiteSpace($env:") == 2, (
        "expected exactly two emptiness guards to strip, found "
        f"{body.count('if ([string]::IsNullOrWhiteSpace($env:')} — this control "
        f"would otherwise test an unmodified body. Body: {body!r}"
    )
    start = body.index("if ([string]::IsNullOrWhiteSpace")
    last_exit = body.index("exit 1", body.index("IN_SINCE_DAYS)", start))
    end = body.index("}", last_exit) + 1
    stripped = body[:start] + body[end:]
    assert "IsNullOrWhiteSpace($env:" not in stripped, (
        f"a guard survived the strip, so the control is measuring the guarded "
        f"body. Stripped: {stripped!r}"
    )
    # The call under test must still be there — a strip that removed it would
    # also produce "no CALLED line" and pass for the wrong reason.
    assert "discover-uncaptured-comms.ps1" in stripped, (
        f"the strip removed the invocation itself, so the control proves "
        f"nothing. Stripped: {stripped!r}"
    )
    return stripped


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_both_inputs_are_wired_through_env():
    _assert_wiring(_step())


def test_the_step_sits_in_the_gated_m365_job():
    """This entry is a WRITE lane in the #1080 freeze because of `m365-prod`.

    If the job ever loses its environment, an injected payload no longer runs
    after an approval and the reasoning in this module's docstring stops being
    the reason the fix mattered — which is worth failing over rather than
    silently keeping.
    """
    job = load_workflow(WORKFLOW)["jobs"][JOB]
    assert job.get("environment") == ENVIRONMENT, (
        f"job {JOB!r} declares environment {job.get('environment')!r}, expected "
        f"{ENVIRONMENT!r}"
    )


def test_both_inputs_are_free_text():
    """A `default:` is not a constraint, and this is what makes them findings.

    Asserted against the guard's OWN constrained set rather than restating it,
    so the two cannot quietly disagree if GitHub ever constrains another type.
    """
    declared = guard.dispatch_inputs(load_workflow(WORKFLOW))
    for name in INPUT_NAMES:
        assert name in declared, f"input {name!r} is gone from the dispatch form: {declared}"
        assert declared[name] not in guard.CONSTRAINED_TYPES, (
            f"input {name!r} now declares type {declared[name]!r}, which GitHub "
            f"constrains — the freeze entry's premise has changed"
        )
    assert set(guard.free_text_inputs(load_workflow(WORKFLOW))) >= set(INPUT_NAMES)


def test_the_graph_token_reaches_the_step_only_through_github_env():
    """This module's central claim, pinned rather than left as prose.

    The credential in reach of the injection point is exported by the PRECEDING
    step through GITHUB_ENV. So a reviewer reading `env:` blocks (L213) and a
    sweep for `secrets.` in a `run:` body both score this file as holding
    nothing — while a payload here runs beside a live Graph bearer token.
    """
    workflow = load_workflow(WORKFLOW)
    steps = workflow["jobs"][JOB]["steps"]
    token_step = find_step(workflow, JOB, TOKEN_STEP)
    assert "GITHUB_ENV" in token_step["run"] and TOKEN_VAR in token_step["run"], (
        f"the {TOKEN_STEP!r} step no longer exports {TOKEN_VAR} through "
        f"GITHUB_ENV: {token_step['run']!r}"
    )
    assert steps.index(token_step) < steps.index(_step()), (
        "the token export no longer precedes the injection point, so the "
        "credential is not in that step's environment — re-read this module's "
        "docstring before trusting it"
    )
    for step in steps:
        assert TOKEN_VAR not in (step.get("env") or {}), (
            f"{TOKEN_VAR} now appears in step {step.get('name')!r}'s env: block. "
            f"That is a better state, not a worse one — but this module's claim "
            f"is that it did NOT, so the docstring needs re-reading"
        )
    assert TOKEN_VAR not in (workflow["jobs"][JOB].get("env") or {})


# --------------------------------------------------------------------------
# Behaviour: the defect, and its absence
# --------------------------------------------------------------------------


def test_the_pre_fix_body_stole_the_graph_token_and_exited_zero():
    """The positive control: the defect was real, silent, and gated behind nothing.

    Without this, every assertion below is a claim about a body that might never
    have executed anything at all.
    """
    out, stolen, rc = _run(
        _pre_fix(_breakout_payload()), **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert stolen is not None, (
        f"the pre-fix body did not execute the payload, so this control proves "
        f"nothing about what the fix prevents. Output: {out}"
    )
    assert stolen.strip() == FAKE_TOKEN, (
        f"the sentinel holds {stolen.strip()!r}, not the Graph token — the "
        f"payload ran but reached something else, and the claim under test is "
        f"WHICH credential it reached"
    )
    assert f"CALLED Mailboxes=[{LEGAL_MAILBOXES}]" in out, (
        f"the exploited run did not go on to call the scanner with a legal "
        f"mailbox list, so it would not have looked like a normal run: {out}"
    )
    assert rc == 0, (
        f"the exploited run exited {rc}, not 0 — the whole point of this control "
        f"is that the theft is invisible in the run's outcome: {out}"
    )


def test_the_subexpression_payload_is_inert_in_single_quotes():
    """The control that would have "proved" there was no defect here.

    306 interpolated into SINGLE quotes, so the `$( )` payload that exploited
    every earlier lane does not expand. A module that reached for the familiar
    payload would report the pre-fix body as harmless — the flattering
    direction, and a reason not to fix a live injection.
    """
    out, stolen, rc = _run(
        _pre_fix(_subexpression_payload()), **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert stolen is None, (
        "the `$( )` payload executed inside single quotes, which contradicts "
        "this module's stated reason for using a quote break-out instead — "
        f"re-read the docstring. Output: {out}"
    )
    assert "$(Set-Content" in out, (
        f"the subexpression did not reach the callee as literal text either, so "
        f"this test is no longer measuring the inertness it claims: {out}"
    )
    assert rc == 0, f"expected the inert run to exit 0, got {rc}: {out}"


def test_the_shipped_body_binds_the_payload_as_data():
    """The same payload, through `env:`: it arrives as an argument, verbatim."""
    step = _step()
    _assert_wiring(step)
    payload = _breakout_payload()
    out, stolen, rc = _run(
        step["run"],
        IN_MAILBOXES=payload,
        IN_SINCE_DAYS=LEGAL_SINCE_DAYS,
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert stolen is None, (
        f"the payload still executed: the sentinel holds {stolen!r}. The env: "
        f"mapping is not doing what #1080 requires of it. Output: {out}"
    )
    assert f"CALLED Mailboxes=[{payload}]" in out, (
        f"the payload did not arrive at the callee as one literal argument, so "
        f"it was neither executed nor bound as data — something else happened "
        f"and this test cannot tell what: {out}"
    )
    assert rc == 0, f"the shipped body exited {rc} on a legal-length input: {out}"


def test_the_shipped_body_still_passes_ordinary_inputs_through():
    """The fix must not have made the step inert (ledger L202)."""
    out, _, rc = _run(
        _step()["run"],
        IN_MAILBOXES=LEGAL_MAILBOXES,
        IN_SINCE_DAYS=LEGAL_SINCE_DAYS,
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert (
        f"CALLED Mailboxes=[{LEGAL_MAILBOXES}] SinceDays=[{LEGAL_SINCE_DAYS}]" in out
    ), f"the ordinary path no longer reaches the scanner with its inputs: {out}"
    assert rc == 0, f"the ordinary path exited {rc}: {out}"


# --------------------------------------------------------------------------
# Fail-closed, and the control that says what the guard is actually for
# --------------------------------------------------------------------------


def test_an_empty_mapping_fails_closed_and_says_which_one():
    """rc AND text, per CLAUDE.md: `rc == 1` alone cannot tell a refusal from a
    harness that could not start."""
    for var, other in (
        ("IN_MAILBOXES", "IN_SINCE_DAYS"),
        ("IN_SINCE_DAYS", "IN_MAILBOXES"),
    ):
        legal = LEGAL_MAILBOXES if other == "IN_MAILBOXES" else LEGAL_SINCE_DAYS
        out, _, rc = _run(
            _step()["run"],
            **{var: "   ", other: legal, TOKEN_VAR: FAKE_TOKEN},
        )
        assert rc == 1, f"an empty {var} exited {rc}, expected 1: {out}"
        assert f"::error::{var} is empty" in out, (
            f"the step exited 1 with an empty {var} but did not say so — "
            f"an exit code alone does not distinguish a refusal from a broken "
            f"harness. Output: {out}"
        )
        assert "CALLED Mailboxes=" not in out, (
            f"the scanner was invoked despite an empty {var}: {out}"
        )


def test_an_unset_mapping_fails_closed_and_says_which_one():
    out, _, rc = _run(_step()["run"], **{TOKEN_VAR: FAKE_TOKEN})
    assert rc == 1, f"unset mappings exited {rc}, expected 1: {out}"
    assert "::error::IN_MAILBOXES is empty" in out, (
        f"the step exited 1 with no mappings at all but named nothing: {out}"
    )
    assert "CALLED Mailboxes=" not in out, f"the scanner ran anyway: {out}"


def test_without_the_guard_empty_binds_and_unset_refuses():
    """Both cases controlled, because the guard is only needed for one of them.

    #1080's lane 9 review corrected the standing justification: an UNSET
    variable does not make pwsh shift the binding onto the next `-` token — the
    binder refuses it outright and the step already dies. It is the EMPTY case
    that binds silently and runs the scan against nothing. A control that
    strips the guard and re-runs only the empty case claims two behaviours and
    measures one; this asserts the asymmetry.
    """
    stripped = _strip_guards(_step()["run"])

    out, _, rc = _run(
        stripped, IN_MAILBOXES="", IN_SINCE_DAYS="", **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert rc == 0 and "CALLED Mailboxes=[]" in out, (
        f"with the guard removed, an EMPTY mapping was expected to bind and "
        f"reach the scanner silently (rc 0) — that is what the guard exists to "
        f"stop. Got rc={rc}: {out}"
    )

    out, _, rc = _run(stripped, **{TOKEN_VAR: FAKE_TOKEN})
    assert rc == 1, (
        f"with the guard removed, an UNSET mapping was expected to fail on the "
        f"binder's own refusal (rc 1). Got rc={rc}: {out}"
    )
    assert "Missing an argument for parameter 'Mailboxes'" in out, (
        f"the unset case failed for some reason other than the binder refusing "
        f"the next token, so the corrected model in this module's docstring is "
        f"not what was measured: {out}"
    )
    assert "CALLED Mailboxes=" not in out, (
        f"the scanner was reached with an unset -Mailboxes: {out}"
    )


# --------------------------------------------------------------------------
# The checker agrees
# --------------------------------------------------------------------------


def test_the_guard_no_longer_reports_this_workflow():
    findings, unreadable, _ = guard.scan_all()
    assert not unreadable, f"the guard could not read: {unreadable}"
    assert WORKFLOW not in guard.current_map(findings), (
        f"{WORKFLOW} still interpolates a free-text dispatch input into a "
        f"script body"
    )
    assert WORKFLOW not in guard.KNOWN_UNGUARDED, (
        f"{WORKFLOW} was burned down but is still listed in KNOWN_UNGUARDED — "
        f"a stale entry, which the guard itself exits 1 on"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    if shutil.which("pwsh") is None:
        print("  SKIP all (pwsh not installed in this environment; runs in CI)")
        sys.exit(0)
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
