"""Unit tests for 229's four dispatch-input call sites (#1080 burn-down).

229 copies a WHMCS client's product custom-field answers onto their client
profile, under the gated `whmcs-prod` environment whose live mode is an
UpdateClient write. Its two free-text dispatch inputs, `client_id` and `email`,
used to be interpolated into the single pwsh body TWICE EACH — once into the
emptiness test and once into the hashtable the splat is built from. All four
sites now read `$env:IN_CLIENT_ID` / `$env:IN_EMAIL`.

TWO FREEZE NAMES, FOUR SUBSTITUTION POINTS
    `KNOWN_UNGUARDED` records an input NAME per workflow, not a count, so
    `("client_id", "email")` was two entries covering four sites — and both
    duplicates sat on the SAME LINE as their partner, which is the spelling a
    hand count is likeliest to read as one. `test_the_body_has_no_remaining_
    interpolation` is therefore written over the checker's own patterns rather
    than over a number this module chose (the L255/116 lesson, one level down).

WHERE THE CREDENTIAL SAT — INVISIBLE TO BOTH RECOMMENDED SWEEPS
    The WHMCS API credential (`WHMCS_API_IDENTIFIER` / `WHMCS_API_SECRET` /
    `WHMCS_APIM_SUBSCRIPTION_KEY`) is minted by the PRECEDING step,
    `uses: ./.github/actions/whmcs-secrets-from-kv` with `scope: write`, which
    exports it through GITHUB_ENV. It is therefore in the process environment of
    the injection point while appearing in no `env:` block in this file and
    matching no `secrets.` reference, so an L213 `env:` read and the #1141
    `secrets.` grep both score the file as holding nothing. It is also the
    workflow's ONLY credential.

THE INJECTION POINT WAS THE VALIDATION
    This is what distinguishes 229 from its neighbours in the freeze. The first
    substitution of each pair sat inside

        if (-not [string]::IsNullOrWhiteSpace('${{ inputs.client_id }}')) { … }

    the emptiness test #1213 added specifically to refuse a blank. So the check
    whose job is to reject bad input was the first place the dispatcher's code
    ran, one line above the splat — and a reviewer triaging the file for an
    unvalidated input finds a validated one. Same shape as ledger L206, where
    112's callee-side `[ValidatePattern]` ran after the payload it appeared to
    constrain; here the validation is not merely downstream of the injection, it
    IS the injection site.

SINGLE QUOTES, SO THE USUAL PAYLOAD IS INERT
    pwsh does not expand `$( )` inside single quotes, so the suffix payload the
    double-quoted lanes (116, 702) used reports this body as harmless. The
    breakout that works closes the literal and continues the EXPRESSION, which
    is legal in both positions the value lands in — a method argument and an
    assignment RHS:

        client_id = 419' + $($null = Set-Content -Path <sentinel> -Value $env:WHMCS_API_SECRET) + '

    which renders as `'419' + $(…) + ''` and evaluates to `419`, because `$null =`
    swallows the subexpression's output and `'419' + $null` is `'419'`. Measured
    against the body as it shipped, on pwsh 7.4.6, for EACH input separately:

        * the WHMCS API secret was written to the sentinel file;
        * the callee was still invoked with `ClientId=[419]`,
          `Email=[a@example.org]` — a legal, ordinary-looking call;
        * the step exited **0**, under `whmcs-prod`.

THE REMEDY IS A GATED APPEND, NOT A FAIL-CLOSED GUARD — AND THAT IS MEASURED
    Every fail-closed lane (118, 116, 205) exits 1 on a blank. 229 must not: a
    blank `client_id` is its DOCUMENTED path ("leave blank to use email"), so
    exiting 1 on empty would break the common dispatch. It is the 119 case.

    What makes the gated append load-bearing rather than cosmetic is the splat
    target: the hashtable goes to a NATIVE command (`pwsh -File`), whose argument
    order is undefined (ledger L254). With both predicates replaced by
    unconditional assignments, `client_id` empty and `email` supplied, 8 runs on
    pwsh 7.4.6 produced FOUR outcomes, every one at exit 0:

        2/8   ClientId=[]                              correct
        2/8   ClientId=[-Email:a@example.org]  Email=[]
        2/8   ClientId=[-MapJson:config/…]
        2/8   ClientId=[-OutputFile:artifacts/…]

    Note WHICH blank that is. An UNSET mapping is dropped from the rendering
    entirely and was correct 8/8; it is the EMPTY one that shifts — i.e. the
    blank the dispatch form produces by default, on the workflow's primary use
    case, not the one a broken `env:` block produces. That is the opposite of the
    intuition the other lanes build, and it is why
    `test_without_the_predicates_an_empty_client_id_shifts_the_binding` runs the
    case repeatedly: a single green run of a nondeterministic defect is not
    evidence of absence. Ledger L271.
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

WORKFLOW = "229-whmcs-client-field-populate.yml"
JOB = "populate"
STEP = "Populate client fields from product answers"
ENVIRONMENT = "whmcs-prod"
CALLEE = "whmcs-client-field-populate.ps1"

# The env vars the moved inputs must travel in, and the expressions they map from.
MAPPINGS = {
    "IN_CLIENT_ID": "${{ inputs.client_id }}",
    "IN_EMAIL": "${{ inputs.email }}",
}
INPUT_NAMES = ("client_id", "email")

# The credential in reach of the injection point, and the step that puts it there.
# That step is a bare `uses:` with no `name:`, so it is located by its `uses:`
# value rather than through find_step (which matches on name).
TOKEN_VAR = "WHMCS_API_SECRET"
TOKEN_STEP_USES = "./.github/actions/whmcs-secrets-from-kv"
CREDENTIAL_VARS = (
    "WHMCS_API_SECRET",
    "WHMCS_API_IDENTIFIER",
    "WHMCS_APIM_SUBSCRIPTION_KEY",
)

# Deliberately NOT shaped like a real secret: a value a scanner treats as a
# credential comes back REDACTED, and the one place it is printed is an assertion
# message on a failing run — exactly when the reader needs to see whether the
# sentinel holds the credential or an empty string.
FAKE_TOKEN = "whmcs-secret-placeholder-not-a-real-token"
SENTINEL = "STOLEN-229.txt"

LEGAL_CLIENT_ID = "419"
LEGAL_EMAIL = "a@example.org"

# The body as it shipped BEFORE the burn-down, verbatim from origin/main :60-75,
# with ALL FOUR substitution points marked. The booleans are rendered to the
# literals GitHub substitutes because GitHub would. It is the positive control:
# without it, "the fixed body does not execute the payload" is a claim about a
# body that might never have executed anything.
PRE_FIX_BODY = """$ErrorActionPreference = 'Stop'
$params = @{
  ApiUrl     = $env:WHMCS_API_URL
  MapJson    = 'config/whmcs-client-field-populate-map.json'
  OutputFile = 'artifacts/whmcs/whmcs_client_field_populate.json'
}
if (-not [string]::IsNullOrWhiteSpace('CLIENT_ID_HERE')) { $params.ClientId = 'CLIENT_ID_HERE' }
if (-not [string]::IsNullOrWhiteSpace('EMAIL_HERE')) { $params.Email = 'EMAIL_HERE' }
if ('false' -eq 'true') { $params.Overwrite = $true }
if ('true' -eq 'false') { $params.Execute = $true }
& pwsh -NoProfile -File ./scripts/whmcs-client-field-populate.ps1 @params | Tee-Object -FilePath $env:GITHUB_STEP_SUMMARY -Append
if ($LASTEXITCODE -ne 0) { throw "Client-field populate failed with exit code $LASTEXITCODE." }
"""

# A stand-in for the real script that records what it was BOUND, which is the only
# discriminator that works here — a marker-string search over stdout is not one,
# because pwsh echoes the offending source line back in a ParserError, so any
# substring predicate matches the payload text on a run that executed nothing.
#
# The `throw` on both-blank is the real script's own precondition
# (scripts/whmcs-client-field-populate.ps1:157-158), kept because it is what makes
# the both-blank case fail loudly rather than silently — a fact this module
# asserts rather than the workflow's predicates taking credit for it.
STUB = """[CmdletBinding()]
param(
    [Parameter()][string]$ClientId,
    [Parameter()][string]$Email,
    [Parameter()][string]$MapJson = 'config/whmcs-client-field-populate-map.json',
    [Parameter()][string]$OutputFile,
    [Parameter()][string]$ApiUrl,
    [Parameter()][switch]$Overwrite,
    [Parameter()][switch]$Execute
)
if ([string]::IsNullOrWhiteSpace($ClientId) -and [string]::IsNullOrWhiteSpace($Email)) {
    throw 'Provide -ClientId or -Email to identify the target client.'
}
Write-Output "CALLED ClientId=[$ClientId] Email=[$Email] Overwrite=[$Overwrite] Execute=[$Execute]"
"""

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that omits
# it pins the wrong exit code and vouches for it (recorded on #1080's lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

# The interpolations the burn-down deliberately LEAVES in the body, and what GitHub
# substitutes for them. Both are `type: boolean`, so GitHub generates the value and
# neither can carry a payload — which is exactly why they stay. Rendering them is
# what makes a run of the shipped body a run of what actually ships.
CONSTRAINED_RENDERINGS = {
    "${{ inputs.overwrite }}": "false",
    "${{ inputs.dry_run }}": "true",
}

# Every variable the harness must own outright: an inherited one could satisfy an
# assertion the workflow is supposed to.
CONTROLLED_VARS = ("IN_CLIENT_ID", "IN_EMAIL", TOKEN_VAR)

# The two gated appends, as anchors. Stripping them is how the L254 control is
# built, and the count is asserted before substituting (ledger L47).
PREDICATES = (
    (
        "if (-not [string]::IsNullOrWhiteSpace($env:IN_CLIENT_ID)) "
        "{ $params.ClientId = $env:IN_CLIENT_ID }",
        "$params.ClientId = $env:IN_CLIENT_ID",
    ),
    (
        "if (-not [string]::IsNullOrWhiteSpace($env:IN_EMAIL)) "
        "{ $params.Email = $env:IN_EMAIL }",
        "$params.Email = $env:IN_EMAIL",
    ),
)

# How many times the nondeterministic L254 shift is sampled. 8 is the figure 118's
# lane used, and it is a sample size rather than a proof: the assertion is that at
# least one run binds wrong, so more runs can only help it.
SHIFT_RUNS = 8


def _payload(legal: str) -> str:
    """The payload a SINGLE-quoted site takes: close the literal, concatenate.

    `$( )` does not expand in single quotes, so the suffix payload the
    double-quoted lanes use is inert here — which is the trap, because it reports
    this body as safe. Closing the literal and continuing with `+ $(…) + '`
    leaves a legal expression in BOTH positions the value lands in, and `$null =`
    keeps the resulting value equal to the legal input, so nothing in the log
    looks unusual.
    """
    return (
        legal
        + "' + $($null = Set-Content -Path "
        + SENTINEL
        + " -Value $env:"
        + TOKEN_VAR
        + ") + '"
    )


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input this body reaches through a `${{ }}` expression.

    Deliberately the CHECKER's own two patterns rather than a substring test of
    this module's own devising, so a spelling the checker recognises
    (`${{ inputs . client_id }}`) cannot slip past the step-level assertion, and
    the two cannot drift apart the way a restated rule does.
    """
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    return found


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


