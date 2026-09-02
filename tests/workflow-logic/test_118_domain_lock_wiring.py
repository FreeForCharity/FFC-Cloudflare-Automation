"""Unit tests for 118's one dispatch-input call site (#1080 burn-down).

118 reads or CHANGES a domain's registrar lock in WHMCS under the gated
`whmcs-prod` environment. Its one free-text dispatch input, `domain`, used to be
interpolated into the single pwsh body that builds the splat for
`whmcs-domain-lock.ps1`. It now arrives through step-level `env:` (IN_DOMAIN).

WHERE THE CREDENTIAL SAT — INVISIBLE TO BOTH RECOMMENDED SWEEPS
    The WHMCS API credential (`WHMCS_API_IDENTIFIER` / `WHMCS_API_SECRET` /
    `WHMCS_APIM_SUBSCRIPTION_KEY`) is minted by the PRECEDING step,
    `uses: ./.github/actions/whmcs-secrets-from-kv`, which exports it through
    GITHUB_ENV. So it is in the process environment of the injection point while
    appearing in no `env:` block anywhere in the file and matching no `secrets.`
    reference. A reviewer reading the step's own `env:` (ledger L213) and a sweep
    for `secrets.` in a `run:` body (#1141) both score this file as holding
    nothing — and it is the workflow's ONLY credential, the same surface 205 and
    #1080's lanes 7, 9 and 10 each rediscovered by hand.
    `test_the_whmcs_credential_reaches_the_step_only_through_the_kv_action` pins
    it, because it is the reason this entry was worth taking.

THE INJECTION POINT SAT IN SINGLE QUOTES, AND THE WRONG PAYLOAD "PROVES" SAFETY
    `domain` interpolated as a SINGLE-quoted hashtable value:

        $params = @{ Domain = '${{ inputs.domain }}' }

    pwsh does not expand `$( )` inside single quotes, so the subexpression payload
    that exploited the double-quoted lanes is inert here — measured, it reaches the
    callee as the literal text `example.org$(Set-Content …)` at exit 0. A control
    built that way reports *the pre-fix body did not execute the payload*, which
    reads as evidence the interpolation was harmless: the flattering direction, and
    a reason not to fix a live injection under `whmcs-prod` (ledger L243).
    `test_the_subexpression_payload_is_inert_in_single_quotes` keeps that as an
    assertion rather than a footnote. The real control CLOSES the single-quoted
    string AND the hashtable, steals, then re-opens a fresh
    `$params = @{ Domain = 'example.org` so the body's own trailing ` }` closes it —
    which is why the exploited run still exits 0 with a normal-looking call.

    Measured against the body as it shipped, `domain` =
    `example.org' }; $null = Set-Content -Path <sentinel> -Value
    $env:WHMCS_API_SECRET; $params = @{ Domain = 'example.org`:

        * the WHMCS API secret was written to the sentinel file;
        * the callee was then invoked with `Domain=[example.org]` and `DryRun` intact;
        * the step exited **0**, under `whmcs-prod`, after the approval.

THE BLANK IS NONDETERMINISTIC, AND THAT IS A DEFECT THE INTERPOLATION WAS HIDING
    Every earlier lane added its `IsNullOrWhiteSpace` guard to close a hazard the
    REMEDY manufactures (#1150, ledger L214: moving a value into `env:` lets an
    unset mapping vanish from the argument list). Here the guard closes a defect
    that was already in the shipped body, and it is worse than the usual shift:

        `& pwsh -NoProfile -File … @params` splats a HASHTABLE onto a NATIVE
        command. `@{ }` is unordered — PowerShell makes no guarantee about
        enumeration order and it genuinely varies per process — and a blank value
        is DROPPED from the rendered `-Key:Value` list rather than rendered empty.
        So when `Domain` happens to enumerate before `DryRun`, `-Domain` binds the
        NEXT token and everything shifts one position left.

    Measured on the pre-fix body with the dispatch box blanked and `dry_run=true`,
    eight runs of one unchanged script:

        * 5 runs — `Cannot process command because of one or more missing
          mandatory parameters: Domain`, rc 1. Loud, correct, harmless.
        * 3 runs — `CALLED Domain=[-DryRun:True] Lock=[False] DryRun=[False]`,
          rc **0**. The `-DryRun` switch the operator dispatched is simply gone.

    Same input, same commit, two outcomes. `test_the_shift_is_an_ordering_property`
    pins the mechanism deterministically with `[ordered]` hashtables rather than
    asserting on a coin flip, and
    `test_without_the_guard_a_blank_domain_never_runs_as_dispatched` asserts the
    invariant that holds on every ordering: a blank domain never reaches the callee
    as the run that was actually asked for.

    Note what the nondeterminism does NOT reach: the swallowed token is
    `-DryRun:True`, so the callee is handed that string as its domain and cannot
    find it in WHMCS. The exposure is a lost dry-run flag on a bogus lookup, not a
    lock change on a real domain — stated here because the tempting version of this
    finding ("a dry run silently performed a registrar write") is one step stronger
    than what was measured.

A DEFAULT IS NOT A CONSTRAINT
    `domain` is `required: true` with no default, `type: string`. GitHub constrains
    `boolean` and `choice` and nothing else, so the dispatcher may put any text in
    that box. `action` (choice) and `dry_run` (boolean) stay interpolated and are
    NOT findings; `test_the_domain_input_is_free_text` pins that against the
    guard's own constrained set rather than restating it.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE. It
    is a detector defined over the defect's spelling, so it goes quiet the moment
    the spelling is gone — including when the REMEDY is gone too (ledger L202).
    Delete the `env:` mapping and nothing is interpolated, so the checker is
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

WORKFLOW = "118-whmcs-domain-lock.yml"
JOB = "lock"
STEP = "Run registrar lock/unlock"
ENVIRONMENT = "whmcs-prod"

# The env var the moved input must travel in, and the expression it maps from.
MAPPINGS = {"IN_DOMAIN": "${{ inputs.domain }}"}
INPUT_NAMES = ("domain",)

# The credential in reach of the injection point, and the step that puts it there.
# Exported to GITHUB_ENV by the `whmcs-secrets-from-kv` composite action, so it
# appears in no `env:` block and matches no `secrets.*` reference in this file.
# That step is a bare `uses:` with no `name:`, so it is located by its `uses:`
# value rather than through find_step (which matches on name).
TOKEN_VAR = "WHMCS_API_SECRET"
TOKEN_STEP_USES = "./.github/actions/whmcs-secrets-from-kv"
CREDENTIAL_VARS = (
    "WHMCS_API_SECRET",
    "WHMCS_API_IDENTIFIER",
    "WHMCS_APIM_SUBSCRIPTION_KEY",
)

# Deliberately NOT shaped like a real secret. A value a scanner treats as a
# credential comes back REDACTED, and the one place this value is printed is an
# assertion message on a failing run — the moment the reader needs to see whether
# the sentinel holds the credential or an empty string.
FAKE_TOKEN = "whmcs-secret-placeholder-not-a-real-token"
SENTINEL = "STOLEN-118.txt"

LEGAL_DOMAIN = "example.org"

# The interpolations the burn-down deliberately LEAVES in the body, and what GitHub
# substitutes for them. `action` is `type: choice` and `dry_run` `type: boolean`, so
# GitHub generates both from the declared options and neither can carry a payload —
# which is exactly why they stay. Rendering them is what makes a run of the shipped
# body a run of what actually ships.
CONSTRAINED_RENDERINGS = {
    "${{ inputs.action }}": "unlock",
    "${{ inputs.dry_run }}": "true",
}

# Every variable the harness must own outright: an inherited one could satisfy an
# assertion the workflow is supposed to.
CONTROLLED_VARS = ("IN_DOMAIN", TOKEN_VAR)

# The body as it shipped BEFORE the burn-down, verbatim from origin/main, with the
# substitution point marked. `action` and `dry_run` are rendered to their
# constrained literals because GitHub would. It is the positive control: without
# it, "the fixed body does not execute the payload" is a claim about a body that
# might never have executed anything.
PRE_FIX_BODY = """$ErrorActionPreference = 'Stop'
$params = @{ Domain = 'DOMAIN_HERE' }
if ('unlock' -eq 'lock') { $params.Lock = $true }
if ('true' -eq 'true') { $params.DryRun = $true }

