"""Unit tests for 205's two dispatch-input call sites (#1080 burn-down).

205 opens a WHMCS support ticket under the gated `whmcs-prod` environment. Two of
its free-text dispatch inputs, `deptid` and `client_id`, used to be interpolated
into the one pwsh body that builds the argument list for `whmcs-ticket-open.ps1`.
Both now arrive through step-level `env:` (TICKET_DEPTID / TICKET_CLIENT_ID).

WHERE THE CREDENTIAL SAT — INVISIBLE TO BOTH RECOMMENDED SWEEPS
    The WHMCS API credential (`WHMCS_API_IDENTIFIER` / `WHMCS_API_SECRET` /
    `WHMCS_APIM_SUBSCRIPTION_KEY`) is minted by the PRECEDING step,
    `uses: ./.github/actions/whmcs-secrets-from-kv`, which exports it through
    GITHUB_ENV. So it is in the process environment of the injection point while
    appearing in no `env:` block anywhere in the file and matching no `secrets.`
    reference. A reviewer reading the step's own `env:` (ledger L213) and a sweep
    for `secrets.` in a `run:` body (#1141) both score this file as holding
    nothing — and it is the workflow's ONLY credential, exactly the surface
    #1080's lanes 7, 9 and 10 each rediscovered by hand.
    `test_the_whmcs_credential_reaches_the_step_only_through_the_kv_action` pins
    that, because it is the reason this entry was worth taking.

THE INJECTION POINT SAT IN SINGLE QUOTES, AND THE WRONG PAYLOAD "PROVES" SAFETY
    `deptid` interpolated as a SINGLE-quoted array element:

        '-DeptId', '${{ inputs.deptid }}',

    pwsh does not expand `$( )` inside single quotes, so the subexpression payload
    that exploited the earlier double-quoted lanes is inert here — measured, it
    reaches the callee as the literal text `1$(Set-Content …)`. A control built
    that way reports *the pre-fix body did not execute the payload*, which reads
    as evidence the interpolation was harmless — the flattering direction, and a
    reason not to fix a live injection under `whmcs-prod`.
    `test_the_subexpression_payload_is_inert_in_single_quotes` keeps that fact as
    an assertion rather than a footnote. The real control CLOSES the single-quoted
    string, steals, then re-opens a fresh `$cliArgs = @('-DeptId', '1` so the
    body's trailing continuation lines bind to it — which is why the exploited run
    still exits 0 with a normal-looking call in the log.

    Measured against the body as it shipped, `deptid` =
    `1'); $null = Set-Content -Path <sentinel> -Value $env:WHMCS_API_SECRET;
    $cliArgs = @('-DeptId', '1`:

        * the WHMCS API secret was written to the sentinel file;
        * the callee was then invoked with `DeptId=[1]` and the rest intact;
        * the step exited **0**, under `whmcs-prod`, after the approval.

A DEFAULT IS NOT A CONSTRAINT
    `deptid` declares `default: '1'`, which is what makes it read as safe on a
    skim — the dispatch form arrives pre-filled with a legal value. GitHub
    constrains `boolean` and `choice` and nothing else: both `deptid` and
    `client_id` declare `type: string`, so the dispatcher may replace either box
    with any text at all. `test_both_inputs_are_free_text` pins that against the
    guard's own constrained set rather than restating it. `priority` (choice) and
    `dry_run` (boolean) stay interpolated and are NOT findings.

THE ARITY HAZARD, AND WHICH BLANK ACTUALLY REACHES IT
    Moving `deptid` into `env:` manufactures the #1150 / L214 hazard: `& pwsh
    -File … @cliArgs` is a NATIVE command, so a NULL `$env:TICKET_DEPTID` is not an
    empty argument — it is NO argument, and every later element shifts one position
    left. The two blanks behave differently, and only one is the guard's real job
    (measured, all downstream params typed `[string]` in the stub so the story is
    about arity not coercion):

        * An UNSET mapping (a DELETED or misspelled `env:` line — the case the
          original comment named) makes `$env:TICKET_DEPTID` NULL, drops it from
          the splat, and the binder refuses the next token: `Missing an argument
          for parameter 'DeptId'`, rc 1. The step already dies.
        * An EMPTY-STRING mapping (GitHub always SETS the var, blank if the
          dispatcher blanks the box) binds as an empty element and reaches the
          scanner SILENTLY: `CALLED DeptId=[]`, rc 0. That is the silent case, and
          the one the fail-closed guard exists to stop.

    Same asymmetry 306 measured, for the same reason. `client_id` / `client_email`
    are optional, so they are gated rather than fatal -- but by the SAME
    `IsNullOrWhiteSpace` test the three required inputs use. The `-ne ''` gate they
    carried until #1207 admitted exactly the two blanks the required guards refuse,
    measured with pwsh 7.4.6:

        * UNSET (mapping deleted or misspelled) -- `$null -ne ''` is TRUE, so the
          branch is entered and `-ClientId` reaches the binder with no value after
          it. It does fail there, non-zero, but on arity rather than on a named
          cause, and only because the dropped pair is LAST in the array: there is no
          following token for `-ClientId` to swallow. Move the append and the same
          spelling becomes the silent shift the required guards exist to stop.
        * WHITESPACE (`client_id: "   "`) -- `'   ' -ne ''` is TRUE, so whitespace
          reaches the callee as a client id. `IsNullOrWhiteSpace` refuses it.

    The runner's own empty string (`''`, what a blanked dispatch box produces) was
    handled correctly by both spellings -- which is why this was a latent defect and
    not a live one, and why the shipped path did not change behaviour.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE. It
    is a detector defined over the defect's spelling, so it goes quiet the moment
    the spelling is gone — including when the REMEDY is gone too (ledger L202).
    Delete an `env:` mapping and nothing is interpolated, so the checker is
    honestly green over a step that can no longer receive its input. Only a
    per-step assertion on the wiring notices, which is `_assert_wiring`.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow  # noqa: E402

_GUARD_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "check-workflow-input-interpolation.py"
)
_spec = importlib.util.spec_from_file_location("interp_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "205-whmcs-ticket-open.yml"
JOB = "open_ticket"
STEP = "Open ticket"
ENVIRONMENT = "whmcs-prod"

# The env vars the moved inputs must travel in, and the expressions they map from.
MAPPINGS = {
    "TICKET_DEPTID": "${{ inputs.deptid }}",
    "TICKET_CLIENT_ID": "${{ inputs.client_id }}",
}
INPUT_NAMES = ("deptid", "client_id")

# The credential in reach of the injection point, and the step that puts it there.
# It is exported to GITHUB_ENV by the `whmcs-secrets-from-kv` composite action, so
# it appears in no `env:` block and matches no `secrets.*` reference in this file.
# The KV step is a bare `uses:` with no `name:`, so it is located by its `uses:`
# value rather than through find_step (which matches on name).
TOKEN_VAR = "WHMCS_API_SECRET"
TOKEN_STEP_USES = "./.github/actions/whmcs-secrets-from-kv"

# Deliberately NOT shaped like a real secret. A value that a scanner treats as a
# credential comes back REDACTED, and the one place this value is printed is an
# assertion message on a failing run — the moment the reader needs to see whether
# the sentinel holds the credential or an empty string.
FAKE_TOKEN = "whmcs-secret-placeholder-not-a-real-token"
SENTINEL = "STOLEN-205.txt"

LEGAL_DEPTID = "1"
LEGAL_SUBJECT = "Site down"
LEGAL_MESSAGE = "Please investigate"
LEGAL_CLIENT_ID = "419"

# Every variable the harness must own outright: an inherited one could satisfy an
# assertion the workflow is supposed to.
CONTROLLED_VARS = (
    "TICKET_DEPTID",
    "TICKET_SUBJECT",
    "TICKET_MESSAGE",
    "TICKET_EMAIL",
    "TICKET_CLIENT_ID",
    TOKEN_VAR,
)

# The body as it shipped BEFORE the burn-down, verbatim from origin/main, with the
# two substitution points marked. `priority` and `dry_run` are rendered to their
# constrained literals because GitHub would. It is the positive control: without
# it, "the fixed body does not execute the payload" is a claim about a body that
# might never have executed anything.
PRE_FIX_BODY = """$ErrorActionPreference = 'Stop'
$cliArgs = @(
  '-DeptId', 'DEPT_HERE',
  '-Subject', $env:TICKET_SUBJECT,
  '-Message', $env:TICKET_MESSAGE,
  '-Priority', 'Medium'
)
if ('CLIENT_HERE' -ne '') { $cliArgs += @('-ClientId', 'CLIENT_HERE') }
elseif ($env:TICKET_EMAIL -ne '') { $cliArgs += @('-Email', $env:TICKET_EMAIL, '-Name', $env:TICKET_EMAIL) }
if ('true' -ne 'false') { $cliArgs += '-DryRun' }