def _rendered(body: str) -> str:
    """The shipped body with GitHub's substitution performed, as GitHub does it.

    Running the RAW body instead is a silent trap: the literal text
    `'${{ inputs.dry_run }}'` is not `'true'`, so a test asserting the ordinary
    path measures a body GitHub never runs. Every `${{ }}` left must be one of the
    two constrained survivors — a new one means an input was re-interpolated, or a
    constrained input was renamed, and either way the body being executed is no
    longer the body that ships.
    """
    remaining = set(guard._EXPRESSION.findall(body))
    unknown = {
        e.strip() for e in remaining if f"${{{{{e}}}}}" not in CONSTRAINED_RENDERINGS
    }
    assert not unknown, (
        f"the step body interpolates {sorted(unknown)}, which this module does not "
        f"know how to render. If that is a free-text dispatch input it is a #1080 "
        f"finding; if it is a new constrained one, add it to "
        f"CONSTRAINED_RENDERINGS. Body: {body!r}"
    )
    for expression, value in CONSTRAINED_RENDERINGS.items():
        assert body.count(expression) == 1, (
            f"expected exactly one {expression} to render, found "
            f"{body.count(expression)} — this substitution would otherwise leave "
            f"the body unrendered and the run would measure something else "
            f"(ledger L47). Body: {body!r}"
        )
        body = body.replace(expression, value)
    assert "${{" not in body, f"substitution left an expression behind: {body!r}"
    return body