$out = & pwsh -NoProfile -File .\\scripts\\whmcs-domain-lock.ps1 @params
if ($LASTEXITCODE -ne 0) { throw "whmcs-domain-lock.ps1 failed (exit $LASTEXITCODE)." }

$json = ($out | Out-String).Trim()
Write-Host $json
$fence = [string][char]0x60 * 3
@('### Registrar lock result', '', ($fence + 'json'), $json, $fence) |
  Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
"""

# A stand-in for the real script that records what it was BOUND, which is the only
# discriminator that works here — a marker-string search over stdout is not one,
# because pwsh echoes the offending source line back in a ParserError, so any
# substring predicate matches the payload text on a run that executed nothing.
#
# `Domain` keeps the real script's `[Parameter(Mandatory = $true)]`. That attribute
# is not decoration in this module: it is what turns the non-shifted blank into a
# loud refusal, and therefore what makes the two halves of the nondeterminism
# distinguishable. `Lock` / `DryRun` are the real switches; the callee's remaining
# connection parameters are never splatted by this workflow.
STUB = """[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [Parameter()]
    [switch]$Lock,
    [Parameter()]
    [switch]$DryRun
)
Write-Output "CALLED Domain=[$Domain] Lock=[$Lock] DryRun=[$DryRun]"
"""

# The two orderings of the same splat, as a MECHANISM probe. Not derived from the
# shipped body: the claim under test is a property of PowerShell's native-command
# splatting, and pinning it with `[ordered]` is what makes it an assertion rather
# than a coin flip. `@{ }` in the workflow is one of these two at random.
ORDERED_PROBE = """$ErrorActionPreference = 'Stop'
$params = [ordered]@{ ORDER_HERE }
$out = & pwsh -NoProfile -File .\\scripts\\whmcs-domain-lock.ps1 @params
Write-Output (($out | Out-String).Trim())
"""
DOMAIN_FIRST = "Domain = ''; DryRun = $true"
DOMAIN_LAST = "DryRun = $true; Domain = ''"

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that omits
# it pins the wrong exit code and vouches for it (recorded on #1080's lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

# What the shift looks like when it happens, and what the other ordering prints.
SHIFTED = "CALLED Domain=[-DryRun:True] Lock=[False] DryRun=[False]"
REFUSED = "missing mandatory parameters: Domain"


def _breakout_payload() -> str:
    """A payload valid in the position it lands in: a SINGLE-quoted hashtable value.

    It closes the string and the `@{` hashtable, steals the credential, then
    re-opens a fresh `$params = @{ Domain = 'example.org` so the body's own trailing
    ` }` closes it — which is what keeps the exploited run at exit 0 with a
    normal-looking call in the log.
    """
    return (
        LEGAL_DOMAIN + "' }; "
        "$null = Set-Content -Path '" + SENTINEL + "' -Value $env:" + TOKEN_VAR + "; "
        "$params = @{ Domain = '" + LEGAL_DOMAIN
    )


def _subexpression_payload() -> str:
    """The payload every DOUBLE-quoted lane used: `$( )`, inert in single quotes.

    A bareword `-Path` (no inner quotes) so the demonstration is a clean literal
    rather than a quote-break parse error — the claim under test is that the
    subexpression does not EXECUTE here, not that it fails to parse.
    """
    return (
        LEGAL_DOMAIN
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
    (`${{ inputs . domain }}`) cannot slip past the step-level assertion, and the
    two cannot drift apart the way a restated rule does.
    """
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    return found


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


