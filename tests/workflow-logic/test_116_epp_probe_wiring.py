"""Unit tests for 116's two dispatch-input call sites (#1080 burn-down).

116 probes whether a domain's EPP/auth transfer code comes back inline from the
WHMCS API or is only emailed to the registrant, under the gated `whmcs-prod`
environment. Its one free-text dispatch input, `domain`, used to be interpolated
into the single pwsh body TWICE — once into the variable the splat is built from
and once into the job-summary line. Both now read `$env:IN_DOMAIN` / the local
`$domain` it is copied into.

TWO CALL SITES, ONE FREEZE ENTRY
    `KNOWN_UNGUARDED` records an input NAME per workflow, not a count, so
    `("domain",)` was one entry covering two substitution points. A lane that
    fixed only the assignment would have satisfied a reading of the entry and
    left the summary line executing dispatcher text — which is why
    `test_the_body_has_no_remaining_domain_interpolation` is written over the
    checker's own patterns rather than over a count this module chose.

WHERE THE CREDENTIAL SAT — INVISIBLE TO BOTH RECOMMENDED SWEEPS
    The WHMCS API credential (`WHMCS_API_IDENTIFIER` / `WHMCS_API_SECRET` /
    `WHMCS_APIM_SUBSCRIPTION_KEY`) is minted by the PRECEDING step,
    `uses: ./.github/actions/whmcs-secrets-from-kv`, which exports it through
    GITHUB_ENV. It is therefore in the process environment of the injection point
    while appearing in no `env:` block in this file and matching no `secrets.`
    reference, so an L213 `env:` read and the #1141 `secrets.` grep both score
    the file as holding nothing. It is also the workflow's ONLY credential.

THE INJECTION POINT SAT IN DOUBLE QUOTES — NO BREAKOUT WAS NEEDED
    Unlike 118, whose single-quoted site made the `$( )` payload inert and
    required closing the string and the hashtable, pwsh expands subexpressions
    inside double quotes. So the payload is a plain suffix on a legal value:

        domain = example.org$($null = Set-Content -Path <sentinel> -Value $env:WHMCS_API_SECRET)

    `$null =` swallows the subexpression's output, so `$domain` keeps the value
    `example.org` and nothing in the log looks unusual. Measured against the body
    as it shipped, on pwsh 7.4.6:

        * the WHMCS API secret was written to the sentinel file;
        * the callee was then invoked with `Domain=[example.org]`, `Execute` and
          `ShowCode` intact;
        * the step exited **0**, under `whmcs-prod`, after the approval.

THIS LANE BOUNDS L214 RATHER THAN RE-CONFIRMING IT
    Every earlier lane added `IsNullOrWhiteSpace` to close a hazard the REMEDY
    manufactures: moving a value into `env:` lets an unset mapping vanish from a
    native command's argument list, so `-Domain` binds the next token and
    everything shifts one position left (#1150, ledger L214).

    That does not happen here, and the difference was measured rather than
    inherited. 116 splats an ARRAY whose only other elements are SWITCHES
    (`-Execute`, `-ShowCode`), and a switch cannot absorb a value — so there is
    no plausible-looking call for the blank to shift into. With the guard
    stripped, on pwsh 7.4.6, both call forms and both blank states:

        unset     `Missing an argument for parameter 'Domain'.`      rc 1
        empty ''  `Cannot bind argument to parameter 'Domain'
                   because it is an empty string.`                   rc 1

    Loud, deterministic, and harmless in both directions — none of the
    3-of-8-runs nondeterminism 118 measured. The guard is still correct to add,
    for a different and smaller reason: it attributes the fault to the missing or
    misnamed `env:` mapping at the step that owns it, instead of surfacing as a
    binder error naming the callee. `test_without_the_guard_a_blank_fails_loudly`
    pins the bound so the next lane does not restate 118's claim about a body
    whose shape does not support it. Ledger L260.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow, pwsh_invocation  # noqa: E402

_GUARD_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "scripts"
    / "check-workflow-input-interpolation.py"
)
_spec = importlib.util.spec_from_file_location("interp_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "116-domain-transfer-epp-probe.yml"
JOB = "probe"
STEP = "Run EPP probe"
ENVIRONMENT = "whmcs-prod"
CALLEE = "domain-transfer-epp-probe.ps1"

# The env var the moved input must travel in, and the expression it maps from.
MAPPINGS = {"IN_DOMAIN": "${{ inputs.domain }}"}
INPUT_NAMES = ("domain",)

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
SENTINEL = "STOLEN-116.txt"

LEGAL_DOMAIN = "example.org"

# The body as it shipped BEFORE the burn-down, verbatim from origin/main :61-80,
# with BOTH substitution points marked. `mode` and `show_code` are rendered to
# their constrained literals because GitHub would. It is the positive control:
# without it, "the fixed body does not execute the payload" is a claim about a
# body that might never have executed anything.
PRE_FIX_BODY = """$ErrorActionPreference = 'Stop'
$domain = "DOMAIN_HERE"
$mode = "dry-run"

