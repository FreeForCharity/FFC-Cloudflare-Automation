"""Unit tests for 115's three dispatch-input call sites (#1080 lane 22).

115 exports WHMCS domains and then classifies, offline, which of them are ready
to transfer to Cloudflare Registrar. Its three free-text dispatch inputs used to
be interpolated into two different script bodies: `min_days_to_expiry` and
`post_reg_lock_days` into the preflight `run:` body, and `issue_number` into the
`actions/github-script` body that comments the summary back. All three now
arrive through step-level `env:`.

`type: number` IS FREE TEXT
    All three inputs are `type: number`, which is why #1080's own table — built
    over `type: string` — did not list this workflow at all, and why the freeze
    baseline is 36 rather than 34. GitHub constrains `boolean` and `choice` to a
    value it generated itself and constrains nothing else, so a `number` input
    carries arbitrary text to exactly the place a `string` one does.

TWO SHAPES IN ONE FILE, AND THE SECOND IS NOT A pwsh SHAPE
    The two pwsh sites sat UNQUOTED, in argument position of a native call, where
    `$( )` expands — so no quote breakout was needed. Measured on pwsh 7.4.6
    against the body as it shipped, with `min_days_to_expiry` =

        $(Write-Host '...'; $null = Set-Content -Path <sentinel> -Value "token=$env:GITHUB_TOKEN"; 15)

    the payload ran, the runner environment reached the sentinel, the callee was
    STILL handed a legal `MinDaysToExpiry=[15]`, and the step exited **0**.

    The `github-script` site is a different shape from every lane before it:
    `Number('${{ inputs.issue_number }}')` is SINGLE-quoted JavaScript, so it
    needed a quote breakout and nothing else. Measured on node with
    `719'); <payload>; Number('`: the payload ran with the step's authenticated
    Octokit, posted a comment of its own choosing, the legitimate summary comment
    still posted afterwards, and the step exited 0.

THE CREDENTIAL #1188's SWEEP CORRECTLY COULD NOT SEE
    115 never appeared in the credential-reachability index, and that was right:
    that sweep is defined over variables, and an `actions/github-script` token is
    not a variable in anyone's `env:`. The workflow declares `issues: write` at
    the top level and the `preflight` job declares no `permissions:` of its own,
    so the injected body inherits an Octokit that can write issues. It is the one
    credential this workflow's interpolation sites ever had in reach, and it is
    reachable only through the shape the variable sweep does not model.

THE GUARD CLOSES A HOLE THE REMEDY OPENS — THE OPPOSITE OF 116
    116 measured that its blank was already loud in both forms and recorded the
    guard as attribution rather than safety (ledger L260). Here the two blank
    forms SPLIT, and the split is created by the remedy. Measured on pwsh 7.4.6
    with the guard stripped:

        unset ($null)  the reference vanishes from the argument list; the callee's
                       binder refuses with `Missing an argument for parameter
                       'MinDaysToExpiry'`. rc 1. LOUD.
        empty ('')     the reference survives as an EMPTY argument, and `[int]''`
                       coerces to **0**. The callee runs with MinDaysToExpiry=0
                       and PostRegLockDays=0 and the step exits **0**. SILENT.

    Those two zeros are not cosmetic: they disable the expiry floor and the ICANN
    60-day post-registration lock, so an already-expired or just-registered domain
    is classified `ready` in a report whose entire purpose is deciding which
    domains to transfer. On the interpolated site BOTH forms were loud, because an
    empty substitution is no token at all. So moving the value into `env:` is what
    turns "argument absent" into "argument present and empty" — and the guard is
    load-bearing here, not attribution. `test_without_the_guard_an_empty_value_is_silently_zero`
    pins it so a later lane copying this shape does not drop the guard on 116's
    reasoning.
"""

from __future__ import annotations