def _pre_fix(client_id: str, email: str) -> str:
    """The pre-fix body with GitHub's substitution performed, at ALL FOUR sites.

    The counts are asserted before substituting (ledger L47): a `.replace` that
    stopped matching would leave the markers in place and the control would
    measure an unexploited body while reporting that the payload did not run. Two
    each is the number that matters — it is the fact the freeze entry's
    `("client_id", "email")` does not record.
    """
    for marker, expected in (("CLIENT_ID_HERE", 2), ("EMAIL_HERE", 2)):
        assert PRE_FIX_BODY.count(marker) == expected, (
            f"the pre-fix body must carry BOTH {marker} call sites; found "
            f"{PRE_FIX_BODY.count(marker)}"
        )
    rendered = PRE_FIX_BODY.replace("CLIENT_ID_HERE", client_id)
    rendered = rendered.replace("EMAIL_HERE", email)
    assert "_HERE" not in rendered, f"substitution left a marker behind: {rendered!r}"
    return rendered


def _strip_predicates(body: str) -> str:
    """Replace both gated appends with unconditional assignments.

    This is the L254 control, and it is the shape a lane "simplifying" the
    remedy would land. Each anchor's count is asserted BEFORE substituting
    (ledger L47), so a refactor that reworded a predicate fails loudly here
    instead of silently leaving the body unchanged and scoring the control as a
    pass — which, for a control that expects to see a DEFECT, would read as
    reassurance.
    """
    stripped = body
    for guarded, unguarded in PREDICATES:
        assert stripped.count(guarded) == 1, (
            f"expected exactly one occurrence of the gated append {guarded!r}, "
            f"found {stripped.count(guarded)} — this control cannot be applied, "
            f"so it would otherwise measure an unmodified body. Body: {body!r}"
        )
        stripped = stripped.replace(guarded, unguarded)
    # Count the ANCHOR, not the bare call name: the body explains the predicates in
    # a comment directly above them, so an `"IsNullOrWhiteSpace" not in stripped`
    # check fails on the PROSE describing the thing it is looking for and reports
    # the control as broken over a correct strip (#1019).
    for guarded, _ in PREDICATES:
        assert stripped.count(guarded) == 0, (
            f"a predicate survived the strip, so the control is measuring the "
            f"guarded body. Stripped: {stripped!r}"
        )
    assert CALLEE in stripped, (
        f"the strip removed the invocation itself, so the control proves nothing. "
        f"Stripped: {stripped!r}"
    )
    return stripped