$scriptArgs = @('-Domain', $domain)
if ($mode -eq 'execute') { $scriptArgs += '-Execute' }
if ('false' -eq 'true') { $scriptArgs += '-ShowCode' }

$ErrorActionPreference = 'Continue'
$out = & pwsh -NoProfile -File .\\scripts\\domain-transfer-epp-probe.ps1 @scriptArgs
$exit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($exit -ne 0) { throw "domain-transfer-epp-probe.ps1 failed (exit $exit)." }

$summary = ($out | Out-String)
Write-Host $summary
$fence = [string][char]0x60 * 3
$lines = @("### EPP probe ($mode): DOMAIN_HERE", '', ($fence + 'json'), $summary.TrimEnd(), $fence)
$lines | Out-File -FilePath $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
"""

# A stand-in for the real script that records what it was BOUND, which is the only
# discriminator that works here — a marker-string search over stdout is not one,
# because pwsh echoes the offending source line back in a ParserError, so any
# substring predicate matches the payload text on a run that executed nothing.
#
# `Domain` keeps the real script's `[Parameter(Mandatory = $true)]`; `Execute` and
# `ShowCode` are the real switches. That the other two are SWITCHES is the whole
# reason the blank cannot shift into a plausible call here, so the stub's shape is
# load-bearing rather than decorative.
STUB = """[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [Parameter()]
    [switch]$Execute,
    [Parameter()]
    [switch]$ShowCode
)
Write-Output "CALLED Domain=[$Domain] Execute=[$Execute] ShowCode=[$ShowCode]"
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
# substitutes for them. `mode` is `type: choice` and `show_code` `type: boolean`, so
# GitHub generates both from the declared options and neither can carry a payload —
# which is exactly why they stay. Rendering them is what makes a run of the shipped
# body a run of what actually ships.
CONSTRAINED_RENDERINGS = {
    "${{ inputs.mode }}": "dry-run",
    "${{ inputs.show_code }}": "false",
}

# Every variable the harness must own outright: an inherited one could satisfy an
# assertion the workflow is supposed to.
CONTROLLED_VARS = ("IN_DOMAIN", TOKEN_VAR)

# What a blank produces once the guard is gone — the two loud outcomes, neither of
# which is a shift. Quoted from the pwsh binder rather than paraphrased.
UNSET_REFUSAL = "Missing an argument for parameter 'Domain'"
EMPTY_REFUSAL = "Cannot bind argument to parameter 'Domain'"

GUARD_ANCHOR = "if ([string]::IsNullOrWhiteSpace($env:IN_DOMAIN)) {"