& pwsh -NoProfile -File .\\scripts\\whmcs-ticket-open.ps1 @cliArgs
if ($LASTEXITCODE -ne 0) { throw "Open ticket failed (exit $LASTEXITCODE)." }
"""

# A permissive stand-in for the real script: it records what it was BOUND, which is
# the only discriminator that works here. A marker-string search over stdout is not
# one — pwsh echoes the offending source line back in a ParserError, so any
# substring predicate matches the payload text on a run that executed nothing.
# Every parameter is [string] so the stub reports what it received rather than
# coercing (the real callee types `DeptId`/`ClientId` as `[int]`; the point here is
# what reached the boundary, not what it validates to).
STUB = """[CmdletBinding()]
param(
    [string]$DeptId,
    [string]$Subject,
    [string]$Message,
    [string]$ClientId,
    [string]$Name,
    [string]$Email,
    [string]$Priority,
    [switch]$DryRun
)
Write-Output "CALLED DeptId=[$DeptId] Subject=[$Subject] Message=[$Message] ClientId=[$ClientId] Email=[$Email]"
"""

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that omits
# it pins the wrong exit code and vouches for it (recorded on #1080's lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)


def _breakout_payload() -> str:
    """A payload valid in the position it lands in: a SINGLE-quoted array element.

    It closes the string and the `@(` array, steals the credential, then re-opens
    a fresh `$cliArgs = @('-DeptId', '1` so the body's remaining continuation lines
    bind to it — which is what keeps the exploited run at exit 0 with a
    normal-looking call in the log.
    """
    return (
        LEGAL_DEPTID + "'); "
        "$null = Set-Content -Path '" + SENTINEL + "' -Value $env:" + TOKEN_VAR + "; "
        "$cliArgs = @('-DeptId', '" + LEGAL_DEPTID
    )


def _subexpression_payload() -> str:
    """The payload every DOUBLE-quoted lane used: `$( )`, inert in single quotes.

    A bareword `-Path` (no inner quotes) so the demonstration is a clean literal
    rather than a quote-break parse error — the claim under test is that the
    subexpression does not EXECUTE here, not that it fails to parse.
    """
    return (
        LEGAL_DEPTID
        + "$(Set-Content -Path "
        + SENTINEL
        + " -Value $env:"
        + TOKEN_VAR
        + ")"
    )


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input this body reaches through a `${{ }}` expression.

    Deliberately the CHECKER's own two patterns rather than a substring test of
    this module's own devising, so a spelling the checker recognises
    (`${{ inputs . deptid }}`) cannot slip past the step-level assertion, and the
    two cannot drift apart the way a restated rule does.
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
    every behavioural test below keeps passing over plumbing that no longer exists.
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


def _run(body: str, **env_overrides: str):
    """Run a pwsh body the way the RUNNER runs it, in a temp cwd holding the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS, not
    merely its existence: a file written from an unset variable would score the
    same as one written from the live credential, and the claim under test is which
    credential the payload reached.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "whmcs-ticket-open.ps1").write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(
            RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8"
        )
        env = child_env(**env_overrides)
        # Only what the test sets may be visible: an inherited TICKET_* would make
        # the fail-closed tests pass for the wrong reason, and an inherited token
        # would let a theft assertion pass without the workflow supplying anything.
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