def _run(body: str, **env_overrides: str):
    """Run a pwsh body the way the RUNNER runs it, in a temp cwd holding the stub.

    Returns (output, sentinel_contents_or_None, rc). The sentinel's CONTENTS, not
    merely its existence: a file written from an unset variable would score the
    same as one written from the live credential, and the claim under test is
    which credential the payload reached.

    `stdin=DEVNULL` is load-bearing, not hygiene. A parameter that ends up
    unsatisfied makes PowerShell PROMPT for it, and on an interactive stdin the
    call blocks forever. A runner's stdin is not a terminal, so DEVNULL is also
    the faithful shape.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / CALLEE).write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8")
        # The body's last pipeline appends to the job summary. Unset, that is
        # `Tee-Object -FilePath $null` under `ErrorActionPreference = 'Stop'`, so
        # every behavioural test would fail on the harness rather than on the body.
        summary = tmp / "step-summary.md"
        env = child_env(
            GITHUB_STEP_SUMMARY=str(summary),
            WHMCS_API_URL="https://apim-ffc-gateway-prod.azure-api.net/whmcs/api.php",
            **env_overrides,
        )
        # Only what the test sets may be visible: an inherited IN_CLIENT_ID would
        # make the blank cases pass for the wrong reason, and an inherited token
        # would let a theft assertion pass without the workflow supplying anything.
        for var in CONTROLLED_VARS:
            if var not in env_overrides:
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


def _called_line(output: str) -> str:
    return next((l.strip() for l in output.splitlines() if "CALLED" in l), "")


def _assert_wiring(step: dict) -> None:
    """Each input travels in env, the body reads it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES these variables (ledger L199): delete the
    workflow's `env:` mapping and the step still sees them from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists.
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


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_both_inputs_are_wired_through_env():
    _assert_wiring(_step())


