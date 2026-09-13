"""Unit tests for 208's three dispatch-input call sites (#1080 lane 24).

208 exports WHMCS support tickets to a CSV artifact. Its two free-text dispatch
inputs used to be interpolated into the one `whmcs-prod-read` pwsh body across
THREE call sites: `output_file` into a double-quoted assignment, and `status`
into two single-quoted positions on one line. Both now arrive through step-level
`env:` (IN_OUTPUT_FILE / IN_STATUS).

TWO QUOTING SHAPES, AND THE SAME TEXT SUBSTITUTED TWICE ON ONE LINE
    The shape that makes this lane different from 218's, which also carried two
    quoting shapes: here the single-quoted input appears TWICE in one statement,
    so a payload that breaks out of the first quote leaves the second occurrence
    dangling and the step dies with a parse error. An injection that fails
    loudly is the one thing an injection must not do, so the payload has to
    close the call AND the `if`, run, re-supply the legitimate `-Status` append,
    and comment out the rest of the line — which is where the second occurrence
    sits. Measured on pwsh 7.4.6 against the body as it shipped, with a decoy
    WHMCS credential in the environment:

        $out = "${{ inputs.output_file }}"   DOUBLE-quoted, so `$( )` expands
                                             where it stands and no breakout is
                                             needed at all. `Set-Content` emits
                                             nothing, so `$out` keeps a legal
                                             path and the callee binds arguments
                                             byte-identical to the control.
        '${{ inputs.status }}'  x2           SINGLE-quoted, so `$( )` is inert.
                                             `Open')) { }; <payload>;
                                             $cliArgs += @('-Status','Open'); #`

    Both exfiltrated the credential at exit 0 with the legitimate export still
    made. Nothing in the run — arguments, exit code, artifact — distinguishes
    either from an ordinary dispatch.

THE BLANK CASE IS PRE-EXISTING AND SILENT, AND ONLY THE WHITESPACE HALF OF IT
    Ledger L292's QUOTED case: the shipped site sat inside double quotes, so an
    empty `output_file` was already an argument that is present and empty rather
    than no argument at all. Measured against BOTH bodies with the guard
    stripped, which is what separates the two halves:

        empty ('')       rc 1 at `Split-Path -Parent`, in the shipped body and
                         the fixed one alike. Already loud; the remedy neither
                         creates nor removes this case, and a guard justified on
                         this half alone would be attribution only (L260).
        all-whitespace   callee binds `OutputFile=[   ]` and the step exits
                         **0** — in BOTH bodies. Silent and wrong: the export
                         writes its CSV to a whitespace-named file while the
                         `Upload CSV artifact` step's `path:` looks for the same
                         blank name two steps later.

    So the guard is load-bearing, it closes a defect the interpolation was
    hiding, and `-not IsNullOrWhiteSpace` rather than `-ne ''` is the whole of
    what makes it do anything: the one case it catches is the one case that was
    silent. The same measurement 601 recorded under #1213.
    `test_without_the_guard_a_whitespace_output_file_binds_whitespace_at_exit_zero`
    pins it so a later lane copying this shape cannot drop the guard on L260's
    reasoning.

WHY `output_file` FAILS CLOSED RATHER THAN DEFAULT-FILLING
    It declares a default, so L254 would ordinarily say default-fill. It has a
    SECOND consumer: the `Upload CSV artifact` step interpolates the same raw
    input into its `path:`. That is an artifact path, not a script body, so
    #1080's guard correctly does not judge it and this lane does not widen to
    cover it — but a default supplied only in the body would write the CSV to
    the default path while the upload still looked at the blank one, turning a
    clear failure into an `if-no-files-found: error` two steps later that names
    neither cause. Failing closed keeps the two consumers agreeing. Identical
    reasoning to 218's `output_file`, deliberately: the two workflows have the
    same two-consumer shape.

WHY `status` NEEDS NO GUARD, AND WHY THE PREDICATE'S SPELLING MATTERS MORE NOW
    `status` is `required: false` with no declared default, and blank means "no
    filter" — the documented behaviour, not an error. The shipped body already
    gated it, and that gate is preserved. What the move to `env:` changes is the
    predicate's DOMAIN: an omitted input reaches the interpolated body as the
    empty string between two quotes, and reaches this one as `$null`.
    `IsNullOrWhiteSpace` is true for both; `-ne ''` is true for `$null`, so the
    #1213 spelling is load-bearing in one MORE case after the move than before
    it. Measured: blank, all-whitespace and unset all bind `Status=[]` at rc 0,
    in the shipped body and this one alike.
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
_spec = importlib.util.spec_from_file_location("interp_guard_208", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "208-whmcs-tickets-export.yml"
JOB = "export_tickets"
STEP = "Export tickets (read-only)"
ARTIFACT_STEP = "Upload CSV artifact"
CALLEE = "whmcs-tickets-export.ps1"

MAPPINGS = {
    "IN_OUTPUT_FILE": "${{ inputs.output_file }}",
    "IN_STATUS": "${{ inputs.status }}",
}
BURNED_INPUTS = ("output_file", "status")

LEGAL_OUT = "artifacts/whmcs/whmcs_tickets.csv"
LEGAL_STATUS = "Open"

SENTINEL = "STOLEN-208.txt"

# The variables that stand in for the workflow's own `env:` mapping. Each is
# REMOVED unless a test supplies it, so an inherited one cannot satisfy an
# assertion the workflow is supposed to (ledger L199).
CONTROLLED_VARS = ("IN_OUTPUT_FILE", "IN_STATUS")

# The decoys standing in for what `whmcs-secrets-from-kv` exports to GITHUB_ENV.
# Always set: they are the thing a payload is supposed to be able to reach, so
# clearing them would make the pre-fix control look harmless while the payload
# had in fact executed perfectly. The assertions below read the sentinel's
# CONTENTS for that reason — its mere existence would score the same either way.
DECOY_CREDENTIALS = {
    "WHMCS_API_SECRET": "whmcs-secret-placeholder-not-a-real-credential",
    "WHMCS_APIM_SUBSCRIPTION_KEY": "apim-key-placeholder-not-a-real-credential",
}

# The payload for the double-quoted assignment: no breakout needed. `$null =`
# swallows Set-Content's (absent) output and the trailing string leaves `$out`
# holding a legal path, so the run reads as ordinary.
SUBEXPR_PAYLOAD = (
    LEGAL_OUT
    + "$(Write-Host 'INJECTED-208-SUBEXPR'; "
    + "$null = Set-Content -Path " + SENTINEL + " -Value \"secret=$env:WHMCS_API_SECRET\")"
)

# The payload for the single-quoted pair. It closes `IsNullOrWhiteSpace(` and the
# `if`, supplies an empty body for it, runs, re-appends the legitimate -Status,
# then comments out the remainder of the line — which is where the SECOND
# occurrence of the substituted text lives.
BREAKOUT_PAYLOAD = (
    LEGAL_STATUS + "')) { }; "
    "$null = Set-Content -Path " + SENTINEL + " -Value \"apim=$env:WHMCS_APIM_SUBSCRIPTION_KEY\"; "
    "Write-Host 'INJECTED-208-BREAKOUT'; "
    "$cliArgs += @('-Status','" + LEGAL_STATUS + "'); #"
)

# The guards the pre-fix control strips, each with the literal that ends its
# block so the strip can find the closing brace without a bare `.index`.
#
# All four are `output_file`'s and all four are this lane's, so all four come
# out for the pre-fix body: one emptiness guard, and the three path guards added
# for the Copilot finding on #1308.
#
# The END LITERAL is each guard's own message fragment, not the bare word
# `throw`. It was `throw` while the emptiness guard was the only one, and the
# moment the path guards landed that made `test_the_guard_anchor…` stop
# discriminating: it asserted no `throw` survived the strip, which a correct
# strip of one guard out of four can no longer satisfy. The control failed
# loudly and correctly rather than going quietly permissive — but the shape is
# worth naming, because an end-anchor shared by several blocks silently stops
# identifying any one of them.
GUARD_BLOCKS = (
    (
        "if ([string]::IsNullOrWhiteSpace($env:IN_OUTPUT_FILE)) {",
        "output_file is blank.",
    ),
    (
        r"if ($env:IN_OUTPUT_FILE -match '[*?\[\]]') {",
        "glob metacharacter",
    ),
    (
        r"if ($env:IN_OUTPUT_FILE -match '^([A-Za-z]:|[\\/])') {",
        "workspace-relative",
    ),
    (
        r"if (($env:IN_OUTPUT_FILE -split '[\\/]') -contains '..') {",
        "'..' segment",
    ),
)

# The shipped spellings the pre-fix control rewrites, and what each becomes when
# GitHub pastes the value in as raw text instead.
PRE_FIX_SITES = (
    ("$out = $env:IN_OUTPUT_FILE", '$out = "{}"'),
    (
        "if (-not [string]::IsNullOrWhiteSpace($env:IN_STATUS)) "
        "{ $cliArgs += @('-Status', $env:IN_STATUS) }",
        "if (-not [string]::IsNullOrWhiteSpace('{0}')) "
        "{{ $cliArgs += @('-Status', '{0}') }}",
    ),
)

# A stand-in for the real exporter that records what it was BOUND. That is the
# only discriminator that works: a marker search over stdout is not one, because
# pwsh echoes offending source back in a binder error, so a substring predicate
# matches the payload text on a run that executed nothing.
STUB = """param(
    [Parameter()]
    [string]$Status,
    [Parameter()]
    [string]$OutputFile = 'whmcs_tickets.csv'
)
[Console]::Error.WriteLine("CALLED Status=[$Status] Out=[$OutputFile]")
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $dir = Split-Path -Parent $OutputFile
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Set-Content -Path $OutputFile -Value 'id,subject'
}
exit 0
"""

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that omits
# it pins the wrong exit code and vouches for it (recorded on #1080's lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)


def _workflow() -> dict:
    return load_workflow(WORKFLOW)


def _step() -> dict:
    return find_step(_workflow(), JOB, STEP)


def _body() -> str:
    return _step()["run"]


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input this body reaches through a `${{ }}` expression.

    Deliberately the CHECKER's own two patterns rather than a substring test of
    this module's own devising, so a spelling the checker recognises cannot slip
    past the step-level assertion and the two cannot drift apart.
    """
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    return found