def _pre_fix(deptid: str, client_id: str = "") -> str:
    """The pre-fix body with GitHub's substitution performed, as GitHub does it.

    Asserted rather than assumed: a `.replace` that stopped matching would leave
    the marker in place and the control would measure an unexploited body while
    reporting that the payload did not run (ledger L47).
    """
    assert PRE_FIX_BODY.count("DEPT_HERE") == 1
    rendered = PRE_FIX_BODY.replace("DEPT_HERE", deptid).replace(
        "CLIENT_HERE", client_id
    )
    assert "_HERE" not in rendered, f"substitution left a marker behind: {rendered!r}"
    return rendered


def _strip_guards(body: str) -> str:
    """Remove all three emptiness checks, asserting they were there.

    Anchored on the span unique to the guards, and the occurrence count is asserted
    BEFORE substituting (ledger L47): an anchor that stopped matching must fail
    loudly rather than silently leave the body unchanged and score the control as a
    pass.
    """
    anchor = "if ([string]::IsNullOrWhiteSpace($env:"
    expected_optional_gates = body.count("-not [string]::IsNullOrWhiteSpace(")
    assert body.count(anchor) == 3, (
        f"expected exactly three emptiness guards to strip, found "
        f"{body.count(anchor)} — this control would otherwise test an unmodified "
        f"body. Body: {body!r}"
    )
    while anchor in body:
        start = body.index(anchor)
        end = body.index("}", body.index("exit 1", start)) + 1
        body = body[:start] + body[end:]
    # The optional `client_id` / `client_email` gates test emptiness with the SAME
    # call (negated), so "no IsNullOrWhiteSpace survives" would fail on a correct
    # strip. Derive the survivor count from the body rather than freezing it: a
    # fail-closed guard that survives still fires this, and adding or removing an
    # optional gate does not (ledger L247 -- a frozen count is the thing that goes
    # stale).
    assert body.count(anchor) == 0, (
        f"a fail-closed guard survived the strip, so the control is measuring the "
        f"guarded body. Stripped: {body!r}"
    )
    surviving = body.count("IsNullOrWhiteSpace(")
    assert surviving == expected_optional_gates, (
        f"expected exactly {expected_optional_gates} emptiness tests to survive the "
        f"strip (the optional gates), found {surviving}. Either a fail-closed guard "
        f"was respelled so the strip no longer reaches it, or an optional gate was "
        f"removed. Stripped: {body!r}"
    )
    assert "whmcs-ticket-open.ps1" in body, (
        f"the strip removed the invocation itself, so the control proves nothing. "
        f"Stripped: {body!r}"
    )
    return body


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_both_inputs_are_wired_through_env():
    _assert_wiring(_step())


