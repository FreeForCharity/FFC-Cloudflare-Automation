"""Unit tests for the 222 / 224 twin lane of the #1080 burn-down.

222 and 224 are the same engine over two source lists: both splat a `$params`
hashtable onto `scripts/whmcs-product-alignment.ps1`, both run on `whmcs-prod`,
and both place `$0` AddOrder writes when `dry_run` is false. Each took one
free-text dispatch input, `product_id`, at one substitution point, single-quoted
inside the hashtable. Both now read `$env:IN_PRODUCT_ID`.

WHY ONE MODULE FOR TWO WORKFLOWS
    Not brevity. The defect that makes this lane worth measuring is only visible
    across the PAIR: the shared callee defaults `ProductId` to `'39'`, which is
    222's marker. A remedy that lets a blank fall through to that default is
    invisible on 222 forever and silently wrong on 224, whose product is pid 40.
    Every behavioural case below therefore runs against BOTH bodies, separately
    rendered from the tree (ledger L260 — a twin's measurement is not evidence
    about its sibling, because what a blank can shift into is a property of the
    surrounding splat, not of the remedy).

SINGLE QUOTES, SO THE USUAL PAYLOAD IS INERT
    pwsh does not expand `$( )` inside single quotes, so the suffix payload the
    double-quoted lanes (116, 702) used reports these bodies as harmless. The
    breakout that works closes the literal and continues the EXPRESSION:

        product_id = 39' + $($null = Set-Content -Path <sentinel> -Value $env:WHMCS_API_SECRET) + '

    which renders as `'39' + $(…) + ''` and evaluates to `39`, because `$null =`
    swallows the subexpression's output. Measured on pwsh 7.4.6 against each body
    as it shipped: the credential was written to the sentinel, the callee was
    still handed a legal `ProductId=[39]`, and the step exited **0**.
    `test_the_subexpression_payload_alone_is_inert_here` pins the inert half too,
    so a later reader who reaches for `$( )`, sees nothing happen, and concludes
    these sites were never exploitable is contradicted in the same file.

THE REMEDY IS FAIL-CLOSED, AND WHICH BLANK IT CATCHES IS MEASURED
    `$params` goes to a NATIVE command, whose argument rendering DROPS an empty
    element (ledger L214/L254), so an empty `ProductId` does not arrive empty — it
    does not arrive, and the next rendered argument binds in its place. Sampled 8
    times per twin with the guard removed and `IN_PRODUCT_ID` empty: four outcomes
    on 222, three on 224, none of them the dispatched value, most at exit 0. An
    UNSET mapping was deterministic (8/8) — and still bound `'39'`, the callee's
    default, which on 224 is the WRONG MARKER. So neither blank is safe here and
    they fail differently: the empty one nondeterministically, the unset one
    reliably and quietly. That is the argument for `exit 1` over 229's gated
    append, and `test_even_an_unset_product_id_reaches_the_callee_as_222s_marker`
    is what would notice if a later edit swapped the remedy back.
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

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GUARD_PATH = _REPO_ROOT / "scripts" / "check-workflow-input-interpolation.py"
_spec = importlib.util.spec_from_file_location("interp_guard", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

CALLEE = "whmcs-product-alignment.ps1"
ENVIRONMENT = "whmcs-prod"
ENV_VAR = "IN_PRODUCT_ID"
MAPPED_EXPRESSION = "${{ inputs.product_id }}"

# The credential in reach of both injection points, and the step that puts it
# there. That step is a bare `uses:` with no `name:`, so it is located by its
# `uses:` value rather than through find_step (which matches on name).
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
SENTINEL = "STOLEN-222-224.txt"

# The callee's own default for -ProductId, asserted against the script rather than
# restated: it is the value a dropped argument falls through to, and the whole
# reason a default fill or a gated append is the wrong remedy here.
CALLEE_DEFAULT_PRODUCT_ID = "39"

# How many times the nondeterministic L254 shift is sampled. 8 is the figure lane
# 18 used; it is a sample size, not a proof. The assertion is that at least one run
# of the unguarded body mis-binds, so more runs can only strengthen it.
SHIFT_RUNS = 8

# The interpolation each body deliberately KEEPS, and what GitHub substitutes.
# `dry_run` is `type: boolean`, so GitHub generates the value from the declared
# option and it cannot carry a payload — which is exactly why it stays. Rendering
# it is what makes a run of the shipped body a run of what actually ships.
CONSTRAINED_RENDERINGS = {"${{ inputs.dry_run }}": "true"}

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that omits
# it pins the wrong exit code and vouches for it (recorded on #1080's lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

# A stand-in for the real script that records what it was BOUND, which is the only
# discriminator that works here — a marker-string search over stdout is not one,
# because pwsh echoes the offending source line back in a ParserError, so any
# substring predicate matches the payload text on a run that executed nothing.
#
# `DomainsJson` keeps the real script's `Mandatory = $true` and `ProductId` keeps
# its default, because both are what a shifted binding runs into: the first turns
# a swallowed argument into a loud failure, the second into a quiet wrong answer.
STUB = """[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$DomainsJson,
    [Parameter()][string]$ProductId = '39',
    [Parameter()][string]$BillingCycle = 'free',
    [Parameter()][string]$PaymentMethod = 'mailin',
    [Parameter()][switch]$Execute,
    [Parameter()][string]$ApiUrl,
    [Parameter()][string]$Identifier,
    [Parameter()][string]$Secret,
    [Parameter()][string]$WelcomeEmailJson,
    [Parameter()][string]$OutputFile = 'artifacts/whmcs/whmcs_product_alignment.json'
)
Write-Output "CALLED ProductId=[$ProductId] DomainsJson=[$DomainsJson] Execute=[$Execute]"
"""

# The bodies as they shipped BEFORE the burn-down, verbatim from origin/main, with
# the one substitution point marked. They are the positive control: without them,
# "the fixed body does not execute the payload" is a claim about a body that might
# never have executed anything.
PRE_FIX_222 = """$ErrorActionPreference = 'Stop'
$params = @{
  DomainsJson      = 'artifacts/cloudflare/registrar_domains.json'
  ProductId        = 'PRODUCT_ID_HERE'
  ApiUrl           = $env:WHMCS_API_URL
  OutputFile       = 'artifacts/whmcs/whmcs_product_alignment.json'
  WelcomeEmailJson = 'config/whmcs-cloudflare-domain-email.json'
}
if ('true' -eq 'false') { $params.Execute = $true }
& pwsh -NoProfile -File ./scripts/whmcs-product-alignment.ps1 @params | Tee-Object -FilePath $env:GITHUB_STEP_SUMMARY -Append
if ($LASTEXITCODE -ne 0) { throw "Product alignment failed with exit code $LASTEXITCODE." }
"""

PRE_FIX_224 = """$ErrorActionPreference = 'Stop'
$params = @{
  DomainsJson = 'artifacts/github/github_pages_domains.json'
  ProductId   = 'PRODUCT_ID_HERE'
  ApiUrl      = $env:WHMCS_API_URL
  OutputFile  = 'artifacts/whmcs/whmcs_github_pages_alignment.json'
}
if ('true' -eq 'false') { $params.Execute = $true }
& pwsh -NoProfile -File ./scripts/whmcs-product-alignment.ps1 @params | Tee-Object -FilePath $env:GITHUB_STEP_SUMMARY -Append
if ($LASTEXITCODE -ne 0) { throw "Product alignment failed with exit code $LASTEXITCODE." }
"""

# The fail-closed check, as an anchor. Stripping it is how the L254 control is
# built, and its count is asserted before substituting (ledger L47).
GUARD_ANCHOR = "if ([string]::IsNullOrWhiteSpace($env:IN_PRODUCT_ID)) {"

TWINS = {
    "222-whmcs-product-alignment.yml": {
        "job": "align",
        "step": "Align product onto registrar-domain clients",
        "pre_fix": PRE_FIX_222,
        "domains_json": "artifacts/cloudflare/registrar_domains.json",
        "product_id": "39",
    },
    "224-whmcs-github-pages-product-alignment.yml": {
        "job": "align",
        "step": "Align product onto GitHub-Pages-site clients",
        "pre_fix": PRE_FIX_224,
        "domains_json": "artifacts/github/github_pages_domains.json",
        "product_id": "40",
    },
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _payload(legal: str) -> str:
    """The payload a SINGLE-quoted site takes: close the literal, concatenate."""
    return (
        legal
        + "' + $($null = Set-Content -Path "
        + SENTINEL
        + " -Value $env:"
        + TOKEN_VAR
        + ") + '"
    )


def _step(workflow: str) -> dict:
    spec = TWINS[workflow]
    return find_step(load_workflow(workflow), spec["job"], spec["step"])


def _dispatch_input_defaults(workflow: str) -> dict:
    """Each dispatch input's declared default.

    `on:` is the YAML 1.1 boolean True after safe_load (the Norway problem), so
    the obvious `doc["on"]` raises KeyError — and a version that swallowed that
    would report every default as absent and pass by inspecting nothing.
    """
    doc = load_workflow(workflow)
    triggers = doc.get("on", doc.get(True, {})) or {}
    inputs = (triggers.get("workflow_dispatch") or {}).get("inputs") or {}
    return {name: spec.get("default") for name, spec in inputs.items()}


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input this body reaches through a `${{ }}` expression.

    Deliberately the CHECKER's own two patterns rather than a substring test of
    this module's own devising, so a spelling the checker recognises cannot slip
    past, and the two cannot drift apart the way a restated rule does.
    """
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    return found