def _strip_guards(body: str) -> str:
    """Remove all four `output_file` guards, asserting each was there first.

    Counts are asserted BEFORE substituting (ledger L47): an anchor that stopped
    matching must fail loudly rather than silently leave the body unchanged and
    score the control as a pass.

    Every lookup is guarded by an assertion rather than a bare `str.index`. A
    ValueError here is not an AssertionError, so the module runner would not
    catch it — it would abort the roster and every case sorted after this one
    would report no outcome at all, which a reviewer counting FAIL lines scores
    as passing (ledger L194).
    """
    for anchor, terminator in GUARD_BLOCKS:
        assert body.count(anchor) == 1, (
            f"expected exactly one {anchor!r} to strip, found "
            f"{body.count(anchor)} — this control would otherwise measure an "
            f"unmodified body. Body: {body!r}"
        )
        start = body.index(anchor)
        assert terminator in body[start:], (
            f"the guard at {anchor!r} no longer contains {terminator!r}, so "
            f"this control cannot locate its end. Body: {body!r}"
        )
        end = body.index("}", body.index(terminator, start)) + 1
        body = body[:start] + body[end:]
    # Count the ANCHOR, not the bare call name: the step body explains each
    # guard in comments directly above it, so an `"IsNullOrWhiteSpace" not in
    # body` check would fail on the PROSE describing the thing it looks for and
    # report the control as broken over a correct strip (#1019). For the same
    # reason the message fragments are checked inside the remaining CODE only —
    # the comments quote them too.
    for anchor, _ in GUARD_BLOCKS:
        assert anchor not in body, (
            f"a guard survived the strip, so the control is measuring the "
            f"guarded body. Stripped: {body!r}"
        )
    assert CALLEE in body, (
        f"the strip removed the invocation itself, so the control proves "
        f"nothing. Stripped: {body!r}"
    )
    return body