def test_the_step_sits_in_the_gated_whmcs_job():
    """This entry is a WRITE lane in the #1080 freeze because of `whmcs-prod`.

    If the job ever loses its environment, an injected payload no longer runs after
    an approval and the reasoning in this module's docstring stops being the reason
    the fix mattered — worth failing over rather than silently keeping.
    """
    job = load_workflow(WORKFLOW)["jobs"][JOB]
    assert job.get("environment") == ENVIRONMENT, (
        f"job {JOB!r} declares environment {job.get('environment')!r}, expected "
        f"{ENVIRONMENT!r}"
    )


def test_both_inputs_are_free_text():
    """A `default:` is not a constraint, and this is what makes them findings.

    Asserted against the guard's OWN constrained set rather than restating it, so
    the two cannot quietly disagree if GitHub ever constrains another type.
    """
    declared = guard.dispatch_inputs(load_workflow(WORKFLOW))
    for name in INPUT_NAMES:
        assert name in declared, f"input {name!r} is gone from the dispatch form: {declared}"
        assert declared[name] not in guard.CONSTRAINED_TYPES, (
            f"input {name!r} now declares type {declared[name]!r}, which GitHub "
            f"constrains — the freeze entry's premise has changed"
        )
    assert set(guard.free_text_inputs(load_workflow(WORKFLOW))) >= set(INPUT_NAMES)