def _rendered(body: str) -> str:
    """The shipped body with GitHub's substitution performed, as GitHub does it.

    The two survivors are `action` (choice) and `dry_run` (boolean), which the
    burn-down deliberately leaves interpolated. Running the RAW body instead is a
    silent trap and it caught this module first time out: the literal text
    `'${{ inputs.dry_run }}'` is not `'true'`, so `-DryRun` is never added to the
    splat and a test asserting the ordinary path still carries it fails against a
    correct workflow — a false red pointing at the fix.

    Every `${{ }}` left in the body must be one of these two. A new one means an
    input was re-interpolated, or a constrained input was renamed, and either way
    the body being executed is no longer the body that ships. Failing here is much
    louder than executing an unsubstituted expression and reading the result.
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


def _assert_wiring(step: dict) -> None:
    """The input travels in env, the body reads it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES this variable (ledger L199): delete the
    workflow's `env:` mapping and the step still sees it from the harness, so every
    behavioural test below keeps passing over plumbing that no longer exists.
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

    `stdin=DEVNULL` is load-bearing, not hygiene. A mandatory parameter that ends
    up unsatisfied makes PowerShell PROMPT for it, and on an interactive stdin the
    call blocks forever — measured here as a whole-suite hang rather than a failure.
    A runner's stdin is not a terminal, so DEVNULL is also the faithful shape: the
    prompt then fails immediately with the mandatory-parameter error this module
    asserts on.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "whmcs-domain-lock.ps1").write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8")
        # The shipped body's last statement appends to the job summary. Unset, that
        # is `Out-File -FilePath $null` under `ErrorActionPreference = 'Stop'`, so
        # every behavioural test would fail on the harness rather than on the body.
        summary = tmp / "step-summary.md"
        env = child_env(GITHUB_STEP_SUMMARY=str(summary), **env_overrides)
        # Only what the test sets may be visible: an inherited IN_DOMAIN would make
        # the fail-closed tests pass for the wrong reason, and an inherited token
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
            timeout=120,
        )
        stolen = tmp / SENTINEL
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return proc.stdout + proc.stderr, contents, proc.returncode