def _subexpression_payload() -> str:
    """The payload a DOUBLE-quoted site takes: `$( )`, no breakout required.

    `$null =` swallows the subexpression's output so the surrounding string keeps
    its legal value — which is what makes the exploited run indistinguishable
    from an ordinary one in the log, and therefore worth pinning.
    """
    return (
        LEGAL_DOMAIN
        + "$($null = Set-Content -Path "
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

    Running the RAW body instead is a silent trap: the literal text
    `'${{ inputs.show_code }}'` is not `'false'`, so a test asserting the ordinary
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


def _pre_fix(domain: str) -> str:
    """The pre-fix body with GitHub's substitution performed, at BOTH sites.

    The count is asserted before substituting (ledger L47): a `.replace` that
    stopped matching would leave the markers in place and the control would
    measure an unexploited body while reporting that the payload did not run. Two
    is the number that matters here — it is the fact the freeze entry's single
    `("domain",)` does not record.
    """
    assert PRE_FIX_BODY.count("DOMAIN_HERE") == 2, (
        "the pre-fix body must carry BOTH call sites; found "
        f"{PRE_FIX_BODY.count('DOMAIN_HERE')}"
    )
    rendered = PRE_FIX_BODY.replace("DOMAIN_HERE", domain)
    assert "_HERE" not in rendered, f"substitution left a marker behind: {rendered!r}"
    return rendered


def _strip_guard(body: str) -> str:
    """Remove the emptiness guard, asserting it was there.

    The occurrence count is asserted BEFORE substituting (ledger L47): an anchor
    that stopped matching must fail loudly rather than silently leave the body
    unchanged and score the control as a pass.
    """
    assert body.count(GUARD_ANCHOR) == 1, (
        f"expected exactly one emptiness guard to strip, found "
        f"{body.count(GUARD_ANCHOR)} — this control would otherwise test an "
        f"unmodified body. Body: {body!r}"
    )
    start = body.index(GUARD_ANCHOR)
    end = body.index("}", body.index("exit 1", start)) + 1
    stripped = body[:start] + body[end:]
    # Count the ANCHOR, not the bare call name. The step body explains the guard in
    # a comment directly above it, so a `"IsNullOrWhiteSpace" not in stripped` check
    # fails on the PROSE describing the thing it is looking for and reports the
    # control as broken over a correct strip (#1019).
    assert stripped.count(GUARD_ANCHOR) == 0, (
        f"a guard survived the strip, so the control is measuring the guarded "
        f"body. Stripped: {stripped!r}"
    )
    assert "exit 1" not in stripped, (
        f"the strip left a fail-closed exit behind, so the control is not measuring "
        f"an unguarded body. Stripped: {stripped!r}"
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

    `stdin=DEVNULL` is load-bearing, not hygiene. A mandatory parameter that ends
    up unsatisfied makes PowerShell PROMPT for it, and on an interactive stdin the
    call blocks forever. A runner's stdin is not a terminal, so DEVNULL is also
    the faithful shape: the prompt then fails immediately with the
    mandatory-parameter error this module asserts on.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / CALLEE).write_text(STUB, encoding="utf-8")
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


def _assert_wiring(step: dict) -> None:
    """The input travels in env, the body reads it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES this variable (ledger L199): delete the
    workflow's `env:` mapping and the step still sees it from the harness, so
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


def test_the_domain_input_is_wired_through_env():
    _assert_wiring(_step())


def test_the_body_has_no_remaining_domain_interpolation():
    """Both call sites, not just the assignment the freeze entry implies.

    The entry was `("domain",)` — one input name covering TWO substitution
    points. A lane that fixed the `$domain = "…"` line and left the job-summary
    line alone would still satisfy a careless reading of it, so this asserts over
    the checker's own patterns and additionally pins that the summary line reads
    the local variable.
    """
    body = _step().get("run", "")
    still = _interpolated_inputs(body) & set(INPUT_NAMES)
    assert not still, (
        f"the body still interpolates {sorted(still)} — expected every FREE-TEXT "
        f"dispatch input to arrive through env:. (`mode` and `show_code` are "
        f"constrained and stay; test_the_domain_input_is_free_text_and_the_others_"
        f"are_not is what keeps that true.) Body: {body!r}"
    )
    assert body.count("$env:IN_DOMAIN") >= 1, (
        f"the body no longer reads the env mapping at all. Body: {body!r}"
    )
    assert "### EPP probe ($mode): $domain" in body, (
        "the job-summary line no longer reads the local $domain. That was the "
        "SECOND call site this lane closed; if it has been rewritten, re-check "
        "that it did not go back to interpolating the input. Body: " + repr(body)
    )


def test_the_step_sits_in_the_gated_whmcs_job():
    """This entry is a WRITE lane in the #1080 freeze because of `whmcs-prod`.

    If the job ever loses its environment, an injected payload no longer runs
    after an approval and the reasoning in this module's docstring stops being
    the reason the fix mattered — worth failing over rather than silently keeping.
    """
    job = load_workflow(WORKFLOW)["jobs"][JOB]
    assert job.get("environment") == ENVIRONMENT, (
        f"job {JOB!r} declares environment {job.get('environment')!r}, expected "
        f"{ENVIRONMENT!r}"
    )


def test_the_domain_input_is_free_text_and_the_others_are_not():
    """Why `domain` had to move and why `mode` / `show_code` did not.

    Read from the workflow's own declarations rather than restated, so a change
    of `mode` from `choice` to `string` fails here instead of silently making two
    interpolations live again.

    Read through the GUARD's own `dispatch_inputs` rather than by indexing `on:`
    — after `yaml.safe_load` that key is the YAML 1.1 boolean `True` (the Norway
    problem), so the obvious spelling raises KeyError, and the near-miss spelling
    `workflow.get("on", {})` would return `{}` and pass this test by inspecting
    nothing.
    """
    declared = guard.dispatch_inputs(load_workflow(WORKFLOW))
    assert set(declared) >= {"domain", "mode", "show_code"}, (
        f"the dispatch inputs read as {sorted(declared)} — this test must not "
        f"pass by inspecting an empty declaration set"
    )
    assert declared["domain"] not in guard.CONSTRAINED_TYPES, (
        "`domain` is no longer free text; if GitHub now constrains it, this "
        "lane's premise has changed"
    )
    for name in ("mode", "show_code"):
        assert declared[name] in guard.CONSTRAINED_TYPES, (
            f"`{name}` is declared {declared[name]!r}, which GitHub does not "
            f"constrain — it is interpolated into the step body, so it is now a "
            f"#1080 finding rather than a deliberate survivor"
        )


def test_the_whmcs_credential_reaches_the_step_only_through_the_kv_action():
    """This module's central claim, pinned rather than left as prose.

    The credential in reach of the injection point is exported by the PRECEDING
    `whmcs-secrets-from-kv` step through GITHUB_ENV. So a reviewer reading `env:`
    blocks (L213) and a sweep for `secrets.` in a `run:` body both score this
    file as holding nothing — while a payload here runs beside the live WHMCS
    secret.
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
        "the whmcs-secrets-from-kv step no longer precedes the injection point, "
        "so the credential is not in that step's environment — re-read this "
        "module's docstring before trusting it"
    )
    for name in CREDENTIAL_VARS:
        for step in steps:
            assert name not in (step.get("env") or {}), (
                f"{name} now appears in step {step.get('name')!r}'s env: block. "
                f"That is a better state, not a worse one — but this module's "
                f"claim is that it did NOT, so the docstring needs re-reading"
            )
        assert name not in (workflow["jobs"][JOB].get("env") or {})
    for step in steps:
        assert "secrets." not in (step.get("run") or ""), (
            f"step {step.get('name')!r} now references secrets.* in its run body; "
            f"a grep-for-secrets sweep would find this file, contradicting the "
            f"claim"
        )