def _pre_fix(output_file: str, status: str) -> str:
    """The body as it shipped BEFORE this lane, with GitHub's substitution done.

    DERIVED from the current body rather than kept as a second copy, so the
    control cannot drift from what ships: only the spellings the burn-down
    changed are rewritten, and the guard the burn-down added is stripped — it is
    part of the remedy, and leaving it in would make the control refuse the
    payload for the reason under test rather than executing it.

    Each anchor's count is asserted before substituting (ledger L47).
    """
    body = _strip_guards(_body())
    for shipped, template in PRE_FIX_SITES:
        assert body.count(shipped) == 1, (
            f"expected exactly one {shipped!r} to rewrite, found "
            f"{body.count(shipped)} — the pre-fix control would otherwise "
            f"measure a body that is not the shipped one. Body: {body!r}"
        )
        value = output_file if "IN_OUTPUT_FILE" in shipped else status
        body = body.replace(shipped, template.format(value))
    return body


def _run(body: str, **env_overrides: str):
    """Run a pwsh body the way the RUNNER runs it, in a temp cwd holding the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS, not
    merely its existence: a file written from an unset variable would score the
    same as one written from the live credential, and the claim under test is
    which credential the payload reached.

    `stdin=DEVNULL` is load-bearing, not hygiene. An unsatisfied parameter makes
    PowerShell PROMPT for it, and on an interactive stdin the call blocks
    forever. A runner's stdin is not a terminal, so DEVNULL is also the faithful
    shape.

    A variable is REMOVED when its override is None, rather than set to "": the
    two blank forms differ and conflating them is what L291 is about.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / CALLEE).write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(
            RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8", newline="\n"
        )
        concrete = {k: v for k, v in env_overrides.items() if v is not None}
        env = child_env(**DECOY_CREDENTIALS, **concrete)
        for var in CONTROLLED_VARS:
            if var not in concrete:
                env.pop(var, None)
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=tmp,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        stolen = tmp / SENTINEL
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return proc.stdout + proc.stderr, contents, proc.returncode


def _bound(output: str) -> str:
    """The stub's `CALLED …` line, or "" when the callee never ran."""
    for line in output.splitlines():
        if line.startswith("CALLED "):
            return line
    return ""


def _executed(output: str, marker: str) -> bool:
    """Did the payload RUN, as opposed to merely appearing in the output?

    `marker in output` is not the question, and getting that wrong is the whole
    reason this helper exists. When the remedy works, the payload arrives at the
    callee as an ordinary argument VALUE — so the stub's `CALLED …` line quotes
    it verbatim, and so does any pwsh error that echoes the offending argument
    back. A substring predicate therefore reports "the payload executed" for the
    run in which it provably did not, which is the reassuring direction: it
    turns the fixed body's proof into a failure and would push the next reader
    to weaken the assertion rather than the predicate.

    `Write-Host` emits the marker as a line of its OWN. Nothing that merely
    quotes the payload can produce that, because every such line carries the
    surrounding argument text with it. So the discriminator is an exact
    whole-line match, and the sentinel file — written only by code that ran —
    is the independent second reading beside it.

    218's module records the mirror image of this trap (a marker search matching
    a binder error on a run that executed nothing); this is the same mistake
    reached from the data side.
    """
    return any(line.strip() == marker for line in output.splitlines())