def _pre_fix(domain: str) -> str:
    """The pre-fix body with GitHub's substitution performed, as GitHub does it.

    Asserted rather than assumed: a `.replace` that stopped matching would leave
    the marker in place and the control would measure an unexploited body while
    reporting that the payload did not run (ledger L47).
    """
    assert PRE_FIX_BODY.count("DOMAIN_HERE") == 1
    rendered = PRE_FIX_BODY.replace("DOMAIN_HERE", domain)
    assert "_HERE" not in rendered, f"substitution left a marker behind: {rendered!r}"
    return rendered


def _strip_guard(body: str) -> str:
    """Remove the emptiness guard, asserting it was there.

    The occurrence count is asserted BEFORE substituting (ledger L47): an anchor
    that stopped matching must fail loudly rather than silently leave the body
    unchanged and score the control as a pass.
    """
    anchor = "if ([string]::IsNullOrWhiteSpace($env:IN_DOMAIN)) {"
    assert body.count(anchor) == 1, (
        f"expected exactly one emptiness guard to strip, found {body.count(anchor)} "
        f"— this control would otherwise test an unmodified body. Body: {body!r}"
    )
    start = body.index(anchor)
    end = body.index("}", body.index("exit 1", start)) + 1
    stripped = body[:start] + body[end:]
    # Count the ANCHOR, not the bare call name. The step body explains the guard in
    # a comment directly above it, so a `"IsNullOrWhiteSpace" not in stripped` check
    # fails on the PROSE describing the thing it is looking for and reports the
    # control as broken over a correct strip — #1019, hit while writing this module.
    assert stripped.count(anchor) == 0, (
        f"a guard survived the strip, so the control is measuring the guarded "
        f"body. Stripped: {stripped!r}"
    )
    assert "exit 1" not in stripped, (
        f"the strip left a fail-closed exit behind, so the control is not measuring "
        f"an unguarded body. Stripped: {stripped!r}"
    )
    assert "whmcs-domain-lock.ps1" in stripped, (
        f"the strip removed the invocation itself, so the control proves nothing. "
        f"Stripped: {stripped!r}"
    )
    return stripped


def _classify(out: str, rc: int) -> str:
    """Which of the two blank-domain outcomes a run produced."""
    if SHIFTED in out:
        return "shifted"
    if REFUSED in out and rc != 0:
        return "refused"
    return f"UNEXPECTED(rc={rc}): {out.strip()[:300]}"


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_the_domain_input_is_wired_through_env():
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