# --------------------------------------------------------------------------
# Behaviour: the defect, and its absence
# --------------------------------------------------------------------------


def test_the_pre_fix_body_stole_the_whmcs_secret_and_exited_zero():
    """The positive control: the defect was real, and it left no trace.

    Without this, "the shipped body does not execute the payload" is a claim
    about a body that might never have executed anything. The three assertions
    together are the finding: the secret moved, the call still looked ordinary,
    and the step reported success.
    """
    out, stolen, rc = _run(_pre_fix(_subexpression_payload()), **{TOKEN_VAR: FAKE_TOKEN})
    assert stolen is not None, (
        f"the payload did not execute against the pre-fix body, so this control "
        f"proves nothing about the fix. Output: {out.strip()[:400]}"
    )
    assert stolen.strip() == FAKE_TOKEN, (
        f"the sentinel exists but does not hold the credential ({stolen!r}) — the "
        f"payload ran without reaching {TOKEN_VAR}, which is a weaker claim than "
        f"this control is making"
    )
    assert f"Domain=[{LEGAL_DOMAIN}]" in out, (
        f"the exploited run did not still bind a legal domain, so the theft would "
        f"have been visible in the log. Output: {out.strip()[:400]}"
    )
    assert rc == 0, (
        f"the exploited run exited {rc}, not 0 — the point of this control is that "
        f"nothing failed. Output: {out.strip()[:400]}"
    )