# --------------------------------------------------------------------------
# Wiring — pure YAML/AST, no tool required
# --------------------------------------------------------------------------


def test_both_inputs_travel_in_env_and_are_not_interpolated():
    """The mapping exists, the body reads it, and no site was reintroduced.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES these variables (ledger L199): delete the
    workflow's `env:` mapping and the step still sees them from the harness, so
    every behavioural test below would keep passing over plumbing that no longer
    exists.
    """
    step = _step()
    env = step.get("env") or {}
    body = step["run"]
    for var, expression in MAPPINGS.items():
        assert env.get(var) == expression, (
            f"step {STEP!r} in job {JOB!r} must map {var} to {expression} — its "
            f"env: mapping is {env!r}"
        )
        assert f"$env:{var}" in body, (
            f"step {STEP!r} maps {var} but never reads $env:{var} — the env: "
            f"block is decoration and the value reaches nothing. Body: {body!r}"
        )
    reintroduced = _interpolated_inputs(body) & set(BURNED_INPUTS)
    assert not reintroduced, (
        f"step {STEP!r} interpolates {sorted(reintroduced)} into its script body "
        f"again (#1080): GitHub substitutes that as raw text before the body is "
        f"parsed, so a dispatcher supplies code. Body: {body!r}"
    )


def test_each_variable_reaches_the_CALL_SITE_and_not_merely_the_body():
    """`$env:X in body` is too weak, because the guards read the variables too.

    Ledger L294. The #1080 remedy has two parts — a guard that reads the mapped
    variable, and a call site that passes it on — so a body that fills a value
    and then hands the callee a literal satisfies the wiring assertion above
    while the dispatcher's value reaches nothing. Measured on this body: replace
    the `-Status` append's `$env:IN_STATUS` with `'Open'` and a dispatch of
    `status: Closed` still binds `Status=[Open]` at rc 0.

    208 invokes through a SPLAT rather than named arguments, so the call site is
    the `$cliArgs` construction rather than the `&` line — which is the shape
    L296 records a locator failing to read. Both halves are asserted here: the
    array is built from the variables, and the `&` line splats that array and
    nothing else.
    """
    body = _body()
    invocation = [
        ln for ln in body.splitlines() if CALLEE in ln and ln.lstrip().startswith("&")
    ]
    assert len(invocation) == 1, (
        f"expected exactly one line invoking {CALLEE}; found {len(invocation)}. "
        f"Body: {body!r}"
    )
    line = invocation[0]
    assert "@cliArgs" in line, (
        f"the invocation must splat $cliArgs — if it stopped doing so, the "
        f"$cliArgs assertions below are no longer about the arguments the callee "
        f"receives. Invocation: {line!r}"
    )
    for argument in (
        "$cliArgs = @('-OutputFile', $out)",
        "$cliArgs += @('-Status', $env:IN_STATUS)",
    ):
        assert argument in body, (
            f"the argument array must be built with {argument!r}, or the "
            f"dispatched value reaches nothing however faithfully the env: block "
            f"maps it. Body: {body!r}"
        )
    assert "$out = $env:IN_OUTPUT_FILE" in body, (
        f"`$out` must come from IN_OUTPUT_FILE — the array passes `$out`, so a "
        f"`$out` assigned from anything else silently ignores the input. "
        f"Body: {body!r}"
    )


def test_the_checker_agrees_this_workflow_is_burned_down():
    """The guard's freeze and this module must not be able to disagree.

    Both halves matter: an entry left in `KNOWN_UNGUARDED` after the fix makes
    the guard's stale-entry detection fail, and a site the guard still finds
    means the burn-down is incomplete however green this module is.
    """
    assert WORKFLOW not in guard.KNOWN_UNGUARDED, (
        f"{WORKFLOW} is burned down but is still listed in KNOWN_UNGUARDED — the "
        f"guard's stale-entry detection should be failing on this tree"
    )
    findings = _interpolated_inputs(_body())
    assert not findings, (
        f"the checker still finds {sorted(findings)} interpolated in {WORKFLOW}'s "
        f"step body"
    )


def test_output_file_fails_closed_because_the_artifact_step_shares_the_input():
    """The second consumer is what rules out default-fill — pin that it exists.

    If the upload step ever stops interpolating the raw input into its `path:`,
    the reason for failing closed rather than default-filling is gone and this
    body should be revisited. That is a reason to fail here, loudly, rather than
    to leave a comment in the workflow claiming a coupling that no longer holds.
    """
    upload = find_step(_workflow(), JOB, ARTIFACT_STEP)
    path = str((upload.get("with") or {}).get("path", ""))
    assert "inputs.output_file" in path, (
        f"step {ARTIFACT_STEP!r} no longer consumes the output_file input in its "
        f"path: ({path!r}) — the two-consumer argument for failing closed rather "
        f"than default-filling no longer applies, so re-decide it rather than "
        f"leaving the body's comment asserting a coupling that is gone"
    )
    body = _body()
    for anchor, _ in GUARD_BLOCKS:
        assert anchor in body, (
            f"the guard {anchor!r} is gone from the body while the second "
            f"consumer remains. All four exist because this input reaches a "
            f"glob-capable `path:` as well as this script. Body: {body!r}"
        )