def test_the_whmcs_credential_reaches_the_step_only_through_the_kv_action():
    """This module's central claim, pinned rather than left as prose.

    The credential in reach of the injection point is exported by the PRECEDING
    `whmcs-secrets-from-kv` step through GITHUB_ENV. So a reviewer reading `env:`
    blocks (L213) and a sweep for `secrets.` in a `run:` body both score this file
    as holding nothing — while a payload here runs beside the live WHMCS secret.
    """
    workflow = load_workflow(WORKFLOW)
    steps = workflow["jobs"][JOB]["steps"]
    token_steps = [
        i for i, s in enumerate(steps) if str(s.get("uses", "")) == TOKEN_STEP_USES
    ]
    assert token_steps, (
        f"no step uses {TOKEN_STEP_USES} — the workflow no longer mints the WHMCS "
        f"credential via the KV action, so re-read this module's docstring"
    )
    assert token_steps[0] < steps.index(_step()), (
        "the whmcs-secrets-from-kv step no longer precedes the injection point, so "
        "the credential is not in that step's environment — re-read this module's "
        "docstring before trusting it"
    )
    # The credential is nowhere the two recommended sweeps can see it: not in any
    # step's env: block, not in the job's env:, and no secrets.* reference exports
    # it (the KV action does, through GITHUB_ENV).
    for name in ("WHMCS_API_SECRET", "WHMCS_API_IDENTIFIER", "WHMCS_APIM_SUBSCRIPTION_KEY"):
        for step in steps:
            assert name not in (step.get("env") or {}), (
                f"{name} now appears in step {step.get('name')!r}'s env: block. "
                f"That is a better state, not a worse one — but this module's claim "
                f"is that it did NOT, so the docstring needs re-reading"
            )
        assert name not in (workflow["jobs"][JOB].get("env") or {})
    for step in steps:
        assert "secrets." not in (step.get("run") or ""), (
            f"step {step.get('name')!r} now references secrets.* in its run body; a "
            f"grep-for-secrets sweep would find this file, contradicting the claim"
        )


# --------------------------------------------------------------------------
# Behaviour: the defect, and its absence
# --------------------------------------------------------------------------