def test_the_domain_input_is_free_text():
    """What makes it a finding, asserted against the guard's OWN constrained set.

    `action` and `dry_run` stay interpolated and are not findings — checked here
    too, so a change that widened either one's type would fail this module rather
    than silently enlarge the injection surface.
    """
    workflow = load_workflow(WORKFLOW)
    declared = guard.dispatch_inputs(workflow)
    for name in INPUT_NAMES:
        assert name in declared, f"input {name!r} is gone from the dispatch form: {declared}"
        assert declared[name] not in guard.CONSTRAINED_TYPES, (
            f"input {name!r} now declares type {declared[name]!r}, which GitHub "
            f"constrains — the freeze entry's premise has changed"
        )
    assert set(guard.free_text_inputs(workflow)) >= set(INPUT_NAMES)
    for constrained in ("action", "dry_run"):
        assert declared.get(constrained) in guard.CONSTRAINED_TYPES, (
            f"input {constrained!r} declares type {declared.get(constrained)!r}, "
            f"which GitHub does NOT constrain — it is interpolated into the step "
            f"body on the strength of being a choice/boolean, so it is now a "
            f"#1080 finding of its own"
        )


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
    for name in CREDENTIAL_VARS:
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
        _pre_fix(_breakout_payload()), **{TOKEN_VAR: FAKE_TOKEN}
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
    assert f"CALLED Domain=[{LEGAL_DOMAIN}]" in out, (
        f"the exploited run did not go on to call the script with a legal domain, "
        f"so it would not have looked like a normal run: {out}"
    )
    assert "DryRun=[True]" in out, (
        f"the exploited run lost the dry-run switch, so it is not the 'looks "
        f"exactly like the dispatch' control this claims to be: {out}"
    )
    assert rc == 0, (
        f"the exploited run exited {rc}, not 0 — the whole point of this control is "
        f"that the theft is invisible in the run's outcome: {out}"
    )