def test_the_job_still_enters_only_a_read_environment():
    """#1080's write half is finished; a lane must not reopen it.

    208's whole blast radius argument — a payload here runs under a read-scoped
    WHMCS credential — depends on this environment, so it is asserted rather
    than assumed.
    """
    job = _workflow()["jobs"][JOB]
    env_name = job.get("environment")
    assert isinstance(env_name, str), (
        f"job {JOB!r} declares environment {env_name!r}; this assertion reads "
        f"only the bare-string shape"
    )
    assert not guard.is_write_environment(env_name), (
        f"job {JOB!r} now enters {env_name!r}, a write environment. #1080's "
        f"write half is closed and this workflow was a read lane"
    )


def test_the_guard_anchor_this_module_strips_is_really_in_the_body():
    """Ledger L47: prove the pre-fix control can still find what it removes.

    Without this, a refactor that renamed a guard would make `_strip_guards`
    fail — but only inside the pwsh-gated cases, which SKIP on a host with no
    pwsh. The control would then be silently unexercised on exactly the hosts
    that cannot notice.
    """
    body = _body()
    for anchor, _ in GUARD_BLOCKS:
        assert body.count(anchor) == 1, (
            f"the pre-fix control's guard anchor {anchor!r} appears "
            f"{body.count(anchor)} times. Body: {body!r}"
        )
    for shipped, _ in PRE_FIX_SITES:
        assert body.count(shipped) == 1, (
            f"the pre-fix control rewrites {shipped!r}, which appears "
            f"{body.count(shipped)} times. Body: {body!r}"
        )
    stripped = _strip_guards(body)
    # Each guard's own message, not the shared word `throw`: four blocks end in
    # `throw`, so that anchor stopped identifying any one of them the moment the
    # path guards landed. Checked against the CODE before the invocation only —
    # the comments above each guard quote these fragments too.
    code = stripped.split(CALLEE)[0]
    for _, message in GUARD_BLOCKS:
        surviving = [
            line for line in code.splitlines()
            if message in line and not line.lstrip().startswith("#")
        ]
        assert not surviving, (
            f"the strip left {message!r} behind in executable code: "
            f"{surviving!r}"
        )


# --------------------------------------------------------------------------
# The pre-fix controls — the defect this lane removes, reproduced
# --------------------------------------------------------------------------


def test_the_pre_fix_body_ran_the_subexpression_payload_and_exited_zero():
    """The double-quoted site: `$( )` expands, so no quote breakout is needed."""
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, rc = _run(_pre_fix(SUBEXPR_PAYLOAD, LEGAL_STATUS))
    assert _executed(out, "INJECTED-208-SUBEXPR"), (
        f"the pre-fix control did not execute the payload, so it does not "
        f"reproduce the defect this lane removes. Output: {out!r}"
    )
    assert stolen is not None and DECOY_CREDENTIALS["WHMCS_API_SECRET"] in stolen, (
        f"the payload ran but did not reach the WHMCS credential, so the control "
        f"understates the finding. Sentinel: {stolen!r}"
    )
    assert rc == 0, (
        f"the injected run must be indistinguishable from an ordinary one; "
        f"rc={rc}. Output: {out!r}"
    )
    assert _bound(out) == f"CALLED Status=[{LEGAL_STATUS}] Out=[{LEGAL_OUT}]", (
        f"the legitimate export must still have been made with correct "
        f"arguments — that is what makes the injection invisible. "
        f"Bound: {_bound(out)!r}"
    )


def test_the_pre_fix_body_ran_the_breakout_payload_and_exited_zero():
    """The single-quoted PAIR: one breakout must satisfy both occurrences.

    The discriminating half of this case is not that the payload runs — it is
    that the step still exits 0 with the legitimate `-Status` bound, despite the
    substituted text appearing a second time later on the same line. A payload
    that only closed the first quote would leave that second occurrence dangling
    and die at parse time.
    """
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, rc = _run(_pre_fix(LEGAL_OUT, BREAKOUT_PAYLOAD))
    assert _executed(out, "INJECTED-208-BREAKOUT"), (
        f"the pre-fix control did not execute the payload. Output: {out!r}"
    )
    assert (
        stolen is not None
        and DECOY_CREDENTIALS["WHMCS_APIM_SUBSCRIPTION_KEY"] in stolen
    ), (
        f"the payload ran but did not reach the APIM subscription key. "
        f"Sentinel: {stolen!r}"
    )
    assert rc == 0, f"the injected run must exit 0; rc={rc}. Output: {out!r}"
    assert _bound(out) == f"CALLED Status=[{LEGAL_STATUS}] Out=[{LEGAL_OUT}]", (
        f"the legitimate export must still have been made with correct "
        f"arguments. Bound: {_bound(out)!r}"
    )