def test_the_body_has_no_remaining_interpolation():
    """Written over the CHECKER's patterns, not over a count this module chose.

    The freeze entry recorded two input NAMES for four substitution points, and
    each duplicate shared a line with its partner — the spelling a hand count
    reads as one. So the assertion is "no reference at all survives", derived
    from the same regexes the guard uses.
    """
    body = _step().get("run", "")
    assert not _interpolated_inputs(body) & set(INPUT_NAMES), (
        f"a free-text input reference survives in the body: {body!r}"
    )


def test_both_gated_appends_read_the_env_var_exactly_once_each():
    """Four sites became four `$env:` reads — two per input, both predicates kept.

    A lane that fixed only the splat and left the emptiness test interpolated
    would satisfy `test_the_body_has_no_remaining_interpolation` for one input
    and not the other, so this pins the shape rather than the absence: each
    input is read in a condition AND in the assignment it guards.
    """
    body = _step().get("run", "")
    for guarded, _ in PREDICATES:
        assert body.count(guarded) == 1, (
            f"expected the gated append {guarded!r} exactly once in the body, "
            f"found {body.count(guarded)} — the remedy's shape has changed and "
            f"the L254 control below can no longer be applied. Body: {body!r}"
        )


def test_the_step_sits_in_the_gated_whmcs_job():
    """The environment is the whole argument: a dispatcher supplies code that
    runs AFTER an approver spends the credential."""
    workflow = load_workflow(WORKFLOW)
    job = workflow["jobs"][JOB]
    assert job.get("environment") == ENVIRONMENT, (
        f"job {JOB!r} must declare environment {ENVIRONMENT!r} — found "
        f"{job.get('environment')!r}. If the gate moved, #1080's argument for "
        f"this workflow changed and this module's premise needs rereading."
    )
    assert guard.is_write_environment(ENVIRONMENT), (
        f"{ENVIRONMENT} is no longer classified as a write environment by the "
        f"guard, so the [W] count this lane decremented is wrong"
    )


def test_the_moved_inputs_are_free_text_and_the_others_are_not():
    """`client_id`/`email` are free text; `overwrite`/`dry_run` are constrained.

    The second half is the load-bearing one: those two stay interpolated, and
    the ONLY thing that makes that safe is their declared type. Pinning the
    declaration is what stops a later edit from turning either into a string and
    silently reopening the hole this lane closed, with the body unchanged.
    """
    workflow = load_workflow(WORKFLOW)
    free_text = guard.free_text_inputs(workflow)
    for name in INPUT_NAMES:
        assert name in free_text, (
            f"{name} is no longer a free-text input, so this module is measuring "
            f"the wrong thing: {sorted(free_text)}"
        )
    # Read through the guard's own accessor, not `workflow["on"]`: after
    # yaml.safe_load, `on:` is the YAML 1.1 boolean True (the Norway problem), so
    # the obvious spelling raises KeyError — and a version that swallowed it would
    # report every input as absent and pass by inspecting nothing.
    declared = guard.dispatch_inputs(workflow)
    for name in ("overwrite", "dry_run"):
        assert declared.get(name) == "boolean", (
            f"{name} is interpolated into the body and is only safe because "
            f"GitHub constrains it; its declared type is "
            f"{declared.get(name)!r}, not 'boolean'"
        )
        assert name not in free_text, f"{name} is now free text and interpolated"