def test_the_subexpression_payload_is_inert_in_single_quotes():
    """The control that would have "proved" there was no defect here.

    118 interpolated into SINGLE quotes, so the `$( )` payload that exploited every
    double-quoted lane does not expand. A module that reached for the familiar
    payload would report the pre-fix body as harmless — the flattering direction,
    and a reason not to fix a live injection.
    """
    out, stolen, rc = _run(
        _pre_fix(_subexpression_payload()), **{TOKEN_VAR: FAKE_TOKEN}
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


def test_the_shipped_body_binds_the_payload_as_data():
    """The same payload, through `env:`: it arrives as an argument, verbatim."""
    step = _step()
    _assert_wiring(step)
    payload = _breakout_payload()
    out, stolen, rc = _run(
        _rendered(step["run"]), IN_DOMAIN=payload, **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert stolen is None, (
        f"the payload still executed: the sentinel holds {stolen!r}. The env: "
        f"mapping is not doing what #1080 requires of it. Output: {out}"
    )
    assert f"CALLED Domain=[{payload}]" in out, (
        f"the payload did not arrive at the callee as one literal argument, so it "
        f"was neither executed nor bound as data — something else happened and this "
        f"test cannot tell what: {out}"
    )
    assert rc == 0, f"the shipped body exited {rc} on a legal-length input: {out}"


def test_the_shipped_body_still_passes_ordinary_inputs_through():
    """The fix must not have made the step inert (ledger L202)."""
    out, _, rc = _run(
        _rendered(_step()["run"]), IN_DOMAIN=LEGAL_DOMAIN, **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert f"CALLED Domain=[{LEGAL_DOMAIN}]" in out, (
        f"the ordinary path no longer reaches the script with its domain: {out}"
    )
    assert "DryRun=[True]" in out, (
        f"the ordinary path no longer carries the dry_run switch the workflow "
        f"renders as `true`, so the splat lost a parameter: {out}"
    )
    assert rc == 0, f"the ordinary path exited {rc}: {out}"


# --------------------------------------------------------------------------
# Fail-closed, and the nondeterminism it removes
# --------------------------------------------------------------------------


def test_an_empty_domain_fails_closed_and_says_so():
    """rc AND text, per CLAUDE.md: `rc == 1` alone cannot tell a refusal from a
    harness that could not start."""
    out, _, rc = _run(_rendered(_step()["run"]), IN_DOMAIN="   ", **{TOKEN_VAR: FAKE_TOKEN})
    assert rc == 1, f"a whitespace IN_DOMAIN exited {rc}, expected 1: {out}"
    assert "::error::IN_DOMAIN is empty" in out, (
        f"the step exited 1 with a blank IN_DOMAIN but did not say so — an exit "
        f"code alone does not distinguish a refusal from a broken harness. "
        f"Output: {out}"
    )
    assert "CALLED Domain=" not in out, (
        f"the script was invoked despite a blank domain: {out}"
    )


def test_an_unset_domain_fails_closed_and_says_so():
    """The deleted-or-misspelled `env:` line, which is the case L214 is about."""
    out, _, rc = _run(_rendered(_step()["run"]), **{TOKEN_VAR: FAKE_TOKEN})
    assert rc == 1, f"an unset IN_DOMAIN exited {rc}, expected 1: {out}"
    assert "::error::IN_DOMAIN is empty" in out, (
        f"the step exited 1 with no mapping at all but named nothing: {out}"
    )
    assert "CALLED Domain=" not in out, f"the script ran anyway: {out}"


def test_the_shift_is_an_ordering_property():
    """The mechanism, pinned deterministically instead of on a coin flip.

    A blank value is DROPPED from a hashtable splat onto a native command, so
    whether `-Domain` swallows the next token depends only on which key enumerates
    first. `[ordered]` fixes that order and makes both outcomes reproducible; the
    shipped `@{ }` is one of these two at random, which is the whole finding.
    """
    out, _, rc = _run(ORDERED_PROBE.replace("ORDER_HERE", DOMAIN_FIRST))
    assert SHIFTED in out, (
        f"with `Domain` first, the blank was expected to drop out of the splat and "
        f"`-Domain` to swallow `-DryRun:True` — the silent case this workflow's "
        f"guard exists to stop. Got rc={rc}: {out}"
    )
    assert rc == 0, f"the shifted run exited {rc}, not 0 — it is meant to be silent: {out}"

    out, _, rc = _run(ORDERED_PROBE.replace("ORDER_HERE", DOMAIN_LAST))
    assert REFUSED in out and rc != 0, (
        f"with `Domain` last, the blank was expected to reach the binder and be "
        f"refused as a missing mandatory parameter — the loud case. If BOTH "
        f"orderings now behave the same way, the nondeterminism in this module's "
        f"docstring is gone and the reasoning needs re-reading. Got rc={rc}: {out}"
    )


def test_without_the_guard_a_blank_domain_never_runs_as_dispatched():
    """The control: strip the guard, and no ordering produces the requested run.

    Deliberately NOT an assertion that both outcomes appear — that is a property of
    a random enumeration order and would flake. The invariant that holds on every
    ordering is the one worth pinning: with the guard removed, a blank domain either
    dies on the mandatory parameter or reaches the callee having silently lost
    `-DryRun`, and never once arrives as the dry run the operator asked for.
    """
    stripped = _strip_guard(_rendered(_step()["run"]))
    seen = []
    for _ in range(6):
        out, _, rc = _run(stripped, IN_DOMAIN="", **{TOKEN_VAR: FAKE_TOKEN})
        verdict = _classify(out, rc)
        seen.append(verdict)
        assert verdict in ("shifted", "refused"), (
            f"with the guard removed, a blank domain produced an outcome this "
            f"module does not model: {verdict}. Both known outcomes are bad ones; "
            f"a third needs reading before it is trusted. All so far: {seen}"
        )
        assert f"CALLED Domain=[] Lock=[False] DryRun=[True]" not in out, (
            f"a blank domain reached the callee as the run that was actually "
            f"dispatched — the callee's Mandatory attribute no longer refuses an "
            f"empty string, so the loud half of this module's model is gone: {out}"
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
    "test_the_shipped_body_binds_the_payload_as_data",
    "test_the_shipped_body_still_passes_ordinary_inputs_through",
    "test_an_empty_domain_fails_closed_and_says_so",
    "test_an_unset_domain_fails_closed_and_says_so",
    "test_the_shift_is_an_ordering_property",
    "test_without_the_guard_a_blank_domain_never_runs_as_dispatched",
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