def test_the_pre_fix_body_also_bound_a_whitespace_output_file_at_exit_zero():
    """L292: the silent blank case PRE-DATES the remedy rather than being created.

    This is the measurement that makes the fail-closed guard load-bearing rather
    than attribution. If this ever goes red because the shipped body refused a
    whitespace path, the guard's justification changes and the body's comment
    should be rewritten — not this test deleted.
    """
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, rc = _run(_pre_fix("   ", ""))
    assert rc == 0, (
        f"the pre-fix body is claimed to have accepted a whitespace path "
        f"silently; rc={rc}. Output: {out!r}"
    )
    assert stolen is None, f"no payload was supplied, yet something wrote {stolen!r}"
    assert _bound(out) == "CALLED Status=[] Out=[   ]", (
        f"the whitespace path must reach the callee as an argument that is "
        f"present and blank — that is the defect the guard closes. "
        f"Bound: {_bound(out)!r}"
    )


def test_the_pre_fix_body_was_already_loud_for_an_EMPTY_output_file():
    """The other half of L292's measurement, and the half that does NOT change.

    Kept beside the whitespace case deliberately: reporting only the silent half
    would overstate what the guard buys, and reporting only this half would
    understate it. The difference between the two is the whole finding.
    """
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(_pre_fix("", ""))
    assert rc != 0, (
        f"an empty output_file is claimed to have failed loudly in the shipped "
        f"body too; rc={rc}. Output: {out!r}"
    )
    assert _bound(out) == "", (
        f"the callee must not have run. Bound: {_bound(out)!r}"
    )


def test_without_the_guard_a_whitespace_output_file_binds_whitespace_at_exit_zero():
    """The guard is load-bearing: strip it and the fixed body is silent too.

    Discrimination, not permissiveness (ledger L47). The guarded body refuses
    this exact value (`test_a_whitespace_output_file_fails_closed_too`); with the
    guard removed it binds `Out=[   ]` at rc 0, which is what proves the guard is
    doing the work rather than the move to `env:` having fixed it incidentally.
    """
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(_strip_guards(_body()), IN_OUTPUT_FILE="   ", IN_STATUS="")
    assert rc == 0, (
        f"the guard-stripped body should accept a whitespace path silently, or "
        f"the guard is not what makes the shipped body refuse it; rc={rc}. "
        f"Output: {out!r}"
    )
    assert _bound(out) == "CALLED Status=[] Out=[   ]", (
        f"expected the whitespace path to bind. Bound: {_bound(out)!r}"
    )


# --------------------------------------------------------------------------
# The shipped body — the same inputs, now data
# --------------------------------------------------------------------------


def test_the_shipped_body_binds_the_subexpression_payload_as_data():
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, _rc = _run(
        _body(), IN_OUTPUT_FILE=SUBEXPR_PAYLOAD, IN_STATUS=LEGAL_STATUS
    )
    assert not _executed(out, "INJECTED-208-SUBEXPR"), (
        f"the payload executed through the env: mapping. Output: {out!r}"
    )
    assert stolen is None, (
        f"the payload reached a credential through the env: mapping: {stolen!r}"
    )
    assert SUBEXPR_PAYLOAD in _bound(out), (
        f"the payload must arrive at the callee as an inert argument value. "
        f"Bound: {_bound(out)!r}"
    )


def test_the_shipped_body_binds_the_breakout_payload_as_data():
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, rc = _run(
        _body(), IN_OUTPUT_FILE=LEGAL_OUT, IN_STATUS=BREAKOUT_PAYLOAD
    )
    assert not _executed(out, "INJECTED-208-BREAKOUT"), (
        f"the payload executed through the env: mapping. Output: {out!r}"
    )
    assert stolen is None, (
        f"the payload reached a credential through the env: mapping: {stolen!r}"
    )
    assert rc == 0, f"an ordinary (if odd) status value should not fail; rc={rc}"
    assert BREAKOUT_PAYLOAD in _bound(out), (
        f"the payload must arrive at the callee as an inert -Status value. "
        f"Bound: {_bound(out)!r}"
    )


def test_the_shipped_body_passes_ordinary_values_through():
    """The positive control: the remedy must not have broken the common path."""
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(
        _body(), IN_OUTPUT_FILE=LEGAL_OUT, IN_STATUS="Answered"
    )
    assert rc == 0, f"an ordinary dispatch must succeed; rc={rc}. Output: {out!r}"
    assert _bound(out) == f"CALLED Status=[Answered] Out=[{LEGAL_OUT}]", (
        f"both dispatched values must reach the callee. Bound: {_bound(out)!r}"
    )


def test_a_blank_output_file_fails_closed_and_never_calls_the_script():
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE="", IN_STATUS="")
    assert rc != 0, f"a blank output_file should fail closed; rc={rc}. {out!r}"
    assert "output_file is blank" in out, (
        f"the step exited non-zero without naming the cause, so this cannot be "
        f"told apart from a broken harness (CLAUDE.md). Output: {out!r}"
    )
    assert _bound(out) == "", (
        f"the callee ran despite the blank path — failing closed means not "
        f"calling it. Bound: {_bound(out)!r}"
    )