def _rendered(body: str) -> str:
    """The shipped body with GitHub's substitution performed, as GitHub does it.

    Running the RAW body instead is a silent trap: the literal text
    `'${{ inputs.dry_run }}'` is not `'true'`, so a test asserting the ordinary
    path measures a body GitHub never runs.
    """
    remaining = {e.strip() for e in guard._EXPRESSION.findall(body)}
    unknown = {e for e in remaining if "${{ " + e + " }}" not in CONSTRAINED_RENDERINGS}
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


def _pre_fix(workflow: str, product_id: str) -> str:
    """The pre-fix body with GitHub's substitution performed at its one site.

    The count is asserted before substituting (ledger L47): a `.replace` that
    stopped matching would leave the marker in place and the control would measure
    an unexploited body while reporting that the payload did not run.
    """
    body = TWINS[workflow]["pre_fix"]
    assert body.count("PRODUCT_ID_HERE") == 1, (
        f"{workflow}: the pre-fix body must carry exactly one call site; found "
        f"{body.count('PRODUCT_ID_HERE')}"
    )
    rendered = body.replace("PRODUCT_ID_HERE", product_id)
    assert "_HERE" not in rendered, f"substitution left a marker behind: {rendered!r}"
    return rendered


def _strip_guard(body: str) -> str:
    """Remove the fail-closed check, leaving the rest of the body intact.

    This is the L254 control, and it is the shape a lane "simplifying" the remedy
    would land. The anchor's count is asserted BEFORE the strip (ledger L47), so a
    reword fails loudly here instead of silently leaving the body unchanged and
    scoring the control — which expects to see a DEFECT — as reassurance.
    """
    assert body.count(GUARD_ANCHOR) == 1, (
        f"expected exactly one fail-closed check anchored on {GUARD_ANCHOR!r}, "
        f"found {body.count(GUARD_ANCHOR)} — this control cannot be applied, so it "
        f"would otherwise measure an unmodified body. Body: {body!r}"
    )
    lines = body.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if GUARD_ANCHOR in line)
    end = next(i for i in range(start, len(lines)) if lines[i].strip() == "}")
    stripped = "".join(lines[:start] + lines[end + 1 :])
    assert GUARD_ANCHOR not in stripped, (
        f"the guard survived the strip, so the control is measuring the guarded "
        f"body. Stripped: {stripped!r}"
    )
    assert "exit 1" not in stripped, (
        f"a fail-closed exit survived the strip: {stripped!r}"
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
        # Only what the test sets may be visible: an inherited IN_PRODUCT_ID would
        # make the blank cases pass for the wrong reason, and an inherited token
        # would let a theft assertion pass without the workflow supplying anything.
        for var in (ENV_VAR, TOKEN_VAR):
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
    return next((line.strip() for line in output.splitlines() if "CALLED" in line), "")


def _bound_product_id(output: str) -> str | None:
    """What the callee was actually bound, or None if it never ran."""
    called = _called_line(output)
    if not called:
        return None
    marker = "ProductId=["
    start = called.index(marker) + len(marker)
    return called[start : called.index("]", start)]


def _assert_wiring(workflow: str, step: dict) -> None:
    """The input travels in env, the body reads it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES this variable (ledger L199): delete the
    workflow's `env:` mapping and the step still sees it from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists.
    """
    env = step.get("env") or {}
    body = step.get("run", "")
    assert env.get(ENV_VAR) == MAPPED_EXPRESSION, (
        f"{workflow}: step {step.get('name')!r} must map {ENV_VAR} to "
        f"{MAPPED_EXPRESSION} — its env: mapping is {env!r}"
    )
    assert f"$env:{ENV_VAR}" in body, (
        f"{workflow}: the body must read $env:{ENV_VAR}; it does not: {body!r}"
    )
    assert "product_id" not in _interpolated_inputs(body), (
        f"{workflow}: product_id is still interpolated into the script body"
    )


# --------------------------------------------------------------------------
# Wiring — pure YAML, must run on a host with no pwsh
# --------------------------------------------------------------------------


def test_both_twins_map_product_id_through_step_level_env():
    for workflow in TWINS:
        _assert_wiring(workflow, _step(workflow))


def test_neither_body_interpolates_any_free_text_input():
    for workflow in TWINS:
        body = _step(workflow).get("run", "")
        interpolated = _interpolated_inputs(body)
        assert interpolated <= {"dry_run"}, (
            f"{workflow}: the body still interpolates {sorted(interpolated)}. Only "
            f"`dry_run` may remain, and only because it is type: boolean"
        )


def test_the_surviving_interpolation_is_declared_boolean():
    """`dry_run` stays in the body, so the claim that it cannot carry a payload
    rests on its DECLARATION, not on this module's say-so.

    Read through the guard's own accessor, not `workflow["on"]`: after
    yaml.safe_load, `on:` is the YAML 1.1 boolean True (the Norway problem), so
    the obvious spelling raises KeyError — and a version that swallowed it would
    report every input as absent and pass by inspecting nothing.
    """
    for workflow in TWINS:
        doc = load_workflow(workflow)
        declared = guard.dispatch_inputs(doc)
        free_text = guard.free_text_inputs(doc)
        assert declared.get("dry_run") == "boolean", (
            f"{workflow}: dry_run is interpolated into the body and is only safe "
            f"because GitHub constrains it; its declared type is "
            f"{declared.get('dry_run')!r}"
        )
        assert "dry_run" not in free_text, (
            f"{workflow}: dry_run is now free text and still interpolated"
        )
        assert "product_id" in free_text, (
            f"{workflow}: product_id is no longer a free-text input, so this "
            f"module is measuring the wrong thing: {sorted(free_text)}"
        )


def test_both_twins_fail_closed_on_a_blank_rather_than_defaulting():
    for workflow in TWINS:
        body = _step(workflow).get("run", "")
        assert GUARD_ANCHOR in body, (
            f"{workflow}: the fail-closed check is gone. A blank product_id is "
            f"dropped from the splat and the callee falls back to its own "
            f"default — see test_even_an_unset_product_id_reaches_the_callee_as_"
            f"222s_marker for why that is not acceptable here"
        )
        assert "exit 1" in body, f"{workflow}: the blank check does not exit 1"


def test_each_twin_declares_the_product_id_this_module_reasons_about():
    """TWINS' `product_id` is not decoration — the callee-default test below draws
    its whole conclusion from whether 224's value differs from the callee's `39`.
    Read it back out of the workflow, or this module keeps asserting a divergence
    that the tree may no longer have.

    Found by mutation review: changing 224's declared default to `'39'` left every
    other case in this module green, because nothing tied the table to the file.
    """
    for workflow, spec in TWINS.items():
        declared = _dispatch_input_defaults(workflow).get("product_id")
        assert declared == spec["product_id"], (
            f"{workflow} declares product_id default {declared!r}, but this module "
            f"reasons about {spec['product_id']!r}. If the workflow's product "
            f"changed, re-derive the lane's argument — in particular whether it "
            f"still differs from the callee's own default "
            f"{CALLEE_DEFAULT_PRODUCT_ID!r}, which is what makes a blank dangerous "
            f"in a way 222 cannot show you"
        )


def test_the_callee_default_is_222s_marker_which_is_why_neither_twin_may_default():
    """The premise of the fail-closed remedy, read from the callee rather than
    restated: `-ProductId` defaults to 39, so a dropped argument silently becomes
    "the Cloudflare registrar marker" — correct-looking on 222, wrong on 224."""
    source = (_REPO_ROOT / "scripts" / CALLEE).read_text(encoding="utf-8")
    needle = f"[string]$ProductId = '{CALLEE_DEFAULT_PRODUCT_ID}'"
    assert needle in source, (
        f"{CALLEE} no longer defaults -ProductId to "
        f"{CALLEE_DEFAULT_PRODUCT_ID!r}. That default is the whole reason both "
        f"twins fail closed instead of omitting the key; if it changed, re-derive "
        f"the remedy rather than updating this constant"
    )
    assert TWINS["222-whmcs-product-alignment.yml"]["product_id"] == (
        CALLEE_DEFAULT_PRODUCT_ID
    ), "222's own product id is expected to coincide with the callee default"
    assert TWINS["224-whmcs-github-pages-product-alignment.yml"]["product_id"] != (
        CALLEE_DEFAULT_PRODUCT_ID
    ), (
        "224's product id is expected to DIFFER from the callee default — that "
        "divergence is what makes the shared default dangerous"
    )


def test_the_credential_is_in_reach_and_named_in_no_env_block():
    """#1188 / ledger L213: the WHMCS credential arrives through GITHUB_ENV from
    the preceding composite action, so the injection point holds it while the
    file reads clean to both recommended sweeps."""
    for workflow in TWINS:
        doc = load_workflow(workflow)
        job = doc["jobs"][TWINS[workflow]["job"]]
        assert job.get("environment") == ENVIRONMENT, (
            f"{workflow}: the aligning job no longer enters {ENVIRONMENT} — the "
            f"credential model this module reasons about has changed"
        )
        steps = job["steps"]
        minting = [s for s in steps if s.get("uses") == TOKEN_STEP_USES]
        assert minting, (
            f"{workflow}: no {TOKEN_STEP_USES} step, so the credential reachability "
            f"this lane documents no longer holds"
        )
        assert minting[0].get("with", {}).get("scope") == "write", (
            f"{workflow}: the credential step is no longer write-scoped: "
            f"{minting[0]!r}"
        )
        raw = (
            _REPO_ROOT / ".github" / "workflows" / workflow
        ).read_text(encoding="utf-8")
        for var in CREDENTIAL_VARS:
            assert f"{var}:" not in raw, (
                f"{workflow}: {var} now appears as an env: key. That would be an "
                f"improvement in visibility, but this module's claim that an L213 "
                f"read scores the file as holding nothing is then stale"
            )
        # The syntax, not the substring: the step's own comment explains that a
        # `secrets.` grep reads clean, and a bare substring check fails on that
        # prose (#1019, and lane 18's second lesson).
        assert "${{ secrets." not in raw, (
            f"{workflow}: a secrets.* expression appeared, so the #1141 premise "
            f"recorded in the step comment is stale"
        )


def test_the_guard_no_longer_reports_either_twin():
    findings, unreadable, _ = guard.scan_all()
    assert not unreadable, f"the guard could not read: {unreadable}"
    current = guard.current_map(findings)
    for workflow in TWINS:
        assert workflow not in current, (
            f"{workflow} still interpolates a free-text dispatch input into a "
            f"script body"
        )
        assert workflow not in guard.KNOWN_UNGUARDED, (
            f"{workflow} was burned down but is still listed in KNOWN_UNGUARDED — "
            f"a stale entry, which the guard itself exits 1 on"
        )


# --------------------------------------------------------------------------
# Behaviour — the injection, both directions
# --------------------------------------------------------------------------


def test_the_pre_fix_bodies_stole_the_whmcs_secret_and_exited_zero():
    """The positive control. Without it, "the fixed body binds the payload as
    data" is a claim about a body that might never have executed anything."""
    for workflow, spec in TWINS.items():
        legal = spec["product_id"]
        output, stolen, rc = _run(
            _pre_fix(workflow, _payload(legal)), **{TOKEN_VAR: FAKE_TOKEN}
        )
        assert stolen is not None and stolen.strip() == FAKE_TOKEN, (
            f"{workflow}: the pre-fix body did NOT execute the payload — this "
            f"control is what makes the fixed-body assertions meaningful. "
            f"sentinel={stolen!r} output={output!r}"
        )
        called = _called_line(output)
        assert f"ProductId=[{legal}]" in called, (
            f"{workflow}: the injected run should still look ordinary — the callee "
            f"was expected to be handed ProductId=[{legal}]. called={called!r}"
        )
        assert rc == 0, (
            f"{workflow}: the injected step was expected to exit 0, which is what "
            f"made this invisible in the run log. rc={rc} output={output!r}"
        )


def test_the_subexpression_payload_alone_is_inert_here():
    """The trap this lane is named for: `$( )` does not expand in single quotes,
    so the payload the double-quoted lanes used reports these sites as SAFE."""
    for workflow, spec in TWINS.items():
        naive = spec["product_id"] + (
            "$($null = Set-Content -Path " + SENTINEL + " -Value $env:" + TOKEN_VAR + ")"
        )
        output, stolen, rc = _run(
            _pre_fix(workflow, naive), **{TOKEN_VAR: FAKE_TOKEN}
        )
        assert stolen is None, (
            f"{workflow}: the bare `$( )` payload executed inside SINGLE quotes, "
            f"which pwsh does not do. If this now fires, the site's quoting "
            f"changed and the lane's reasoning needs re-deriving. output={output!r}"
        )
        assert rc == 0 and naive in _called_line(output), (
            f"{workflow}: the inert payload should arrive at the callee verbatim. "
            f"rc={rc} output={output!r}"
        )


def test_the_shipped_bodies_bind_the_payload_as_data():
    for workflow, spec in TWINS.items():
        step = _step(workflow)
        _assert_wiring(workflow, step)
        payload = _payload(spec["product_id"])
        output, stolen, rc = _run(
            _rendered(step["run"]),
            **{ENV_VAR: payload, TOKEN_VAR: FAKE_TOKEN},
        )
        assert stolen is None, (
            f"{workflow}: the payload EXECUTED through the fixed body. "
            f"sentinel={stolen!r} output={output!r}"
        )
        assert payload in _called_line(output), (
            f"{workflow}: the payload should reach the callee verbatim, as inert "
            f"argv. called={_called_line(output)!r}"
        )
        assert rc == 0, f"{workflow}: rc={rc} output={output!r}"


def test_the_shipped_bodies_still_pass_an_ordinary_product_id_through():
    for workflow, spec in TWINS.items():
        step = _step(workflow)
        output, _stolen, rc = _run(
            _rendered(step["run"]), **{ENV_VAR: spec["product_id"]}
        )
        called = _called_line(output)
        assert rc == 0, f"{workflow}: rc={rc} output={output!r}"
        assert f"ProductId=[{spec['product_id']}]" in called, (
            f"{workflow}: the ordinary path must still bind the dispatched product "
            f"id. called={called!r}"
        )
        assert f"DomainsJson=[{spec['domains_json']}]" in called, (
            f"{workflow}: the work-list argument shifted. called={called!r}"
        )


# --------------------------------------------------------------------------
# Behaviour — the blank, which is the half the remedy turns on
# --------------------------------------------------------------------------


def test_a_blank_product_id_is_refused_before_the_callee_runs():
    """Empty, whitespace and unset all fail closed. Whitespace is not padding:
    `-ne ''` would pass it, which is the #1213 shape, and it renders as a
    non-empty argument the callee would accept as a product id."""
    for workflow in TWINS:
        step = _step(workflow)
        _assert_wiring(workflow, step)
        body = _rendered(step["run"])
        for label, overrides in (
            ("empty", {ENV_VAR: ""}),
            ("whitespace", {ENV_VAR: "   "}),
            ("unset", {}),
        ):
            output, _stolen, rc = _run(body, **overrides)
            assert rc != 0, (
                f"{workflow}: a {label} product_id did not fail the step. "
                f"rc={rc} output={output!r}"
            )
            assert _called_line(output) == "", (
                f"{workflow}: a {label} product_id reached the callee anyway, so "
                f"the check runs too late. output={output!r}"
            )


def test_without_the_guard_an_empty_product_id_shifts_the_binding():
    """Ledger L254, measured per twin rather than carried across (L260).

    Sampled because the defect is NONDETERMINISTIC: one green run of an
    unguarded body is not evidence of absence, and a module that ran it once
    would retire the reason the guard exists roughly a third of the time.
    """
    for workflow in TWINS:
        unguarded = _strip_guard(_rendered(_step(workflow)["run"]))
        bound = []
        for _ in range(SHIFT_RUNS):
            output, _stolen, _rc = _run(unguarded, **{ENV_VAR: ""})
            bound.append(_bound_product_id(output))
        assert not all(b == "" for b in bound), (
            f"{workflow}: every unguarded run bound an EMPTY ProductId, which "
            f"contradicts the measurement this lane is built on (the empty element "
            f"is dropped from a native command's argument rendering). Either the "
            f"invocation stopped being a native call or the host changed. "
            f"bound={bound!r}"
        )
        assert any(b not in ("", None) for b in bound), (
            f"{workflow}: no unguarded run reached the callee with a substituted "
            f"ProductId, so this control measured nothing. bound={bound!r}"
        )


def test_even_an_unset_product_id_reaches_the_callee_as_222s_marker():
    """The deterministic half, and the one that rules out omitting the key.

    An unset mapping is dropped entirely, so the callee falls back to its own
    default — `39`. On 222 that is the intended marker and would hide the defect
    forever; on 224, whose product is 40, it silently aligns the WRONG product
    under a credential an approver has just spent.
    """
    for workflow, spec in TWINS.items():
        unguarded = _strip_guard(_rendered(_step(workflow)["run"]))
        bound = []
        for _ in range(SHIFT_RUNS):
            output, _stolen, _rc = _run(unguarded)
            bound.append(_bound_product_id(output))
        assert set(bound) == {CALLEE_DEFAULT_PRODUCT_ID}, (
            f"{workflow}: an UNSET product_id was expected to bind the callee's "
            f"own default {CALLEE_DEFAULT_PRODUCT_ID!r} on every run — that "
            f"determinism is exactly what makes it quiet. bound={bound!r}"
        )
        if spec["product_id"] != CALLEE_DEFAULT_PRODUCT_ID:
            assert bound[0] != spec["product_id"], (
                f"{workflow}: the fallback bound this workflow's own product id, "
                f"which would make the shared default harmless. It is not: "
                f"bound={bound!r}"
            )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a pwsh subprocess; the wiring, declaration,
# credential-reachability and checker-agreement cases are pure YAML and must run
# on a host with no pwsh (#1182 — a whole-module `shutil.which` gate turns "could
# not run" into "everything passed"). This set is scoped to exactly the cases that
# call `_run`.
NEEDS_PWSH = {
    "test_a_blank_product_id_is_refused_before_the_callee_runs",
    "test_even_an_unset_product_id_reaches_the_callee_as_222s_marker",
    "test_the_pre_fix_bodies_stole_the_whmcs_secret_and_exited_zero",
    "test_the_shipped_bodies_bind_the_payload_as_data",
    "test_the_shipped_bodies_still_pass_an_ordinary_product_id_through",
    "test_the_subexpression_payload_alone_is_inert_here",
    "test_without_the_guard_an_empty_product_id_shifts_the_binding",
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