def test_the_whmcs_credential_reaches_the_step_only_through_the_kv_action():
    """The credential in reach is invisible to both recommended sweeps.

    Pinned rather than narrated, because the two cheap checks a reviewer runs —
    read the step's own `env:` (ledger L213), grep the file for `secrets.`
    (#1141) — both score this file as holding nothing, and it holds the WHMCS API
    credential.
    """
    workflow = load_workflow(WORKFLOW)
    step = _step()
    step_env = step.get("env") or {}
    for var in CREDENTIAL_VARS:
        assert var not in step_env, (
            f"{var} now appears in the injection point's own env: — the L213 read "
            f"would see it, and this test's premise is that it does not"
        )
    raw = (
        pathlib.Path(__file__).resolve().parents[2]
        / ".github"
        / "workflows"
        / WORKFLOW
    ).read_text(encoding="utf-8")
    # `${{ secrets.`, not the bare substring `secrets.`. The step's own comment
    # EXPLAINS that a `secrets.` grep reads this file as clean, so a substring test
    # fails on the prose describing the thing it is looking for and reports the
    # premise as broken over a correct file (#1019 — the same trap this module
    # avoids in _strip_predicates, reached one test over). What #1141 is about is an
    # expression reference, which is the spelling asserted here.
    assert "${{ secrets." not in raw, (
        "the file now references secrets.* in an expression, so the #1141 grep "
        "would no longer read it as clean and this test's premise has changed"
    )
    uses = [s.get("uses") for s in workflow["jobs"][JOB]["steps"]]
    assert TOKEN_STEP_USES in uses, (
        f"the Key Vault step {TOKEN_STEP_USES} is gone from job {JOB!r}, so the "
        f"credential no longer arrives the way this module documents: {uses}"
    )
    assert uses.index(TOKEN_STEP_USES) < workflow["jobs"][JOB]["steps"].index(step), (
        "the Key Vault step no longer precedes the injection point, so the "
        "credential is not in its environment and the reachability claim is stale"
    )


# --------------------------------------------------------------------------
# Behaviour — the payload, measured in both directions
# --------------------------------------------------------------------------