def test_an_unset_output_file_fails_closed_too():
    """The unset form of the same input, measured rather than assumed."""
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE=None, IN_STATUS="")
    assert rc != 0, f"an unset output_file should fail closed; rc={rc}. {out!r}"
    assert "output_file is blank" in out, (
        f"the step exited non-zero without naming the cause. Output: {out!r}"
    )
    assert _bound(out) == "", (
        f"the callee ran despite the missing path. Bound: {_bound(out)!r}"
    )


def test_a_whitespace_output_file_fails_closed_too():
    """The case the guard actually buys — silent in both pre-fix and stripped."""
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE="   ", IN_STATUS="")
    assert rc != 0, (
        f"a whitespace output_file should fail closed — this is the one blank "
        f"form that was silent before the guard; rc={rc}. Output: {out!r}"
    )
    assert "output_file is blank" in out, (
        f"the step exited non-zero without naming the cause. Output: {out!r}"
    )
    assert _bound(out) == "", (
        f"the callee ran with a whitespace path. Bound: {_bound(out)!r}"
    )


def test_a_glob_metacharacter_in_output_file_is_refused():
    """Raised by Copilot on #1308: the upload step's `path:` is a glob SELECTOR.

    `[` and `]` are the case that matters, and the reason this is not covered by
    "a bad path fails the export anyway": `*` and `?` are illegal in a Windows
    filename so they self-block, while `[` and `]` are legal there AND are
    character-class metacharacters to `@actions/glob`. All four are asserted, so
    a future narrowing to only the self-blocking pair fails here.
    """
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    for payload in (
        "artifacts/whmcs/*.csv",
        "artifacts/whmcs/tickets?.csv",
        "artifacts/whmcs/[m]sal_token_cache.json",
        "artifacts/whmcs/x].csv",
    ):
        out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE=payload, IN_STATUS="")
        assert rc != 0, (
            f"output_file {payload!r} carries a glob metacharacter and must be "
            f"refused; rc={rc}. Output: {out!r}"
        )
        assert "glob metacharacter" in out, (
            f"the step exited non-zero without naming the cause, so this cannot "
            f"be told apart from a broken harness (CLAUDE.md). Output: {out!r}"
        )
        assert _bound(out) == "", (
            f"the callee ran despite the glob. Bound: {_bound(out)!r}"
        )


def test_an_absolute_or_drive_rooted_output_file_is_refused():
    """Needs no glob at all — the upload uploads whatever its path: resolves to.

    The Windows spellings are asserted from a Linux test host on purpose: the
    guard is an explicit regex rather than `IsPathRooted` precisely so that the
    host running the assertion and the host running the job agree. If this is
    ever rewritten to a platform-dependent API, `C:/…` and `\\\\server\\share`
    stop being refused here while still reaching the runner.
    """
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    for payload in (
        "/etc/passwd",
        "C:/Users/runneradmin/.azure/msal_token_cache.json",
        "C:\\Users\\runneradmin\\.azure\\accessTokens.json",
        "\\\\server\\share\\x.csv",
        "/home/runner/.azure/x.csv",
    ):
        out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE=payload, IN_STATUS="")
        assert rc != 0, (
            f"output_file {payload!r} is absolute/rooted and must be refused; "
            f"rc={rc}. Output: {out!r}"
        )
        assert "workspace-relative" in out, (
            f"the step exited non-zero without naming the cause. Output: {out!r}"
        )
        assert _bound(out) == "", (
            f"the callee ran despite the absolute path. Bound: {_bound(out)!r}"
        )


def test_a_dotdot_segment_in_output_file_is_refused():
    """Both separators, because the job runs on Windows and the test on Linux."""
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    for payload in (
        "../../../home/runner/.azure/x.csv",
        "artifacts/../../x.csv",
        "artifacts\\..\\..\\x.csv",
    ):
        out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE=payload, IN_STATUS="")
        assert rc != 0, (
            f"output_file {payload!r} escapes the workspace and must be "
            f"refused; rc={rc}. Output: {out!r}"
        )
        assert "'..' segment" in out, (
            f"the step exited non-zero without naming the cause. Output: {out!r}"
        )
        assert _bound(out) == "", (
            f"the callee ran despite the '..'. Bound: {_bound(out)!r}"
        )


def test_the_path_guards_do_not_refuse_a_legitimate_output_file():
    """Positive control — discrimination, not permissiveness (ledger L47).

    Three guards that reject everything would satisfy every case above. These
    are the shapes an ordinary dispatch uses, including the declared default and
    a name containing a dot-segment that is NOT `..`, which a naive
    `contains '..'` substring test would reject.
    """
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    for payload in (
        LEGAL_OUT,
        "whmcs_tickets.csv",
        "artifacts/whmcs/2026-09-13.tickets.csv",
        "artifacts/whmcs/sub/dir/tickets.csv",
        "artifacts\\whmcs\\tickets.csv",
    ):
        out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE=payload, IN_STATUS="")
        assert rc == 0, (
            f"output_file {payload!r} is a legitimate relative path and must be "
            f"accepted; rc={rc}. Output: {out!r}"
        )
        assert _bound(out).endswith(f"Out=[{payload}]"), (
            f"the legitimate path must reach the callee unchanged. "
            f"Bound: {_bound(out)!r}"
        )