def test_the_pre_fix_body_stole_the_whmcs_secret_and_exited_zero():
    """The positive control: the defect was real, silent, and gated behind nothing.

    Without this, every assertion below is a claim about a body that might never
    have executed anything at all.
    """
    out, stolen, rc = _run(
        _pre_fix(_breakout_payload()),
        TICKET_SUBJECT=LEGAL_SUBJECT,
        TICKET_MESSAGE=LEGAL_MESSAGE,
        TICKET_EMAIL="",
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert stolen is not None, (
        f"the pre-fix body did not execute the payload, so this control proves "
        f"nothing about what the fix prevents. Output: {out}"
    )
    assert stolen.strip() == FAKE_TOKEN, (
        f"the sentinel holds {stolen.strip()!r}, not the WHMCS secret — the payload "
        f"ran but reached something else, and the claim under test is WHICH "
        f"credential it reached"
    )
    assert f"CALLED DeptId=[{LEGAL_DEPTID}]" in out, (
        f"the exploited run did not go on to call the scanner with a legal deptid, "
        f"so it would not have looked like a normal run: {out}"
    )
    assert rc == 0, (
        f"the exploited run exited {rc}, not 0 — the whole point of this control is "
        f"that the theft is invisible in the run's outcome: {out}"
    )


def test_the_subexpression_payload_is_inert_in_single_quotes():
    """The control that would have "proved" there was no defect here.

    205 interpolated into SINGLE quotes, so the `$( )` payload that exploited every
    double-quoted lane does not expand. A module that reached for the familiar
    payload would report the pre-fix body as harmless — the flattering direction,
    and a reason not to fix a live injection.
    """
    out, stolen, rc = _run(
        _pre_fix(_subexpression_payload()),
        TICKET_SUBJECT=LEGAL_SUBJECT,
        TICKET_MESSAGE=LEGAL_MESSAGE,
        TICKET_EMAIL="",
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert stolen is None, (
        "the `$( )` payload executed inside single quotes, which contradicts this "
        f"module's stated reason for using a quote break-out instead — re-read the "
        f"docstring. Output: {out}"
    )
    assert "$(Set-Content" in out, (
        f"the subexpression did not reach the callee as literal text, so this test "
        f"is no longer measuring the inertness it claims: {out}"
    )
    assert rc == 0, f"expected the inert run to exit 0, got {rc}: {out}"


def test_the_shipped_body_binds_the_deptid_payload_as_data():
    """The same deptid payload, through `env:`: it arrives as an argument, verbatim."""
    step = _step()
    _assert_wiring(step)
    payload = _breakout_payload()
    out, stolen, rc = _run(
        step["run"],
        TICKET_DEPTID=payload,
        TICKET_SUBJECT=LEGAL_SUBJECT,
        TICKET_MESSAGE=LEGAL_MESSAGE,
        TICKET_EMAIL="",
        TICKET_CLIENT_ID="",
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert stolen is None, (
        f"the payload still executed: the sentinel holds {stolen!r}. The env: "
        f"mapping is not doing what #1080 requires of it. Output: {out}"
    )
    assert f"CALLED DeptId=[{payload}]" in out, (
        f"the payload did not arrive at the callee as one literal argument, so it "
        f"was neither executed nor bound as data — something else happened and this "
        f"test cannot tell what: {out}"
    )
    assert rc == 0, f"the shipped body exited {rc} on a legal-length input: {out}"


def test_the_shipped_body_binds_the_client_id_payload_as_data():
    """The optional site: a client_id payload arrives through its gated append as data."""
    step = _step()
    _assert_wiring(step)
    payload = "419'; $(Set-Content -Path " + SENTINEL + " -Value $env:" + TOKEN_VAR + ")"
    out, stolen, rc = _run(
        step["run"],
        TICKET_DEPTID=LEGAL_DEPTID,
        TICKET_SUBJECT=LEGAL_SUBJECT,
        TICKET_MESSAGE=LEGAL_MESSAGE,
        TICKET_EMAIL="",
        TICKET_CLIENT_ID=payload,
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert stolen is None, (
        f"the client_id payload executed: the sentinel holds {stolen!r}. Output: {out}"
    )
    assert f"CALLED DeptId=[{LEGAL_DEPTID}]" in out and f"ClientId=[{payload}]" in out, (
        f"the client_id payload did not arrive at the callee as one literal "
        f"argument alongside a normal deptid: {out}"
    )
    assert rc == 0, f"the shipped body exited {rc} on a legal deptid: {out}"


def test_the_shipped_body_still_passes_ordinary_inputs_through():
    """The fix must not have made the step inert (ledger L202)."""
    out, _, rc = _run(
        _step()["run"],
        TICKET_DEPTID=LEGAL_DEPTID,
        TICKET_SUBJECT=LEGAL_SUBJECT,
        TICKET_MESSAGE=LEGAL_MESSAGE,
        TICKET_EMAIL="",
        TICKET_CLIENT_ID=LEGAL_CLIENT_ID,
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert f"CALLED DeptId=[{LEGAL_DEPTID}]" in out, (
        f"the ordinary path no longer reaches the scanner with its deptid: {out}"
    )
    assert f"ClientId=[{LEGAL_CLIENT_ID}]" in out, (
        f"the ordinary path no longer appends the client id: {out}"
    )
    assert rc == 0, f"the ordinary path exited {rc}: {out}"


def test_a_whitespace_client_id_is_not_appended():
    """#1207 review: `-ne ''` admitted whitespace, `IsNullOrWhiteSpace` refuses it.

    `client_id` is optional, so the refusal is a silent skip, not a failure -- the
    step must still run and simply not carry a blank id to the callee.
    """
    out, _, rc = _run(
        _step()["run"],
        TICKET_DEPTID=LEGAL_DEPTID,
        TICKET_SUBJECT=LEGAL_SUBJECT,
        TICKET_MESSAGE=LEGAL_MESSAGE,
        TICKET_EMAIL="",
        TICKET_CLIENT_ID="   ",
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert "ClientId=[]" in out, (
        f"a whitespace-only client_id reached the callee: {out}"
    )
    assert f"CALLED DeptId=[{LEGAL_DEPTID}]" in out and rc == 0, (
        f"refusing the blank client id also broke the ordinary path (rc {rc}): {out}"
    )


def test_without_the_fix_whitespace_reaches_the_callee():
    """The control: restore `-ne ''` and watch the same input get through.

    Without this, the test above passes on any body that merely never appends a
    client id -- including one where the append was deleted outright (ledger L202).
    The anchor count is asserted BEFORE substituting (ledger L47).
    """
    body = _step()["run"]
    anchor = "-not [string]::IsNullOrWhiteSpace($env:TICKET_CLIENT_ID)"
    assert body.count(anchor) == 1, (
        f"expected exactly one client_id gate to downgrade, found "
        f"{body.count(anchor)} -- this control would otherwise test an unmodified "
        f"body. Body: {body!r}"
    )
    downgraded = body.replace(anchor, "$env:TICKET_CLIENT_ID -ne ''")
    assert downgraded != body, "the downgrade did not apply"

    out, _, rc = _run(
        downgraded,
        TICKET_DEPTID=LEGAL_DEPTID,
        TICKET_SUBJECT=LEGAL_SUBJECT,
        TICKET_MESSAGE=LEGAL_MESSAGE,
        TICKET_EMAIL="",
        TICKET_CLIENT_ID="   ",
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert "ClientId=[   ]" in out, (
        f"the pre-#1207 gate did NOT admit whitespace, so the test above is not "
        f"discriminating and the fix it vouches for is not the reason it passes "
        f"(rc {rc}): {out}"
    )


# --------------------------------------------------------------------------
# Fail-closed, and the control that says what the guard is actually for
# --------------------------------------------------------------------------


def test_an_empty_deptid_fails_closed_and_says_so():
    """rc AND text, per CLAUDE.md: `rc == 1` alone cannot tell a refusal from a
    harness that could not start."""
    out, _, rc = _run(
        _step()["run"],
        TICKET_DEPTID="   ",
        TICKET_SUBJECT=LEGAL_SUBJECT,
        TICKET_MESSAGE=LEGAL_MESSAGE,
        TICKET_EMAIL="",
        TICKET_CLIENT_ID="",
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert rc == 1, f"an empty TICKET_DEPTID exited {rc}, expected 1: {out}"
    assert "::error::TICKET_DEPTID is empty" in out, (
        f"the step exited 1 with an empty TICKET_DEPTID but did not say so — an "
        f"exit code alone does not distinguish a refusal from a broken harness. "
        f"Output: {out}"
    )
    assert "CALLED DeptId=" not in out, (
        f"the scanner was invoked despite an empty deptid: {out}"
    )


def test_an_unset_deptid_fails_closed_and_says_which_one():
    out, _, rc = _run(_step()["run"], **{TOKEN_VAR: FAKE_TOKEN})
    assert rc == 1, f"unset mappings exited {rc}, expected 1: {out}"
    assert "::error::TICKET_DEPTID is empty" in out, (
        f"the step exited 1 with no mappings at all but named nothing: {out}"
    )
    assert "CALLED DeptId=" not in out, f"the scanner ran anyway: {out}"


def test_without_the_guard_empty_binds_and_unset_refuses():
    """Both cases controlled, because the guard is only needed for one of them.

    An EMPTY-STRING deptid — the blank GitHub actually produces when the dispatcher
    clears the box — binds as an empty element and reaches the scanner SILENTLY at
    rc 0, which is what the guard exists to stop. An UNSET deptid (a deleted or
    misspelled `env:` line) makes `$env:TICKET_DEPTID` null, drops it from the
    splat, and the binder refuses the next token — the step already dies. A control
    that strips the guard and re-runs only one case claims two behaviours and
    measures one; this asserts the asymmetry (same one 306 measured).
    """
    stripped = _strip_guards(_step()["run"])

    out, _, rc = _run(
        stripped,
        TICKET_DEPTID="",
        TICKET_SUBJECT=LEGAL_SUBJECT,
        TICKET_MESSAGE=LEGAL_MESSAGE,
        TICKET_EMAIL="",
        TICKET_CLIENT_ID="",
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert rc == 0 and "CALLED DeptId=[]" in out, (
        f"with the guard removed, an EMPTY-STRING deptid was expected to bind and "
        f"reach the scanner silently (rc 0, DeptId=[]) — that is what the guard "
        f"exists to stop. Got rc={rc}: {out}"
    )

    out, _, rc = _run(stripped, **{TOKEN_VAR: FAKE_TOKEN})
    assert rc == 1, (
        f"with the guard removed, an UNSET deptid was expected to fail on the "
        f"binder's own refusal (rc 1). Got rc={rc}: {out}"
    )
    assert "Missing an argument for parameter 'DeptId'" in out, (
        f"the unset case failed for some reason other than the binder refusing the "
        f"next token, so the model in this module's docstring is not what was "
        f"measured: {out}"
    )
    assert "CALLED DeptId=" not in out, (
        f"the scanner was reached with an unset -DeptId: {out}"
    )


# --------------------------------------------------------------------------
# The checker agrees
# --------------------------------------------------------------------------


def test_the_guard_no_longer_reports_this_workflow():
    findings, unreadable, _ = guard.scan_all()
    assert not unreadable, f"the guard could not read: {unreadable}"
    assert WORKFLOW not in guard.current_map(findings), (
        f"{WORKFLOW} still interpolates a free-text dispatch input into a script "
        f"body"
    )
    assert WORKFLOW not in guard.KNOWN_UNGUARDED, (
        f"{WORKFLOW} was burned down but is still listed in KNOWN_UNGUARDED — a "
        f"stale entry, which the guard itself exits 1 on"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a pwsh subprocess; the wiring, free-text,
# credential-reachability and checker-agreement cases are pure YAML/AST and must
# run on a host with no pwsh (#1182 — a whole-module `shutil.which` gate turns
# "could not run" into "everything passed"). This set is scoped to exactly the
# cases that call `_run`.
NEEDS_PWSH = {
    "test_the_pre_fix_body_stole_the_whmcs_secret_and_exited_zero",
    "test_the_subexpression_payload_is_inert_in_single_quotes",
    "test_the_shipped_body_binds_the_deptid_payload_as_data",
    "test_the_shipped_body_binds_the_client_id_payload_as_data",
    "test_the_shipped_body_still_passes_ordinary_inputs_through",
    "test_an_empty_deptid_fails_closed_and_says_so",
    "test_an_unset_deptid_fails_closed_and_says_which_one",
    "test_without_the_guard_empty_binds_and_unset_refuses",
}

if __name__ == "__main__":
    have_pwsh = shutil.which("pwsh") is not None
    failures = 0
    for t in TESTS:
        if t.__name__ in NEEDS_PWSH and not have_pwsh:
            print(f"  SKIP {t.__name__} (pwsh not installed; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