def test_the_pre_fix_body_stole_the_whmcs_secret_from_client_id_and_exited_zero():
    """The positive control. Without it, the fixed body's inertness is a claim
    about a body that might never have executed anything."""
    output, stolen, rc = _run(
        _pre_fix(_payload(LEGAL_CLIENT_ID), LEGAL_EMAIL), **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert stolen is not None, (
        f"the payload did not execute against the PRE-FIX body, so this module's "
        f"premise is wrong or the payload no longer fits the site. Output: "
        f"{output!r}"
    )
    assert stolen.strip() == FAKE_TOKEN, (
        f"the sentinel holds {stolen!r}, not the credential — the theft is not "
        f"what this test says it is. Output: {output!r}"
    )
    called = _called_line(output)
    assert f"ClientId=[{LEGAL_CLIENT_ID}]" in called, (
        f"the exploited run did not still make a LEGAL call, which is the half "
        f"that makes it invisible in the log: {called!r}"
    )
    assert rc == 0, (
        f"the exploited run exited {rc}, not 0 — an operator reading the log would "
        f"have had a failure to notice. Output: {output!r}"
    )


def test_the_pre_fix_body_stole_the_whmcs_secret_from_email_too():
    """Both inputs, not just the first. Each is its own pair of call sites, and a
    lane that fixed one would leave the other working."""
    output, stolen, rc = _run(
        _pre_fix(LEGAL_CLIENT_ID, _payload(LEGAL_EMAIL)), **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert stolen is not None and stolen.strip() == FAKE_TOKEN, (
        f"the payload in `email` did not steal the credential: sentinel={stolen!r} "
        f"output={output!r}"
    )
    called = _called_line(output)
    assert f"Email=[{LEGAL_EMAIL}]" in called, (
        f"the exploited run did not still make a legal call: {called!r}"
    )
    assert rc == 0, f"the exploited run exited {rc}, not 0. Output: {output!r}"


def test_the_subexpression_payload_alone_is_inert_here():
    """The trap this lane had to avoid: single quotes make `$( )` inert.

    The payload every double-quoted lane (116, 702) used reports THIS body as
    harmless. Pinned so that a future reader who reaches for the familiar payload
    and sees nothing happen does not conclude the site was never exploitable —
    the two tests above are the ones that settle it.
    """
    naive = (
        LEGAL_CLIENT_ID
        + "$($null = Set-Content -Path "
        + SENTINEL
        + " -Value $env:"
        + TOKEN_VAR
        + ")"
    )
    output, stolen, _rc = _run(
        _pre_fix(naive, LEGAL_EMAIL), **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert stolen is None, (
        f"the bare `$( )` payload executed inside SINGLE quotes, which pwsh does "
        f"not do — the premise of _payload()'s breakout is wrong. Output: {output!r}"
    )


def test_the_shipped_body_binds_both_payloads_as_data():
    """Through the fixed body, both payloads arrive verbatim as argument text."""
    step = _step()
    _assert_wiring(step)
    output, stolen, rc = _run(
        _rendered(step["run"]),
        IN_CLIENT_ID=_payload(LEGAL_CLIENT_ID),
        IN_EMAIL=_payload(LEGAL_EMAIL),
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert stolen is None, (
        f"the payload executed against the SHIPPED body: sentinel={stolen!r} "
        f"output={output!r}"
    )
    called = _called_line(output)
    assert _payload(LEGAL_CLIENT_ID) in called, (
        f"the payload did not arrive at the callee verbatim, so it was transformed "
        f"somewhere and this test is not measuring inert data: {called!r}"
    )
    assert rc == 0, f"the ordinary path did not survive the fix: {output!r}"


def test_the_shipped_body_still_passes_ordinary_inputs_through():
    step = _step()
    _assert_wiring(step)
    output, stolen, rc = _run(
        _rendered(step["run"]),
        IN_CLIENT_ID=LEGAL_CLIENT_ID,
        IN_EMAIL=LEGAL_EMAIL,
        **{TOKEN_VAR: FAKE_TOKEN},
    )
    assert stolen is None, f"a benign run wrote the sentinel: {output!r}"
    called = _called_line(output)
    assert f"ClientId=[{LEGAL_CLIENT_ID}]" in called, called
    assert f"Email=[{LEGAL_EMAIL}]" in called, called
    assert "Execute=[False]" in called, (
        f"dry_run=true must NOT pass -Execute: {called!r}"
    )
    assert rc == 0, output


def test_a_blank_client_id_is_omitted_not_passed_through():
    """229's documented path: blank id, resolve by email. NOT a fail-closed case.

    This is why the remedy is a gated append rather than the `exit 1` guard the
    other lanes add — pinned, so a later lane copying 118's wording cannot turn
    the workflow's primary use case into a failure.
    """
    step = _step()
    _assert_wiring(step)
    for blank in ("", "   "):
        output, _stolen, rc = _run(
            _rendered(step["run"]),
            IN_CLIENT_ID=blank,
            IN_EMAIL=LEGAL_EMAIL,
            **{TOKEN_VAR: FAKE_TOKEN},
        )
        called = _called_line(output)
        assert rc == 0, (
            f"a blank client_id ({blank!r}) failed the step — that is 229's "
            f"DOCUMENTED path ('leave blank to use email'). Output: {output!r}"
        )
        assert "ClientId=[]" in called, (
            f"a blank client_id ({blank!r}) reached the callee as something other "
            f"than absent: {called!r}"
        )
        assert f"Email=[{LEGAL_EMAIL}]" in called, (
            f"the email was not passed for a blank id, so nothing identifies the "
            f"client: {called!r}"
        )


def test_both_blank_fails_loudly_in_the_callee_not_silently_in_the_body():
    """Attribution, stated honestly: the workflow does NOT catch this — the
    script's own precondition does.

    Written because every earlier lane's fail-closed guard closes the blank case
    at the step, and copying that claim here would be untrue (the 116/L260
    lesson). The value of pinning it is that the outcome is LOUD either way.
    """
    step = _step()
    _assert_wiring(step)
    output, _stolen, rc = _run(
        _rendered(step["run"]), IN_CLIENT_ID="", IN_EMAIL="", **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert rc != 0, (
        f"both inputs blank exited 0 — nothing identified a client and nothing "
        f"complained. Output: {output!r}"
    )
    assert "Provide -ClientId or -Email" in output, (
        f"the failure did not come from the callee's precondition, so this test "
        f"is attributing it to the wrong component. Output: {output!r}"
    )


def test_without_the_predicates_an_empty_client_id_shifts_the_binding():
    """The L254 control: the gated append is load-bearing, and it is the EMPTY
    blank that needs it.

    Run repeatedly because the defect is nondeterministic — hashtable
    enumeration order is undefined, so one green run is not evidence of absence
    (which is the direction that would retire this control while it still
    matters).
    """
    step = _step()
    _assert_wiring(step)
    unguarded = _strip_predicates(_rendered(step["run"]))
    bindings = set()
    for _ in range(SHIFT_RUNS):
        output, _stolen, rc = _run(
            unguarded,
            IN_CLIENT_ID="",
            IN_EMAIL=LEGAL_EMAIL,
            **{TOKEN_VAR: FAKE_TOKEN},
        )
        called = _called_line(output)
        if called:
            bindings.add((rc, called))
    shifted = {
        (rc, called) for rc, called in bindings if "ClientId=[]" not in called
    }
    assert shifted, (
        f"in {SHIFT_RUNS} runs an unconditional assignment never mis-bound an "
        f"EMPTY client_id, so the gated append's justification (ledger L254) is "
        f"no longer measurable on this host and the body's comment overclaims. "
        f"Observed: {sorted(bindings)}"
    )
    assert any(rc == 0 for rc, _ in shifted), (
        f"every mis-bound run failed the step, which would make the defect loud "
        f"rather than silent — the comment's 'every one at exit 0' is then wrong. "
        f"Observed: {sorted(shifted)}"
    )


def test_without_the_predicates_an_unset_client_id_does_not_shift():
    """The other half of the same measurement, and the counter-intuitive one.

    An UNSET mapping is dropped from the native rendering entirely, so it does
    NOT shift — the hazard is the blank the dispatch form produces by default,
    not the one a deleted `env:` block produces. Pinned because the opposite is
    what the other lanes' L214 reasoning would lead a reader to assume.
    """
    step = _step()
    _assert_wiring(step)
    unguarded = _strip_predicates(_rendered(step["run"]))
    for _ in range(SHIFT_RUNS):
        output, _stolen, rc = _run(
            unguarded, IN_EMAIL=LEGAL_EMAIL, **{TOKEN_VAR: FAKE_TOKEN}
        )
        called = _called_line(output)
        assert rc == 0 and "ClientId=[]" in called, (
            f"an UNSET client_id mis-bound or failed, which contradicts the "
            f"measurement the body's comment records (correct 8/8). rc={rc} "
            f"called={called!r} output={output!r}"
        )


# --------------------------------------------------------------------------
# Agreement with the checker
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
    "test_the_pre_fix_body_stole_the_whmcs_secret_from_client_id_and_exited_zero",
    "test_the_pre_fix_body_stole_the_whmcs_secret_from_email_too",
    "test_the_subexpression_payload_alone_is_inert_here",
    "test_the_shipped_body_binds_both_payloads_as_data",
    "test_the_shipped_body_still_passes_ordinary_inputs_through",
    "test_a_blank_client_id_is_omitted_not_passed_through",
    "test_both_blank_fails_loudly_in_the_callee_not_silently_in_the_body",
    "test_without_the_predicates_an_empty_client_id_shifts_the_binding",
    "test_without_the_predicates_an_unset_client_id_does_not_shift",
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