def test_the_shipped_body_binds_the_payload_as_data():
    """The same payload, supplied the way a dispatcher supplies it, is inert."""
    step = _step()
    _assert_wiring(step)
    payload = _subexpression_payload()
    out, stolen, rc = _run(
        _rendered(step["run"]), IN_DOMAIN=payload, **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert stolen is None, (
        f"the payload executed against the shipped body — the sentinel holds "
        f"{stolen!r}. Output: {out.strip()[:400]}"
    )
    assert f"Domain=[{payload}]" in out, (
        f"the payload did not arrive at the callee as a literal argument, so this "
        f"test is not measuring the remedy. Output: {out.strip()[:400]}"
    )
    assert rc == 0, f"the shipped body failed on a payload it should treat as data: {out.strip()[:400]}"


def test_the_shipped_body_still_passes_ordinary_inputs_through():
    """The common path: a real domain reaches the callee unchanged."""
    step = _step()
    _assert_wiring(step)
    out, stolen, rc = _run(
        _rendered(step["run"]), IN_DOMAIN=LEGAL_DOMAIN, **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert stolen is None
    assert rc == 0, f"the ordinary path failed: {out.strip()[:400]}"
    assert f"Domain=[{LEGAL_DOMAIN}] Execute=[False] ShowCode=[False]" in out, (
        f"the ordinary path no longer binds what it used to — a dry-run dispatch "
        f"must reach the callee with neither switch. Output: {out.strip()[:400]}"
    )


def test_an_empty_domain_fails_closed_and_says_so():
    step = _step()
    _assert_wiring(step)
    out, _, rc = _run(_rendered(step["run"]), IN_DOMAIN="", **{TOKEN_VAR: FAKE_TOKEN})
    assert rc == 1, f"an empty domain must fail closed, got rc={rc}: {out.strip()[:400]}"
    assert "::error::IN_DOMAIN is empty" in out, (
        f"the failure must name the cause so an operator can act on it. "
        f"Output: {out.strip()[:400]}"
    )
    # The stub announces itself with "CALLED" and never prints its own filename,
    # so `CALLEE not in out` is true on every path and a disjunction on it can
    # never fail -- the assertion would vouch for a guard that had let the call
    # through. Assert on what the stub actually emits.
    assert "CALLED" not in out, (
        f"the callee was invoked despite the empty guard. Output: {out.strip()[:400]}"
    )


def test_an_unset_domain_fails_closed_and_says_so():
    """A missing or misnamed `env:` mapping, which is the case the guard names."""
    step = _step()
    _assert_wiring(step)
    out, _, rc = _run(_rendered(step["run"]), **{TOKEN_VAR: FAKE_TOKEN})
    assert rc == 1, f"an unset domain must fail closed, got rc={rc}: {out.strip()[:400]}"
    assert "::error::IN_DOMAIN is empty" in out
    # Same check the empty case makes, for the same reason: rc and the message
    # together say the guard SPOKE, not that the call was actually stopped. Only
    # the stub's own marker says that. Binding precedes the script body, so a
    # callee that started and refused the argument also prints nothing here — the
    # claim is "the callee body never ran", and the `::error::` assertion above is
    # what separates the guard refusing from the binder refusing.
    assert "CALLED" not in out, (
        f"the callee was invoked despite the unset guard. Output: {out.strip()[:400]}"
    )


def test_without_the_guard_a_blank_fails_loudly():
    """The BOUND on L214 — this lane's one genuinely new measurement.

    Every earlier lane's guard closed a SILENT shift: an empty `$env:X` vanishes
    from a native command's argument list, `-Domain` binds the next token, and the
    call proceeds looking plausible. That cannot happen here, because the only
    other elements this body splats are SWITCHES and a switch is not a value.

    So the honest claim for 116 is narrower than 118's, and this test is what
    stops the next lane from restating 118's. Both blank states must fail LOUDLY
    and neither may reach the callee with a shifted binding.
    """
    step = _step()
    _assert_wiring(step)
    unguarded = _strip_guard(_rendered(step["run"]))

    out_empty, stolen_empty, rc_empty = _run(
        unguarded, IN_DOMAIN="", **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert rc_empty != 0, (
        f"an empty domain ran to success without the guard — that IS the silent "
        f"shift this module claims cannot happen here, so the docstring is wrong. "
        f"Output: {out_empty.strip()[:400]}"
    )
    assert EMPTY_REFUSAL in out_empty, (
        f"expected the binder to refuse the empty string by name. "
        f"Output: {out_empty.strip()[:400]}"
    )

    out_unset, _, rc_unset = _run(unguarded, **{TOKEN_VAR: FAKE_TOKEN})
    assert rc_unset != 0, (
        f"an unset domain ran to success without the guard. "
        f"Output: {out_unset.strip()[:400]}"
    )
    assert UNSET_REFUSAL in out_unset, (
        f"expected the binder to report the missing argument by name. "
        f"Output: {out_unset.strip()[:400]}"
    )

    # The discriminator against 118's shape: no run may bind a SWITCH as the value
    # of -Domain. That is what a shift would look like, and it is what does not
    # happen when every other element is a switch.
    for out in (out_empty, out_unset):
        assert "Domain=[-Execute" not in out and "Domain=[-ShowCode" not in out, (
            f"a switch was bound as the domain — the L214 shift DOES occur in this "
            f"shape and this module's bound is wrong. Output: {out.strip()[:400]}"
        )
    assert stolen_empty is None


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


# The CALL SITE (#1306, ledger L294). The `$env:IN_DOMAIN in body` assertion
# above is satisfied by this body's own fail-closed guard, which reads the
# variable before deciding whether to refuse — so it stays green on a body that
# guards the value and then passes a literal to the probe.
SPLAT = "@scriptArgs"
CALL_SITE = {"IN_DOMAIN": ("'-Domain', $domain", "$domain = $env:IN_DOMAIN")}


def test_the_domain_reaches_the_CALL_SITE_and_not_merely_the_body():
    """`$env:IN_DOMAIN in body` is true of the guard alone.

    This body reads the variable twice — once in `IsNullOrWhiteSpace` and once
    into `$domain` — which is exactly the shape #1306 measures: one read is the
    guard, so deleting the other leaves the weak assertion true. Replacing
    `@('-Domain', $domain)` with `@('-Domain', 'example.org')` would send every
    dispatch to the same domain and no other test in this module would notice,
    because the probe is read-only and the harness supplies its own fixture.
    """
    body = _step().get("run", "")
    invocation = pwsh_invocation(body, CALLEE)
    assert SPLAT in invocation, (
        f"the invocation of {CALLEE} must splat {SPLAT}. Invocation: {invocation!r}"
    )
    for var, (element, binding) in CALL_SITE.items():
        assert element in body, (
            f"{SPLAT} must be built with {element!r}, or {var} reaches the "
            f"callee through nothing. Body: {body!r}"
        )
        assert binding in body, (
            f"{binding!r} must be what fills that local, or the argument is "
            f"decoupled from {var}. Body: {body!r}"
        )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a pwsh subprocess; the wiring, free-text,
# credential-reachability and checker-agreement cases are pure YAML/AST and must
# run on a host with no pwsh (#1182 — a whole-module `shutil.which` gate turns
# "could not run" into "everything passed"). This set is scoped to exactly the
# cases that call `_run`.
NEEDS_PWSH = {
    "test_the_pre_fix_body_stole_the_whmcs_secret_and_exited_zero",
    "test_the_shipped_body_binds_the_payload_as_data",
    "test_the_shipped_body_still_passes_ordinary_inputs_through",
    "test_an_empty_domain_fails_closed_and_says_so",
    "test_an_unset_domain_fails_closed_and_says_so",
    "test_without_the_guard_a_blank_fails_loudly",
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
