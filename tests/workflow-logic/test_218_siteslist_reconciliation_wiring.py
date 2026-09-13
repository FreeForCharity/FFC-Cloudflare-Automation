"""Unit tests for 218's three dispatch-input call sites (#1080 lane 23).

218 joins the sites-list with live WHMCS domains and services to measure the
coverage gap and the product-alignment gap, and its JSON artifact is the Phase 2
backfill work-list. Its three remaining free-text dispatch inputs used to be
interpolated into the one `whmcs-prod-read` pwsh body: `output_file` into a
double-quoted assignment, and `cloudflare_pid` / `github_pages_pid` into
single-quoted arguments of the native call. All three now arrive through
step-level `env:`. (`api_url` already travelled in `env:` from an earlier lane
and is untouched here — `test_1146` and `test_1150` pin that mapping and its
guard, and `test_the_api_url_wiring_is_untouched` below re-asserts it so this
lane cannot quietly disturb a neighbour's fixture.)

TWO QUOTING SHAPES IN ONE BODY, AND THE DIFFERENCE IS ONLY HOW THE PAYLOAD IS
WRITTEN -- NOT WHETHER IT RUNS
    Measured on pwsh 7.4.6 against the body as it shipped. Each ran at exit 0
    with a decoy credential written to a sentinel and the legitimate call still
    made with correct arguments, so nothing in the run distinguishes it from an
    ordinary one:

        $out = "${{ inputs.output_file }}"      DOUBLE-quoted, so `$( )` expands
                                                where it stands -- no breakout.
                                                `$null =` swallows the payload's
                                                output, so `$out` keeps a legal
                                                path.
        -CloudflareProductPid '${{ ... }}'      SINGLE-quoted, so a quote
        -GithubPagesProductPid '${{ ... }}'     breakout is needed -- and is all
                                                that is needed: `40'; <payload>; #`

    The trailing-argument site is the cheapest of the three, because the
    legitimate call completes with all four arguments BEFORE the payload runs.
    `$LASTEXITCODE` is therefore the legitimate call's, the step's own
    `Test-Path` check passes, and the step exits 0.

A THIRD BLANK CASE: LOAD-BEARING, BUT PRE-EXISTING RATHER THAN CREATED BY THE
REMEDY
    The two lanes before this one bracket the question and neither answer fits.
    116 (ledger L260) measured that its blank was already loud in both forms and
    recorded its guard as attribution. 115 (ledger L291) found the hazard
    CREATED by the move to `env:`: its sites were UNQUOTED, so an empty
    substitution was no argument at all and the binder refused it, while through
    `env:` the same value survived as an argument that is present and empty.

    Here both pid sites were already SINGLE-quoted, so a blank was already an
    argument that is present and empty -- in the shipped body too. Measured, the
    guard stripped:

        empty ('')   binds `CfPid=[] GhPid=[]`, step exits **0**
        unset        same, because `$env:X` on an unset variable is `$null` and
                     `-Pid $null` binds `[string]` as empty

    That is silent and maximally wrong rather than merely cosmetic. The callee
    tests membership with `$clientPids[$cid].Contains($CloudflareProductPid)`
    (`scripts/whmcs-siteslist-reconciliation.ps1:174,192`), and `.Contains('')`
    is false for every client, so every Cloudflare domain and every Pages site
    falls into the `else` branch and is appended to the gap list. The artifact
    becomes a maximal FALSE work-list that reads exactly like a real finding.

    So the default-fill guard is load-bearing, and it fixes a defect the
    interpolation was hiding rather than one the remedy introduced.
    `test_without_the_guard_an_empty_pid_binds_empty_at_exit_zero` pins it so a
    later lane copying this shape cannot drop the guard on 116's reasoning.

WHY `output_file` FAILS CLOSED INSTEAD
    It declares a default too, so L254 would ordinarily say default-fill. It has
    a SECOND consumer: the `Upload reconciliation artifact` step interpolates the
    same raw input into its `path:`. That is an artifact path, not a script body,
    so #1080's guard correctly does not judge it and this lane does not widen to
    cover it -- but a default supplied only in the body would write the JSON to
    the default path while the upload still looked at the blank one, turning a
    clear failure into a confusing `if-no-files-found: error` two steps later.
    Failing closed keeps the two consumers agreeing and preserves the shipped
    behaviour, which was already rc 1 (measured: the `Test-Path` check below,
    with a message naming no file).
    `test_a_blank_output_file_fails_closed_and_never_calls_the_script` and
    `test_the_pre_fix_body_also_refused_a_blank_output_file` pin both halves --
    the second is what makes "preserves the shipped behaviour" a measurement
    rather than a claim.
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
_spec = importlib.util.spec_from_file_location("interp_guard_218", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "218-whmcs-siteslist-reconciliation.yml"
JOB = "siteslist_reconciliation"
STEP = "Reconcile WHMCS with the sites-list"
ARTIFACT_STEP = "Upload reconciliation artifact"
CALLEE = "whmcs-siteslist-reconciliation.ps1"

MAPPINGS = {
    "IN_OUTPUT_FILE": "${{ inputs.output_file }}",
    "IN_CLOUDFLARE_PID": "${{ inputs.cloudflare_pid }}",
    "IN_GITHUB_PAGES_PID": "${{ inputs.github_pages_pid }}",
}
BURNED_INPUTS = ("output_file", "cloudflare_pid", "github_pages_pid")

# The pid variables the body default-fills, against the input whose DECLARED
# default the fill must reproduce. Read from `on.workflow_dispatch.inputs` rather
# than written down twice: the whole point of default-fill over fail-closed is
# that the body reproduces what the dispatch form promises, and a drift between
# the two is invisible on every ordinary dispatch — GitHub supplies the declared
# default and the in-body fill never runs.
DEFAULT_FILLS = {
    "IN_CLOUDFLARE_PID": "cloudflare_pid",
    "IN_GITHUB_PAGES_PID": "github_pages_pid",
}

LEGAL_OUT = "artifacts/whmcs/whmcs_siteslist_reconciliation.json"
LEGAL_CF = "39"
LEGAL_GH = "40"

SENTINEL = "STOLEN-218.txt"

# The variables that stand in for the workflow's own `env:` mapping. Each is
# REMOVED unless a test supplies it, so an inherited one cannot satisfy an
# assertion the workflow is supposed to (ledger L199).
CONTROLLED_VARS = (
    "IN_OUTPUT_FILE",
    "IN_CLOUDFLARE_PID",
    "IN_GITHUB_PAGES_PID",
)

# The decoys standing in for what `whmcs-secrets-from-kv` exports to GITHUB_ENV.
# The harness always sets these — they are the thing a payload is supposed to be
# able to reach, so clearing them would make the pre-fix control look harmless.
# That is not hypothetical: the first run of this module popped them along with
# the CONTROLLED_VARS above, and the controls reported `secret=` / `apim=`. The
# payload had executed correctly; only the credential it stole was blank. A test
# asserting the sentinel merely EXISTS would have gone green on that run and
# vouched for a control that proved nothing about credential reach — which is
# why the assertions below read its CONTENTS.
DECOY_CREDENTIALS = {
    "WHMCS_API_SECRET": "whmcs-secret-placeholder-not-a-real-credential",
    "WHMCS_APIM_SUBSCRIPTION_KEY": "apim-key-placeholder-not-a-real-credential",
}

# The payload that needs no quote breakout, for the double-quoted assignment.
# `$null =` swallows its output so `$out` keeps a legal value and the log reads
# as an ordinary run.
SUBEXPR_PAYLOAD = (
    "$(Write-Host 'INJECTED-218-SUBEXPR'; "
    "$null = Set-Content -Path " + SENTINEL + " -Value \"secret=$env:WHMCS_API_SECRET\"; "
    "'" + LEGAL_OUT + "')"
)
# The payload for a single-quoted argument: close the quote, run, comment out the
# stray one. On the TRAILING argument the legitimate call has already completed.
BREAKOUT_PAYLOAD = (
    LEGAL_GH + "'; "
    "$null = Set-Content -Path " + SENTINEL + " -Value \"apim=$env:WHMCS_APIM_SUBSCRIPTION_KEY\"; "
    "Write-Host 'INJECTED-218-BREAKOUT'; #"
)

# The three anchors the pre-fix control strips, each with the literal that ends
# its block, so the strip can find the closing brace without a bare `.index`.
GUARD_BLOCKS = (
    ("if ([string]::IsNullOrWhiteSpace($env:IN_CLOUDFLARE_PID)) {", "'39'"),
    ("if ([string]::IsNullOrWhiteSpace($env:IN_GITHUB_PAGES_PID)) {", "'40'"),
    ("if ([string]::IsNullOrWhiteSpace($env:IN_OUTPUT_FILE)) {", "throw"),
)

# The three shipped spellings the pre-fix control rewrites, and what each becomes
# when GitHub pastes the value in as raw text instead.
PRE_FIX_SITES = (
    ("$out = $env:IN_OUTPUT_FILE", '$out = "{}"'),
    ("-CloudflareProductPid $env:IN_CLOUDFLARE_PID", "-CloudflareProductPid '{}'"),
    ("-GithubPagesProductPid $env:IN_GITHUB_PAGES_PID", "-GithubPagesProductPid '{}'"),
)

# A stand-in for the real script that records what it was BOUND. That is the only
# discriminator that works here: a marker search over stdout is not one, because
# pwsh echoes offending source back in a binder error, so a substring predicate
# matches the payload text on a run that executed nothing.
STUB = """param(
    [Parameter()]
    [string]$ApiUrl,
    [Parameter()]
    [string]$OutputFile = 'artifacts/whmcs/whmcs_siteslist_reconciliation.json',
    [Parameter()]
    [string]$CloudflareProductPid = '39',
    [Parameter()]
    [string]$GithubPagesProductPid = '40'
)
[Console]::Error.WriteLine("CALLED Out=[$OutputFile] Cf=[$CloudflareProductPid] Gh=[$GithubPagesProductPid]")
if (-not [string]::IsNullOrWhiteSpace($OutputFile)) {
    $dir = Split-Path -Parent $OutputFile
    if ($dir) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    Set-Content -Path $OutputFile -Value '{"ok":true}'
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


def _declared_specs() -> dict:
    """The raw `on.workflow_dispatch.inputs` mapping, name -> spec dict.

    `on:` parses to the YAML 1.1 boolean `True` (the Norway problem), which is
    the trap `guard.dispatch_inputs` already solves — but that helper returns
    only each input's TYPE, so the defaults have to be read from the raw specs
    here. The `True`-or-`"on"` lookup is copied from it deliberately, and
    `test_the_in_body_pid_defaults_match_the_declared_defaults` fails loudly if
    this ever returns nothing, so the two readings cannot silently diverge.

    Every step is asserted rather than indexed. A KeyError or AttributeError out
    of a helper is NOT an AssertionError, so the module runner does not catch it
    — it aborts the whole roster, and every case sorted after the caller reports
    no outcome at all, which a reviewer counting FAIL lines scores as passing
    (ledger L194; measured on lane 22, and again on the first run of this module).
    """
    workflow = _workflow()
    on = workflow.get(True, workflow.get("on"))
    assert isinstance(on, dict), (
        f"{WORKFLOW} has no readable `on:` block (got {type(on).__name__}) — if "
        f"the YAML 1.1 boolean-key handling changed, fix it here and in "
        f"guard.dispatch_inputs together"
    )
    dispatch = on.get("workflow_dispatch")
    assert isinstance(dispatch, dict), (
        f"{WORKFLOW} declares no workflow_dispatch mapping: {dispatch!r}"
    )
    inputs = dispatch.get("inputs")
    assert isinstance(inputs, dict) and inputs, (
        f"{WORKFLOW} declares no dispatch inputs: {inputs!r}"
    )
    return inputs


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
    """Remove all three emptiness guards, asserting each was there first.

    Counts are asserted BEFORE substituting (ledger L47): an anchor that stopped
    matching must fail loudly rather than silently leave the body unchanged and
    score the control as a pass.

    Every lookup is guarded by an assertion rather than a bare `str.index`. A
    ValueError here is not caught by the module runner, so it would abort the
    roster and every case sorted after this one would report no outcome at all —
    which a reviewer counting FAIL lines scores as passing (ledger L194, measured
    on lane 22 through exactly this kind of helper).
    """
    for anchor, terminator in GUARD_BLOCKS:
        assert body.count(anchor) == 1, (
            f"expected exactly one {anchor!r} to strip, found "
            f"{body.count(anchor)} — this control would otherwise measure an "
            f"unmodified body. Body: {body!r}"
        )
        start = body.index(anchor)
        assert terminator in body[start:], (
            f"the guard at {anchor!r} no longer contains {terminator!r}, so this "
            f"control cannot locate its end. Body: {body!r}"
        )
        end = body.index("}", body.index(terminator, start)) + 1
        body = body[:start] + body[end:]
    # Count the ANCHOR, not the bare call name: the step body explains the guards
    # in comments directly above them, so an `"IsNullOrWhiteSpace" not in body`
    # check would fail on the PROSE describing the thing it looks for and report
    # the control as broken over a correct strip (#1019).
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


def _pre_fix(output_file: str, cf_pid: str, gh_pid: str) -> str:
    """The body as it shipped BEFORE this lane, with GitHub's substitution done.

    DERIVED from the current body rather than kept as a second copy, so the
    control cannot drift from what ships: only the three spellings the burn-down
    changed are rewritten, and the guards the burn-down added are stripped (they
    are part of the remedy — leaving them in would make the control refuse the
    payload for the reason under test rather than executing it).

    Each anchor's count is asserted before substituting (ledger L47).
    """
    body = _strip_guards(_body())
    for shipped, pre_fix_template in PRE_FIX_SITES:
        assert body.count(shipped) == 1, (
            f"expected exactly one {shipped!r} in the shipped body to rewrite; "
            f"found {body.count(shipped)}. If the wiring was renamed, update "
            f"PRE_FIX_SITES; if it was removed, that is the finding. "
            f"Body: {body!r}"
        )
        value = {
            "$out = $env:IN_OUTPUT_FILE": output_file,
            "-CloudflareProductPid $env:IN_CLOUDFLARE_PID": cf_pid,
            "-GithubPagesProductPid $env:IN_GITHUB_PAGES_PID": gh_pid,
        }[shipped]
        body = body.replace(shipped, pre_fix_template.format(value))
    assert "$env:IN_" not in body, (
        f"the control still reads a burn-down variable, so it is not measuring "
        f"the pre-fix body. Body: {body!r}"
    )
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


# --------------------------------------------------------------------------
# Wiring — pure YAML/AST, no tool required
# --------------------------------------------------------------------------


def test_all_three_inputs_travel_in_env_and_are_not_interpolated():
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
    """`$env:X in body` is too weak, because the default-fill guard reads it too.

    Found by this lane's own mutation review. Replacing
    `-GithubPagesProductPid $env:IN_GITHUB_PAGES_PID` with a hard-coded `'40'`
    leaves the variable read twice in the guard above, so the
    "step maps X but never reads it" assertion is satisfied by a body that fills
    the variable and then throws it away at the call site — the dispatcher's
    value reaching nothing, which is precisely the failure that assertion is
    worded to catch. The behavioural tests did catch the mutation, so the module
    was not blind; but the wiring test's message would have sent the reader to
    the wrong place.

    Every #1080 lane's module shares the wiring assertion's shape, and the two-
    part remedy (guard that fills + call site that uses) is what creates the
    hole, so this is a property of the pattern rather than of this workflow.
    Ledger L294.
    """
    body = _body()
    invocation = [ln for ln in body.splitlines() if CALLEE in ln and ln.lstrip().startswith("&")]
    assert len(invocation) == 1, (
        f"expected exactly one line invoking {CALLEE}; found {len(invocation)}. "
        f"Body: {body!r}"
    )
    line = invocation[0]
    for argument in (
        "-OutputFile $out",
        "-CloudflareProductPid $env:IN_CLOUDFLARE_PID",
        "-GithubPagesProductPid $env:IN_GITHUB_PAGES_PID",
    ):
        assert argument in line, (
            f"the callee invocation must pass {argument!r}, or the dispatched "
            f"value reaches nothing however faithfully the env: block maps it. "
            f"Invocation: {line!r}"
        )
    assert "$out = $env:IN_OUTPUT_FILE" in body, (
        f"`$out` must come from IN_OUTPUT_FILE — the invocation passes `$out`, so "
        f"a `$out` assigned from anything else silently ignores the input. "
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


def test_the_in_body_pid_defaults_match_the_declared_defaults():
    """A default-fill that drifts from the dispatch form is invisible in practice.

    GitHub supplies the declared default whenever the input is OMITTED, so the
    in-body fill only ever runs for an explicitly-blanked value — which means a
    drift between the two would never show up on an ordinary dispatch.
    """
    declared = _declared_specs()
    body = _body()
    for var, input_name in DEFAULT_FILLS.items():
        spec = declared.get(input_name)
        assert isinstance(spec, dict), (
            f"{input_name} is no longer a declared dispatch input with a spec "
            f"(got {spec!r}), so the body's default-fill for {var} has nothing "
            f"to agree with"
        )
        want = str(spec.get("default", ""))
        assert want, (
            f"{input_name} no longer declares a default, so default-fill is the "
            f"wrong remedy for {var} — it should fail closed instead (L254)"
        )
        assignment = f"$env:{var} = '{want}'"
        assert assignment in body, (
            f"the body must fill {var} with the declared default {want!r} "
            f"(expected {assignment!r}); it has drifted from "
            f"on.workflow_dispatch.inputs.{input_name}.default. Body: {body!r}"
        )


def test_output_file_fails_closed_because_the_artifact_step_shares_the_input():
    """The reason `output_file` is the exception, pinned to the thing that causes it.

    If the artifact step ever stops consuming the raw input, this test should be
    revisited rather than deleted: the exception exists because there are two
    consumers, so the test asserts the second consumer still exists.
    """
    artifact = find_step(_workflow(), JOB, ARTIFACT_STEP)
    path = str((artifact.get("with") or {}).get("path", ""))
    assert "inputs.output_file" in path, (
        f"the {ARTIFACT_STEP!r} step no longer consumes output_file directly "
        f"(path: {path!r}). That was the whole reason the reconcile step fails "
        f"closed instead of default-filling — re-derive the remedy rather than "
        f"leaving this comment and guard in place unexamined."
    )
    body = _body()
    assert "if ([string]::IsNullOrWhiteSpace($env:IN_OUTPUT_FILE)) {" in body, (
        f"the fail-closed guard on IN_OUTPUT_FILE is gone. Body: {body!r}"
    )
    fill = "$env:IN_OUTPUT_FILE = '"
    assert fill not in body, (
        f"IN_OUTPUT_FILE is being default-filled, which desyncs this step from "
        f"the {ARTIFACT_STEP!r} step's path:. Body: {body!r}"
    )


def test_the_api_url_wiring_is_untouched():
    """This lane must not disturb a neighbouring module's fixture.

    `test_1146_empty_input_argument_binding.py` and `test_1150_empty_input_guard.py`
    both pin this step by name for `WHMCS_API_URL`. They are asserted here too so
    a break shows up in the lane that caused it rather than two modules away.
    """
    step = _step()
    env = step.get("env") or {}
    assert env.get("WHMCS_API_URL") == "${{ inputs.api_url }}", (
        f"the api_url mapping changed: {env!r}"
    )
    body = step["run"]
    assert "if ([string]::IsNullOrWhiteSpace($env:WHMCS_API_URL)) {" in body, (
        f"api_url's own default guard is gone. Body: {body!r}"
    )
    assert "-ApiUrl $env:WHMCS_API_URL" in body, (
        f"the callee no longer receives ApiUrl from the environment. Body: {body!r}"
    )


def test_the_job_still_enters_only_a_read_environment():
    """#1080's write half is done; a lane must not quietly re-gate a read lane.

    A payload in a frozen entry runs under a `-read` credential, and that is the
    property the freeze's `0 write` count reports. Re-pointing this job at a
    write environment would change the blast radius of every remaining finding
    in the file.
    """
    job = _workflow()["jobs"][JOB]
    environment = job.get("environment")
    names = (
        [environment]
        if isinstance(environment, str)
        else [environment.get("name")]
        if isinstance(environment, dict)
        else []
    )
    assert names and all(n and n.endswith("-read") for n in names), (
        f"job {JOB!r} must stay on a read-only environment; found {environment!r}"
    )


def test_the_guard_anchors_this_module_strips_are_really_in_the_body():
    """The strip helper is only as good as its anchors, so check them directly.

    Without this, a renamed guard would be caught only inside `_strip_guards`,
    where the failure reads as a broken control rather than as the workflow
    having changed.
    """
    body = _body()
    for anchor, terminator in GUARD_BLOCKS:
        assert body.count(anchor) == 1, (
            f"expected exactly one {anchor!r} in the shipped body; found "
            f"{body.count(anchor)}"
        )
        assert terminator in body[body.index(anchor):], (
            f"the block at {anchor!r} no longer contains {terminator!r}"
        )


# --------------------------------------------------------------------------
# Behaviour — the pre-fix controls
# --------------------------------------------------------------------------


def test_the_pre_fix_body_ran_the_subexpression_payload_and_exited_zero():
    """The double-quoted site: `$( )` expands, no quote breakout needed.

    This is the positive control. Without it, "the fixed body does not execute
    the payload" is a claim about a body that might never have executed anything.
    """
    test_all_three_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, rc = _run(_pre_fix(SUBEXPR_PAYLOAD, LEGAL_CF, LEGAL_GH))
    assert stolen is not None, (
        f"the pre-fix control did not execute its payload, so every 'the fixed "
        f"body is inert' test below is unanchored. Output: {out!r}"
    )
    assert DECOY_CREDENTIALS["WHMCS_API_SECRET"] in stolen, (
        f"the payload ran but did not reach the job's credential, so the control "
        f"understates the pre-fix hazard. Sentinel: {stolen!r}"
    )
    assert rc == 0, (
        f"the pre-fix injection is supposed to be INVISIBLE — exit 0 with the "
        f"legitimate call still made. rc={rc}. Output: {out!r}"
    )
    assert f"Out=[{LEGAL_OUT}]" in _bound(out), (
        f"the callee should still have been handed a legal path, which is what "
        f"makes the run indistinguishable from an ordinary one. "
        f"Bound: {_bound(out)!r}"
    )


def test_the_pre_fix_body_ran_the_breakout_payload_and_exited_zero():
    """The single-quoted trailing argument: a quote breakout is all it needs.

    The legitimate call completes with all four arguments BEFORE the payload
    runs, so `$LASTEXITCODE` and the step's own `Test-Path` check both pass.
    """
    test_all_three_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, rc = _run(_pre_fix(LEGAL_OUT, LEGAL_CF, BREAKOUT_PAYLOAD))
    assert stolen is not None, (
        f"the breakout control did not execute its payload. Output: {out!r}"
    )
    assert DECOY_CREDENTIALS["WHMCS_APIM_SUBSCRIPTION_KEY"] in stolen, (
        f"the payload ran but did not reach the APIM subscription key. "
        f"Sentinel: {stolen!r}"
    )
    assert rc == 0, f"expected an invisible exit 0; rc={rc}. Output: {out!r}"
    assert f"Gh=[{LEGAL_GH}]" in _bound(out), (
        f"the legitimate call should have bound the real pid before the payload "
        f"ran. Bound: {_bound(out)!r}"
    )


def test_the_pre_fix_body_also_refused_a_blank_output_file():
    """`output_file`'s fail-closed remedy preserves behaviour rather than adding it.

    This is what makes the docstring's "preserves the shipped behaviour" a
    measurement. If this ever passes rc 0, the fail-closed guard is introducing a
    new failure mode instead of keeping an existing one, and the remedy should be
    reconsidered rather than the test relaxed.
    """
    out, stolen, rc = _run(_pre_fix("", LEGAL_CF, LEGAL_GH))
    assert stolen is None, f"no payload was supplied, yet the sentinel exists: {stolen!r}"
    assert rc != 0, (
        f"a blank output_file exited 0 in the pre-fix body, so failing closed is "
        f"a behaviour CHANGE, not a preservation. Output: {out!r}"
    )
    # A non-zero rc alone cannot tell the body apart from a broken harness
    # (CLAUDE.md), so pin what was actually said.
    assert "Expected output file not found" in out, (
        f"the pre-fix body exited non-zero for some reason other than its own "
        f"Test-Path check, so this control is measuring the harness. Output: {out!r}"
    )


def test_without_the_guard_an_empty_pid_binds_empty_at_exit_zero():
    """Ledger L291's question, answered for this body: the guard is load-bearing.

    Strip the default-fill and an empty pid binds as an empty string at exit 0 —
    silent, and `.Contains('')` is false for every client, so the artifact
    becomes a maximal false Phase 2 work-list. Note this is measured on the
    PRE-FIX body too, which is what makes this a third case rather than L291
    repeating: the hazard predates the move to `env:` here.
    """
    out, _, rc = _run(_pre_fix(LEGAL_OUT, "", ""))
    assert rc == 0, (
        f"expected the unguarded blank to be SILENT (exit 0) — if it is loud, "
        f"the default-fill is attribution rather than safety and the docstring "
        f"above overstates it. rc={rc}. Output: {out!r}"
    )
    assert "Cf=[] Gh=[]" in _bound(out), (
        f"expected both pids to bind empty without the guard. "
        f"Bound: {_bound(out)!r}"
    )


# --------------------------------------------------------------------------
# Behaviour — the body this lane ships
# --------------------------------------------------------------------------


def test_the_shipped_body_binds_the_subexpression_payload_as_data():
    test_all_three_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, _ = _run(
        _body(),
        IN_OUTPUT_FILE=SUBEXPR_PAYLOAD,
        IN_CLOUDFLARE_PID=LEGAL_CF,
        IN_GITHUB_PAGES_PID=LEGAL_GH,
    )
    assert stolen is None, (
        f"the payload EXECUTED through the shipped body — it reached "
        f"{stolen!r}. The env: mapping is not doing its job."
    )
    # The callee must have been handed the payload verbatim, as ONE argument.
    # Without this the test would pass just as well on a body that dropped the
    # value entirely, which is not the property being claimed.
    assert "INJECTED-218-SUBEXPR" in _bound(out), (
        f"the payload text did not reach the callee as data, so this test cannot "
        f"tell 'passed through inertly' from 'never passed at all'. "
        f"Bound: {_bound(out)!r}"
    )


def test_the_shipped_body_binds_the_breakout_payload_as_data():
    test_all_three_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, rc = _run(
        _body(),
        IN_OUTPUT_FILE=LEGAL_OUT,
        IN_CLOUDFLARE_PID=LEGAL_CF,
        IN_GITHUB_PAGES_PID=BREAKOUT_PAYLOAD,
    )
    assert stolen is None, (
        f"the breakout payload EXECUTED through the shipped body — it reached "
        f"{stolen!r}"
    )
    assert "INJECTED-218-BREAKOUT" in _bound(out), (
        f"the payload did not reach the callee as data. Bound: {_bound(out)!r}"
    )
    assert rc == 0, (
        f"an inert string argument should not fail the step; rc={rc}. "
        f"Output: {out!r}"
    )


def test_the_shipped_body_passes_ordinary_values_through():
    """The negative control: the remedy must not break the normal dispatch."""
    test_all_three_inputs_travel_in_env_and_are_not_interpolated()
    out, stolen, rc = _run(
        _body(),
        IN_OUTPUT_FILE=LEGAL_OUT,
        IN_CLOUDFLARE_PID=LEGAL_CF,
        IN_GITHUB_PAGES_PID=LEGAL_GH,
    )
    assert stolen is None, f"a benign run wrote the sentinel: {stolen!r}"
    assert rc == 0, f"a benign run failed; rc={rc}. Output: {out!r}"
    assert _bound(out) == f"CALLED Out=[{LEGAL_OUT}] Cf=[{LEGAL_CF}] Gh=[{LEGAL_GH}]", (
        f"the callee did not receive the dispatched values verbatim. "
        f"Bound: {_bound(out)!r}"
    )


def test_an_empty_pid_falls_back_to_the_declared_default():
    test_all_three_inputs_travel_in_env_and_are_not_interpolated()
    out, _, rc = _run(
        _body(),
        IN_OUTPUT_FILE=LEGAL_OUT,
        IN_CLOUDFLARE_PID="",
        IN_GITHUB_PAGES_PID="",
    )
    assert rc == 0, f"the default-fill did not rescue an empty pid; rc={rc}. {out!r}"
    assert f"Cf=[{LEGAL_CF}] Gh=[{LEGAL_GH}]" in _bound(out), (
        f"expected the declared defaults to be bound. Bound: {_bound(out)!r}"
    )


def test_an_unset_pid_falls_back_to_the_declared_default():
    """The other blank form. It is a separate test because the two differ.

    On an unset variable `$env:X` is `$null` rather than `''`, and the two have
    reached different outcomes in this burn-down before (ledger L291) — so they
    are measured separately rather than assumed equivalent.
    """
    test_all_three_inputs_travel_in_env_and_are_not_interpolated()
    out, _, rc = _run(
        _body(),
        IN_OUTPUT_FILE=LEGAL_OUT,
        IN_CLOUDFLARE_PID=None,
        IN_GITHUB_PAGES_PID=None,
    )
    assert rc == 0, f"the default-fill did not rescue an unset pid; rc={rc}. {out!r}"
    assert f"Cf=[{LEGAL_CF}] Gh=[{LEGAL_GH}]" in _bound(out), (
        f"expected the declared defaults to be bound. Bound: {_bound(out)!r}"
    )


def test_a_blank_output_file_fails_closed_and_never_calls_the_script():
    test_all_three_inputs_travel_in_env_and_are_not_interpolated()
    out, _, rc = _run(
        _body(),
        IN_OUTPUT_FILE="",
        IN_CLOUDFLARE_PID=LEGAL_CF,
        IN_GITHUB_PAGES_PID=LEGAL_GH,
    )
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
    test_all_three_inputs_travel_in_env_and_are_not_interpolated()
    out, _, rc = _run(
        _body(),
        IN_OUTPUT_FILE=None,
        IN_CLOUDFLARE_PID=LEGAL_CF,
        IN_GITHUB_PAGES_PID=LEGAL_GH,
    )
    assert rc != 0, f"an unset output_file should fail closed; rc={rc}. {out!r}"
    assert "output_file is blank" in out, (
        f"the step exited non-zero without naming the cause. Output: {out!r}"
    )
    assert _bound(out) == "", (
        f"the callee ran despite the missing path. Bound: {_bound(out)!r}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a subprocess; the wiring and checker-agreement
# cases are pure YAML/AST and must run on a host without pwsh installed (#1182 —
# a whole-module `shutil.which` gate turns "could not run" into "everything
# passed"). This set is scoped to exactly the cases that call `_run`.
NEEDS_PWSH = {
    "test_the_pre_fix_body_ran_the_subexpression_payload_and_exited_zero",
    "test_the_pre_fix_body_ran_the_breakout_payload_and_exited_zero",
    "test_the_pre_fix_body_also_refused_a_blank_output_file",
    "test_without_the_guard_an_empty_pid_binds_empty_at_exit_zero",
    "test_the_shipped_body_binds_the_subexpression_payload_as_data",
    "test_the_shipped_body_binds_the_breakout_payload_as_data",
    "test_the_shipped_body_passes_ordinary_values_through",
    "test_an_empty_pid_falls_back_to_the_declared_default",
    "test_an_unset_pid_falls_back_to_the_declared_default",
    "test_a_blank_output_file_fails_closed_and_never_calls_the_script",
    "test_an_unset_output_file_fails_closed_too",
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