import importlib.util
import json
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
_spec = importlib.util.spec_from_file_location("interp_guard_115", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

WORKFLOW = "115-domain-transfer-preflight.yml"
JOB = "preflight"
PWSH_STEP = "Run transfer-readiness preflight"
JS_STEP = "Comment summary back to issue"
CALLEE = "domain-transfer-preflight.ps1"

PWSH_MAPPINGS = {
    "IN_MIN_DAYS_TO_EXPIRY": "${{ inputs.min_days_to_expiry }}",
    "IN_POST_REG_LOCK_DAYS": "${{ inputs.post_reg_lock_days }}",
}
JS_MAPPINGS = {"IN_ISSUE_NUMBER": "${{ inputs.issue_number }}"}
PWSH_INPUTS = ("min_days_to_expiry", "post_reg_lock_days")
JS_INPUTS = ("issue_number",)

# The declared defaults the in-body fill must agree with. Read from the workflow
# rather than written down twice: the whole point of default-fill over
# fail-closed is that the body reproduces what `on.workflow_dispatch` promises,
# and a silent drift between the two is the failure this pins.
DEFAULT_FILLS = {
    "IN_MIN_DAYS_TO_EXPIRY": "min_days_to_expiry",
    "IN_POST_REG_LOCK_DAYS": "post_reg_lock_days",
}

# Deliberately NOT shaped like a real token: a value a scanner treats as a
# credential comes back REDACTED, and the one place it is printed is an assertion
# message on a failing run — exactly when the reader needs to see what the
# sentinel actually holds.
FAKE_TOKEN = "github-token-placeholder-not-a-real-token"
SENTINEL = "STOLEN-115.txt"

LEGAL_MIN = "15"
LEGAL_POST = "60"
LEGAL_ISSUE = "719"

# Every variable the harness must own outright: an inherited one could satisfy an
# assertion the workflow is supposed to.
CONTROLLED_VARS = (
    "IN_MIN_DAYS_TO_EXPIRY",
    "IN_POST_REG_LOCK_DAYS",
    "IN_ISSUE_NUMBER",
    "GITHUB_TOKEN",
)

GUARD_ANCHORS = (
    "if ([string]::IsNullOrWhiteSpace($env:IN_MIN_DAYS_TO_EXPIRY)) {",
    "if ([string]::IsNullOrWhiteSpace($env:IN_POST_REG_LOCK_DAYS)) {",
)

UNSET_REFUSAL = "Missing an argument for parameter 'MinDaysToExpiry'"

# The pwsh body as it shipped BEFORE the burn-down, verbatim from
# `origin/main:.github/workflows/115-domain-transfer-preflight.yml`. It is the
# positive control: without it, "the fixed body does not execute the payload" is
# a claim about a body that might never have executed anything.
PRE_FIX_PWSH = r"""$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path artifacts/transfer/runbooks | Out-Null

$ErrorActionPreference = 'Continue'
$out = & pwsh -NoProfile -File .\scripts\domain-transfer-preflight.ps1 `
  -WhmcsDomainsCsv artifacts/transfer/whmcs_domains.csv `
  -CloudflareZonesCsv artifacts/transfer/cf_zones.csv `
  -OutputFile artifacts/transfer/domain_transfer_preflight.csv `
  -RunbookDir artifacts/transfer/runbooks `
  -MinDaysToExpiry ${{ inputs.min_days_to_expiry }} `
  -PostRegLockDays ${{ inputs.post_reg_lock_days }}
$exit = $LASTEXITCODE
$ErrorActionPreference = 'Stop'
if ($exit -ne 0) { throw "domain-transfer-preflight.ps1 failed (exit $exit)." }

$summary = ($out | Out-String)
Write-Host $summary
$summary.TrimEnd() | Out-File -FilePath artifacts/transfer/preflight_summary.json -Encoding utf8
"""

# The injectable head of the github-script body as it shipped, verbatim. Only the
# head is reproduced: the tail that builds `body` and posts it is shared with the
# shipped version, so the control appends the CURRENT body's tail rather than a
# second copy that could drift from it.
PRE_FIX_JS_HEAD = "const issueNumber = Number('${{ inputs.issue_number }}');"

# A stand-in for the real script that records what it was BOUND. That is the only
# discriminator that works here — a marker search over stdout is not one, because
# pwsh echoes the offending source back in a binder error, so a substring
# predicate matches the payload text on a run that executed nothing.
STUB = """param(
    [Parameter()]
    [string]$WhmcsDomainsCsv,
    [Parameter()]
    [string]$CloudflareZonesCsv,
    [Parameter()]
    [int]$MinDaysToExpiry = 15,
    [Parameter()]
    [int]$PostRegLockDays = 60,
    [Parameter()]
    [string]$OutputFile,
    [Parameter()]
    [string]$RunbookDir
)
Write-Output '{"ready":1,"blocked":0,"review":0,"done":0,"total":1}'
[Console]::Error.WriteLine("CALLED Min=[$MinDaysToExpiry] Post=[$PostRegLockDays]")
"""

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that omits
# it pins the wrong exit code and vouches for it (recorded on #1080's lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

# `actions/github-script`'s own contract: the script text is the body of an async
# function called with `github`, `context`, `core`, `require` and friends in
# scope. The stubs record rather than act, and the final line is what lets a test
# distinguish "posted nothing" from "the harness threw before reaching the post".
JS_HARNESS = """const calls = [];
const failures = [];
const github = { rest: { issues: { createComment: async (a) => {
  calls.push({ issue_number: a.issue_number, body: String(a.body) });
} } } };
const context = {
  serverUrl: 'https://github.com',
  repo: { owner: 'FreeForCharity', repo: 'FFC-Cloudflare-Automation' },
  runId: 1,
};
const core = { setFailed: (m) => failures.push(String(m)) };
(async () => {
%s
})()
  .then(() => console.log('RESULT ' + JSON.stringify({ ok: true, calls, failures })))
  .catch((e) => console.log('RESULT ' + JSON.stringify({ ok: false, threw: String(e.message), calls, failures })));
"""


def _workflow() -> dict:
    return load_workflow(WORKFLOW)


def _pwsh_step() -> dict:
    return find_step(_workflow(), JOB, PWSH_STEP)


def _js_step() -> dict:
    return find_step(_workflow(), JOB, JS_STEP)


def _declared_inputs() -> dict:
    """`on.workflow_dispatch.inputs`, through the guard's own `on:` handling.

    `on:` parses to the YAML 1.1 boolean `True`, which is the Norway-problem trap
    the guard already solves — reusing it keeps the two readings identical.
    """
    return guard.dispatch_inputs(_workflow())


def _interpolated_inputs(body: str) -> set:
    """Every dispatch input this body reaches through a `${{ }}` expression.

    Deliberately the CHECKER's own two patterns rather than a substring test of
    this module's own devising, so a spelling the checker recognises
    (`${{ inputs . issue_number }}`, or one laundered through `format()`) cannot
    slip past the step-level assertion, and the two cannot drift apart the way a
    restated rule does.
    """
    found = set()
    for match in guard._EXPRESSION.finditer(body):
        found.update(guard._INPUT_REF.findall(match.group(1)))
    return found


def _assert_wiring(step: dict, mappings: dict, inputs: tuple, body: str) -> None:
    """The input travels in env, the body reads it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES these variables (ledger L199): delete the
    workflow's `env:` mapping and the step still sees them from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists.
    """
    env = step.get("env") or {}
    for var, expression in mappings.items():
        assert env.get(var) == expression, (
            f"step {step.get('name')!r} in job {JOB!r} must map {var} to "
            f"{expression} — its env: mapping is {env!r}"
        )
        reader = f"$env:{var}" if step.get("run") else f"process.env.{var}"
        assert reader in body, (
            f"step {step.get('name')!r} maps {var} but never reads {reader} — "
            f"the env: block is decoration and the value reaches nothing. "
            f"Body: {body!r}"
        )
    reintroduced = _interpolated_inputs(body) & set(inputs)
    assert not reintroduced, (
        f"step {step.get('name')!r} interpolates {sorted(reintroduced)} into its "
        f"script body again (#1080): GitHub substitutes that as raw text before "
        f"the body is parsed, so a dispatcher supplies code. Body: {body!r}"
    )


def _pre_fix_pwsh(min_days: str, post_reg: str) -> str:
    """The pre-fix pwsh body with GitHub's substitution performed, at BOTH sites.

    Counts are asserted before substituting (ledger L47): a `.replace` that
    stopped matching would leave the expressions in place and the control would
    measure an unexploited body while reporting that the payload did not run.
    """
    body = PRE_FIX_PWSH
    for expression, value in (
        ("${{ inputs.min_days_to_expiry }}", min_days),
        ("${{ inputs.post_reg_lock_days }}", post_reg),
    ):
        assert body.count(expression) == 1, (
            f"the pre-fix control must carry exactly one {expression}; found "
            f"{body.count(expression)}"
        )
        body = body.replace(expression, value)
    assert "${{" not in body, f"substitution left an expression behind: {body!r}"
    return body


def _pre_fix_js(issue_number: str) -> str:
    """The pre-fix github-script body: the old head plus the shipped tail.

    The tail is taken from the CURRENT body rather than copied, so the control
    cannot drift from what ships; only the head — the one line the burn-down
    changed — is reproduced. The shipped head is located by its guard, and its
    presence is asserted before removal (ledger L47).
    """
    shipped = _js_step()["with"]["script"]
    anchor = "const issueNumber = Number(process.env.IN_ISSUE_NUMBER);"
    assert shipped.count(anchor) == 1, (
        f"expected exactly one {anchor!r} in the shipped body to replace; found "
        f"{shipped.count(anchor)}. Body: {shipped!r}"
    )
    head, _, tail = shipped.partition(anchor)
    # Drop the shipped validity check with the shipped head: it is part of the
    # remedy, and leaving it in would make the control refuse the payload for the
    # reason under test rather than executing it.
    guard_start = tail.index("if (!Number.isInteger(issueNumber)")
    guard_end = tail.index("}", tail.index("return;", guard_start)) + 1
    body = head + PRE_FIX_JS_HEAD + tail[:guard_start] + tail[guard_end:]
    assert "Number.isInteger" not in body, (
        f"the control still carries the shipped validity check, so it is not "
        f"measuring the pre-fix body. Body: {body!r}"
    )
    assert body.count("${{ inputs.issue_number }}") == 1, (
        f"the control must carry exactly one interpolation to substitute; found "
        f"{body.count('${{ inputs.issue_number }}')}"
    )
    return body.replace("${{ inputs.issue_number }}", issue_number)


def _strip_pwsh_guards(body: str) -> str:
    """Remove both emptiness guards, asserting each was there.

    Counts are asserted BEFORE substituting (ledger L47): an anchor that stopped
    matching must fail loudly rather than silently leave the body unchanged and
    score the control as a pass.
    """
    for anchor, fill in zip(GUARD_ANCHORS, ("'15'", "'60'")):
        assert body.count(anchor) == 1, (
            f"expected exactly one {anchor!r} to strip, found "
            f"{body.count(anchor)} — this control would otherwise test an "
            f"unmodified body. Body: {body!r}"
        )
        start = body.index(anchor)
        end = body.index("}", body.index(fill, start)) + 1
        body = body[:start] + body[end:]
    # Count the ANCHOR, not the bare call name: the step body explains the guards
    # in a comment directly above them, so a `"IsNullOrWhiteSpace" not in body`
    # check fails on the PROSE describing the thing it looks for and reports the
    # control as broken over a correct strip (#1019).
    for anchor in GUARD_ANCHORS:
        assert anchor not in body, (
            f"a guard survived the strip, so the control is measuring the "
            f"guarded body. Stripped: {body!r}"
        )
    assert CALLEE in body, (
        f"the strip removed the invocation itself, so the control proves "
        f"nothing. Stripped: {body!r}"
    )
    return body


def _run_pwsh(body: str, **env_overrides: str):
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
        (tmp / "artifacts" / "transfer").mkdir(parents=True)
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8")
        # The body's last statements append to the job summary. Unset, that is
        # `Out-File -FilePath $null` under `ErrorActionPreference = 'Stop'`, so
        # every behavioural test would fail on the harness rather than the body.
        summary = tmp / "step-summary.md"
        env = child_env(
            GITHUB_STEP_SUMMARY=str(summary),
            GITHUB_TOKEN=FAKE_TOKEN,
            **env_overrides,
        )
        for var in CONTROLLED_VARS:
            if var not in env_overrides and var != "GITHUB_TOKEN":
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


def _run_js(body: str, **env_overrides: str):
    """Run a github-script body under node, with recording stubs.

    Returns (result_dict, sentinel_contents_or_None). The result dict is parsed
    from a single `RESULT` line rather than scraped from stdout, so a body that
    printed something resembling a success cannot be mistaken for one.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        artifacts = tmp / "artifacts" / "transfer"
        artifacts.mkdir(parents=True)
        # The real artifacts, so the CONTROL exercises the body's happy path
        # rather than three `catch` branches; the body must build a real comment
        # for "the legitimate comment still posted" to mean anything.
        (artifacts / "preflight_summary.json").write_text(
            json.dumps({"ready": 1, "blocked": 0, "review": 0, "done": 0, "total": 1}),
            encoding="utf-8",
        )
        (artifacts / "cat1_ready_in_cloudflare.md").write_text(
            "### Cat1-ready\n", encoding="utf-8"
        )
        (artifacts / "registry_truth.csv").write_text(
            "domain,bucket\nexample.org,ENOM_READY\n", encoding="utf-8"
        )
        indented = "\n".join("  " + line for line in body.splitlines())
        script = tmp / "step.js"
        script.write_text(JS_HARNESS % indented, encoding="utf-8")
        env = child_env(SENTINEL_PATH=str(tmp / SENTINEL), **env_overrides)
        for var in CONTROLLED_VARS:
            if var not in env_overrides:
                env.pop(var, None)
        proc = subprocess.run(
            ["node", str(script)],
            cwd=tmp,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
        )
        result = None
        for line in proc.stdout.splitlines():
            if line.startswith("RESULT "):
                result = json.loads(line[len("RESULT ") :])
        assert result is not None, (
            "the node harness produced no RESULT line, so this test measured the "
            f"harness rather than the body. stdout={proc.stdout!r} "
            f"stderr={proc.stderr!r}"
        )
        stolen = tmp / SENTINEL
        contents = stolen.read_text(encoding="utf-8") if stolen.exists() else None
        return result, contents


def _pwsh_payload() -> str:
    """The payload an UNQUOTED native-call argument takes: `$( )`, no breakout.

    `$null =` swallows the subexpression's output so the argument keeps a legal
    value — which is what made the exploited run indistinguishable from an
    ordinary one in the log, and therefore worth pinning.
    """
    return (
        "$($null = Set-Content -Path "
        + SENTINEL
        + " -Value $env:GITHUB_TOKEN; "
        + LEGAL_MIN
        + ")"
    )


def _js_payload() -> str:
    """The payload a SINGLE-quoted JS string takes: close it, run, reopen it.

    The trailing `Number('` pairs with the shipped line's own `');`, so the
    statement the payload leaves behind is syntactically complete — which is why
    the exploited run exits 0 instead of dying in the parser.
    """
    return (
        LEGAL_ISSUE
        + "'); require('fs').writeFileSync(process.env.SENTINEL_PATH, 'INJECTED-JS'); "
        + "await github.rest.issues.createComment({owner: context.repo.owner, "
        + "repo: context.repo.repo, issue_number: 1, body: 'pwned'}); Number('"
    )


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def test_the_two_pwsh_inputs_are_wired_through_env():
    step = _pwsh_step()
    _assert_wiring(step, PWSH_MAPPINGS, PWSH_INPUTS, step["run"])


def test_the_issue_number_input_is_wired_through_env():
    step = _js_step()
    _assert_wiring(step, JS_MAPPINGS, JS_INPUTS, step["with"]["script"])


def test_no_body_anywhere_in_115_interpolates_a_free_text_input():
    """Whole-file, not per-step: a lane that fixed two of three sites would pass
    a step-scoped assertion and leave the third executing dispatcher text."""
    free_text = {
        name
        for name, declared in _declared_inputs().items()
        if declared not in guard.CONSTRAINED_TYPES
    }
    assert free_text, (
        "115 declares no free-text dispatch input, so this test passes over "
        "nothing — the workflow's inputs were renamed or retyped"
    )
    offenders = {}
    for job_id, job in _workflow()["jobs"].items():
        for step in job.get("steps", []):
            body = step.get("run") or (step.get("with") or {}).get("script") or ""
            hit = _interpolated_inputs(body) & free_text
            if hit:
                offenders[f"{job_id}/{step.get('name', step.get('uses'))}"] = sorted(
                    hit
                )
    assert not offenders, f"free-text inputs interpolated into script bodies: {offenders}"


def test_the_if_condition_still_gates_on_the_raw_expression():
    """An expression context is NOT a script body and must not be 'fixed'.

    `if:` is evaluated by GitHub's own expression engine, which compares values
    rather than substituting text, so the raw `inputs.issue_number` there is
    correct. Pinned because the obvious over-application of this lane is to route
    that through `env.` as well, which would change when the step runs.
    """
    condition = _js_step().get("if", "")
    assert "inputs.issue_number" in condition, (
        f"the comment step must still gate on inputs.issue_number; its if: is "
        f"{condition!r}"
    )


def test_the_in_body_defaults_match_the_declared_defaults():
    """Default-fill only preserves behaviour while the two agree.

    The body hard-codes the fill values, so nothing but this test stops someone
    changing `default: 15` in `on.workflow_dispatch` and leaving the body filling
    15 for a blank — a drift that is invisible on every ordinary dispatch,
    because GitHub supplies the declared default and the fill never runs.
    """
    declared = _workflow()["on" if "on" in _workflow() else True]["workflow_dispatch"][
        "inputs"
    ]
    body = _pwsh_step()["run"]
    for var, input_name in DEFAULT_FILLS.items():
        expected = str(declared[input_name]["default"])
        assignment = f"$env:{var} = '{expected}'"
        assert assignment in body, (
            f"{input_name} declares default {expected!r}, so the blank-fill must "
            f"be {assignment!r} — the body would otherwise substitute a different "
            f"value than a dispatch that omits the input. Body: {body!r}"
        )


def test_the_js_body_refuses_a_non_positive_integer_before_calling_the_api():
    script = _js_step()["with"]["script"]
    assert "Number.isInteger(issueNumber)" in script, (
        "the github-script body must validate the issue number: a payload now "
        f"arrives as DATA, so the non-numeric case became reachable. Body: {script!r}"
    )
    assert script.index("core.setFailed") < script.index("createComment"), (
        "the validity check must precede the API call, or an invalid value "
        f"reaches Octokit as NaN first. Body: {script!r}"
    )


def test_the_checker_agrees_115_is_burned_down():
    assert WORKFLOW not in guard.KNOWN_UNGUARDED, (
        f"{WORKFLOW} is still in KNOWN_UNGUARDED. The freeze is exact in both "
        "directions, so a burned-down entry that is not deleted fails the guard "
        "as a stale entry."
    )
    findings, unreadable, _ = guard.scan_all()
    assert not unreadable, f"workflows that would not parse: {unreadable}"
    assert WORKFLOW not in guard.current_map(findings), (
        f"the checker still finds a free-text interpolation in {WORKFLOW}"
    )


# --------------------------------------------------------------------------
# Behaviour — pwsh
# --------------------------------------------------------------------------


def test_the_pre_fix_body_ran_the_payload_and_exited_zero():
    """Positive control. Without it, every claim below is about a body that
    might never have executed anything."""
    output, stolen, rc = _run_pwsh(_pre_fix_pwsh(_pwsh_payload(), LEGAL_POST))
    assert stolen is not None, (
        f"the pre-fix body did not execute the payload, so it is not a control "
        f"for the fixed body. Output: {output!r}"
    )
    assert FAKE_TOKEN in stolen, (
        f"the sentinel exists but does not hold the token, so the payload ran "
        f"without reaching the credential: {stolen!r}"
    )
    assert rc == 0, (
        f"the exploited run must exit 0 — a loud failure would be a different, "
        f"lesser finding. rc={rc} output={output!r}"
    )
    assert f"CALLED Min=[{LEGAL_MIN}]" in output, (
        f"the callee must still be handed a legal value, which is what makes the "
        f"exploited run indistinguishable from an ordinary one. Output: {output!r}"
    )


def test_the_shipped_body_binds_the_payload_as_data():
    step = _pwsh_step()
    _assert_wiring(step, PWSH_MAPPINGS, PWSH_INPUTS, step["run"])
    output, stolen, rc = _run_pwsh(
        step["run"],
        IN_MIN_DAYS_TO_EXPIRY=_pwsh_payload(),
        IN_POST_REG_LOCK_DAYS=LEGAL_POST,
    )
    assert stolen is None, (
        f"the payload executed through the fixed body: {stolen!r}. Output: {output!r}"
    )
    assert "CALLED" not in output, (
        f"the callee was invoked with a payload-derived value; the binder should "
        f"have refused it as data. Output: {output!r}"
    )
    assert rc != 0, f"a refused value must fail the step. rc={rc} output={output!r}"
    assert "MinDaysToExpiry" in output, (
        f"the failure must name the parameter that refused the value, or it is "
        f"indistinguishable from a harness failure. Output: {output!r}"
    )


def test_the_shipped_body_still_passes_ordinary_values_through():
    step = _pwsh_step()
    output, stolen, rc = _run_pwsh(
        step["run"],
        IN_MIN_DAYS_TO_EXPIRY=LEGAL_MIN,
        IN_POST_REG_LOCK_DAYS=LEGAL_POST,
    )
    assert stolen is None, f"an ordinary run wrote the sentinel: {stolen!r}"
    assert rc == 0, f"an ordinary run must succeed. rc={rc} output={output!r}"
    assert f"CALLED Min=[{LEGAL_MIN}] Post=[{LEGAL_POST}]" in output, (
        f"both values must reach the callee unchanged. Output: {output!r}"
    )


def test_an_empty_value_falls_back_to_the_declared_default():
    step = _pwsh_step()
    output, _, rc = _run_pwsh(
        step["run"], IN_MIN_DAYS_TO_EXPIRY="", IN_POST_REG_LOCK_DAYS=""
    )
    assert rc == 0, f"a blank must use the declared default, not fail. rc={rc} {output!r}"
    assert f"CALLED Min=[{LEGAL_MIN}] Post=[{LEGAL_POST}]" in output, (
        f"a blank must reach the callee as the DECLARED default, never as the "
        f"`[int]''` coercion to 0. Output: {output!r}"
    )


def test_an_unset_variable_falls_back_to_the_declared_default():
    step = _pwsh_step()
    output, _, rc = _run_pwsh(step["run"])
    assert rc == 0, f"an unset mapping must use the declared default. rc={rc} {output!r}"
    assert f"CALLED Min=[{LEGAL_MIN}] Post=[{LEGAL_POST}]" in output, (
        f"an unset mapping must reach the callee as the declared default. "
        f"Output: {output!r}"
    )


def test_without_the_guard_an_empty_value_is_silently_zero():
    """The bound this lane adds, and the reason the guard is not decoration.

    116 measured that its blank was loud in both forms and recorded the guard as
    attribution (ledger L260). Here the forms SPLIT, and the split is created by
    the remedy: through `env:` an empty value survives as an empty ARGUMENT,
    which `[int]` coerces to 0 — disabling the expiry floor and the ICANN
    60-day lock while the step exits 0. Pinned so a later lane copying this shape
    does not drop the guard on 116's reasoning.
    """
    stripped = _strip_pwsh_guards(_pwsh_step()["run"])

    output, _, rc = _run_pwsh(
        stripped, IN_MIN_DAYS_TO_EXPIRY="", IN_POST_REG_LOCK_DAYS=""
    )
    assert rc == 0 and "CALLED Min=[0] Post=[0]" in output, (
        "the unguarded EMPTY case must be the silent zero-coercion this guard "
        f"exists for; if it has become loud, the guard's justification in the "
        f"workflow needs re-measuring. rc={rc} output={output!r}"
    )

    output, _, rc = _run_pwsh(stripped)
    assert rc != 0 and UNSET_REFUSAL in output, (
        "the unguarded UNSET case must be the loud argument-drop (ledger L214); "
        f"if it has become quiet the hazard is wider than documented. rc={rc} "
        f"output={output!r}"
    )


# --------------------------------------------------------------------------
# Behaviour — github-script
# --------------------------------------------------------------------------


def test_the_pre_fix_js_body_ran_the_payload_and_still_posted():
    """Positive control for the second shape. The 'and still posted' half is the
    point: the legitimate comment landing afterwards is what made the exploited
    run look entirely ordinary."""
    result, stolen = _run_js(_pre_fix_js(_js_payload()))
    assert stolen == "INJECTED-JS", (
        f"the pre-fix body did not execute the JS payload, so it is not a "
        f"control for the fixed body. sentinel={stolen!r} result={result!r}"
    )
    assert result["ok"], f"the exploited run must not throw: {result!r}"
    posted = [c["issue_number"] for c in result["calls"]]
    assert 1 in posted, (
        f"the payload's own createComment did not reach the stub, so the "
        f"credential half of this control proves nothing: {result!r}"
    )
    assert int(LEGAL_ISSUE) in posted, (
        f"the legitimate comment must still post after the payload — that is "
        f"what makes the exploited run indistinguishable in the log: {result!r}"
    )


def test_the_shipped_js_body_refuses_the_payload_and_calls_nothing():
    step = _js_step()
    _assert_wiring(step, JS_MAPPINGS, JS_INPUTS, step["with"]["script"])
    result, stolen = _run_js(
        step["with"]["script"], IN_ISSUE_NUMBER=_js_payload()
    )
    assert stolen is None, f"the payload executed through the fixed body: {stolen!r}"
    assert result["calls"] == [], (
        f"nothing may be posted for a value that is not an issue number: {result!r}"
    )
    assert result["failures"], (
        f"the step must fail the job rather than silently doing nothing, or a "
        f"bad dispatch is indistinguishable from a skipped one: {result!r}"
    )
    assert "IN_ISSUE_NUMBER" in result["failures"][0], (
        f"the failure must name the variable, so the cause is the step's own "
        f"env: mapping rather than an Octokit error: {result!r}"
    )


def test_the_shipped_js_body_posts_for_an_ordinary_issue_number():
    result, stolen = _run_js(
        _js_step()["with"]["script"], IN_ISSUE_NUMBER=LEGAL_ISSUE
    )
    assert stolen is None, f"an ordinary run wrote the sentinel: {stolen!r}"
    assert result["failures"] == [], f"an ordinary run must not fail: {result!r}"
    assert [c["issue_number"] for c in result["calls"]] == [int(LEGAL_ISSUE)], (
        f"exactly one comment, on the requested issue: {result!r}"
    )
    assert "ready: **1**" in result["calls"][0]["body"], (
        f"the comment must carry the preflight summary, or this test passes over "
        f"a body that posted an empty shell: {result!r}"
    )


def test_a_non_numeric_issue_number_is_refused_by_name():
    """The case the remedy makes REACHABLE. Before it, such a value was executed
    rather than read, so there was nothing for a validity check to catch."""
    result, _ = _run_js(_js_step()["with"]["script"], IN_ISSUE_NUMBER="not-a-number")
    assert result["calls"] == [], f"nothing may be posted: {result!r}"
    assert result["failures"] and "not-a-number" in result["failures"][0], (
        f"the failure must quote the offending value: {result!r}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a subprocess; the wiring and checker-agreement
# cases are pure YAML/AST and must run on a host with neither tool installed
# (#1182 — a whole-module `shutil.which` gate turns "could not run" into
# "everything passed"). These sets are scoped to exactly the cases that call
# `_run_pwsh` / `_run_js`.
NEEDS_PWSH = {
    "test_the_pre_fix_body_ran_the_payload_and_exited_zero",
    "test_the_shipped_body_binds_the_payload_as_data",
    "test_the_shipped_body_still_passes_ordinary_values_through",
    "test_an_empty_value_falls_back_to_the_declared_default",
    "test_an_unset_variable_falls_back_to_the_declared_default",
    "test_without_the_guard_an_empty_value_is_silently_zero",
}
NEEDS_NODE = {
    "test_the_pre_fix_js_body_ran_the_payload_and_still_posted",
    "test_the_shipped_js_body_refuses_the_payload_and_calls_nothing",
    "test_the_shipped_js_body_posts_for_an_ordinary_issue_number",
    "test_a_non_numeric_issue_number_is_refused_by_name",
}

if __name__ == "__main__":
    have_pwsh = shutil.which("pwsh") is not None
    have_node = shutil.which("node") is not None
    failures = 0
    for t in TESTS:
        if t.__name__ in NEEDS_PWSH and not have_pwsh:
            print(f"  SKIP {t.__name__} (pwsh not installed; runs in CI)")
            continue
        if t.__name__ in NEEDS_NODE and not have_node:
            print(f"  SKIP {t.__name__} (node not installed; runs in CI)")
            continue
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