def test_the_declared_default_survives_its_own_guards():
    """The guards must not reject the value GitHub supplies when nobody types one.

    Read from `on.workflow_dispatch.inputs` rather than written down again: a
    default that the body refuses is a workflow that fails on every ordinary
    dispatch, and nothing else in this module would notice, because every other
    case passes its own literal.
    """
    workflow = _workflow()
    on = workflow.get(True, workflow.get("on"))
    assert isinstance(on, dict), f"{WORKFLOW} has no readable `on:` block"
    spec = (on.get("workflow_dispatch") or {}).get("inputs", {}).get("output_file")
    assert isinstance(spec, dict), f"output_file has no readable spec: {spec!r}"
    declared = spec.get("default")
    assert isinstance(declared, str) and declared, (
        f"output_file no longer declares a default ({declared!r}); the "
        f"fail-closed guard's message tells a dispatcher to omit the input to "
        f"get one, so that advice would now be wrong"
    )
    out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE=declared, IN_STATUS="")
    assert rc == 0, (
        f"the declared default {declared!r} is refused by this step's own "
        f"guards; rc={rc}. Output: {out!r}"
    )


def test_a_blank_status_adds_no_filter():
    """Blank `status` means "all tickets" — documented behaviour, not an error."""
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE=LEGAL_OUT, IN_STATUS="")
    assert rc == 0, f"a blank status must not fail; rc={rc}. Output: {out!r}"
    assert _bound(out) == f"CALLED Status=[] Out=[{LEGAL_OUT}]", (
        f"a blank status must add no -Status argument at all. "
        f"Bound: {_bound(out)!r}"
    )


def test_an_unset_status_adds_no_filter():
    """`$env:IN_STATUS` on an unset variable is `$null`, not "".

    This is the case the move to `env:` ADDS to the predicate's domain, and the
    reason #1213's `IsNullOrWhiteSpace` spelling matters more after the move than
    before it: `-ne ''` is true for `$null`, so that spelling would append a
    `-Status` argument here.
    """
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE=LEGAL_OUT, IN_STATUS=None)
    assert rc == 0, f"an unset status must not fail; rc={rc}. Output: {out!r}"
    assert _bound(out) == f"CALLED Status=[] Out=[{LEGAL_OUT}]", (
        f"an unset status must add no -Status argument. Bound: {_bound(out)!r}"
    )


def test_a_whitespace_status_adds_no_filter():
    """#1213's original finding on this workflow, re-measured after the move."""
    test_both_inputs_travel_in_env_and_are_not_interpolated()
    out, _stolen, rc = _run(_body(), IN_OUTPUT_FILE=LEGAL_OUT, IN_STATUS="   ")
    assert rc == 0, f"a whitespace status must not fail; rc={rc}. Output: {out!r}"
    assert _bound(out) == f"CALLED Status=[] Out=[{LEGAL_OUT}]", (
        f"a whitespace status must add no -Status argument — `-ne ''` would. "
        f"Bound: {_bound(out)!r}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a subprocess; the wiring and checker-agreement
# cases are pure YAML/AST and must run on a host without pwsh installed (#1182 —
# a whole-module `shutil.which` gate turns "could not run" into "everything
# passed"). This set is scoped to exactly the cases that call `_run`.
NEEDS_PWSH = {
    "test_the_pre_fix_body_ran_the_subexpression_payload_and_exited_zero",
    "test_the_pre_fix_body_ran_the_breakout_payload_and_exited_zero",
    "test_the_pre_fix_body_also_bound_a_whitespace_output_file_at_exit_zero",
    "test_the_pre_fix_body_was_already_loud_for_an_EMPTY_output_file",
    "test_without_the_guard_a_whitespace_output_file_binds_whitespace_at_exit_zero",
    "test_the_shipped_body_binds_the_subexpression_payload_as_data",
    "test_the_shipped_body_binds_the_breakout_payload_as_data",
    "test_the_shipped_body_passes_ordinary_values_through",
    "test_a_blank_output_file_fails_closed_and_never_calls_the_script",
    "test_an_unset_output_file_fails_closed_too",
    "test_a_whitespace_output_file_fails_closed_too",
    "test_a_glob_metacharacter_in_output_file_is_refused",
    "test_an_absolute_or_drive_rooted_output_file_is_refused",
    "test_a_dotdot_segment_in_output_file_is_refused",
    "test_the_path_guards_do_not_refuse_a_legitimate_output_file",
    "test_the_declared_default_survives_its_own_guards",
    "test_a_blank_status_adds_no_filter",
    "test_an_unset_status_adds_no_filter",
    "test_a_whitespace_status_adds_no_filter",
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
