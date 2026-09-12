"""601's `output_file` travels in `env:`, and the blank it now refuses.

    #1080 lane 20, and the one that retires the WRITE half of the freeze: 601
    was the last entry entering an environment not suffixed `-read`.

    WHAT MAKES THIS LANE DIFFERENT FROM ITS PREDECESSORS

    Every earlier [W] lane was a workflow that means to write. This one does
    not: `601. WPMUDEV - Export Sites/Domains (Read-only)` is an export, and it
    is a [W] only because its environment is named `wpmudev-prod` rather than
    `wpmudev-prod-read`. That makes it the obvious candidate for "the rule
    over-reported once", and it is not one — the body reaches a live WPMUDEV Hub
    API token whose scope is the Hub account, and an injected payload spends it
    after the approver does. Measured, not argued: the pre-fix body wrote that
    token to a sentinel, handed the exporter a legal
    `OutputFile=[wpmudev_domains.csv]`, and exited 0.

    It is also the EASY shape, which is why it outlived the hard ones. The
    credential is named in the injected step's own `env:`, so an L213 read and a
    `secrets.` grep (#1141) both find it without needing #1188's reachability
    index — the lanes that needed that index were done first.

    THE BLANK, AND WHERE IT BOUNDS L254 RATHER THAN CONTRADICTING IT

    L254 is about a hashtable SPLATTED onto a native command: unordered by
    definition, so a blank value vanishes and a later parameter absorbs the gap.
    This site is not that shape — it is one explicit `-OutputFile $out` — so
    L254's conclusion does not reach it, and the measured behaviour differs. On
    pwsh 7.4.6 the two blank forms fail in OPPOSITE directions, 8/8 each:

      EMPTY   ''     PRESERVED as an argument. The callee bound `OutputFile=[]`,
                     its own 'wpmudev_domains.csv' default never applied, its
                     write failed on an empty path — and the step exited **0**,
                     because this body never checks $LASTEXITCODE after the
                     native call. A silent no-op export.
      UNSET   $null  DROPPED, the way L254's empty splat element vanishes:
                     `Missing an argument for parameter 'OutputFile'`, then a
                     terminating `Test-Path $null`. Loud.

    So the dangerous blank is the quiet one, and it is the one a dispatch form
    produces when someone clears a pre-filled box. `IsNullOrWhiteSpace` covers it
    and the all-spaces case (which bound `OutputFile=[   ]` at exit 0); `-ne ''`
    would pass the latter (#1213).

    NOT FIXED HERE, ON PURPOSE: the missing `$LASTEXITCODE` check after the
    native call is what makes the empty case silent rather than red, and it is a
    defect in its own right — an exporter that fails for ANY reason exits 0. It
    is out of this lane's scope (which is the interpolation) and is called out in
    the PR rather than bundled in. The tests below therefore PIN the exit-0
    behaviour as the measured status quo; if a later fix makes it exit non-zero,
    `test_the_unguarded_empty_value_is_preserved_and_exits_zero` is the test that
    should be updated, and its failure is the good kind.
"""

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

WORKFLOW = "601-wpmudev-export-sites.yml"
JOB = "export"
STEP = "Run export"
CALLEE = "wpmudev-sites-export.ps1"
ENVIRONMENT = "wpmudev-prod"
ENV_VAR = "IN_OUTPUT_FILE"
MAPPED_EXPRESSION = "${{ inputs.output_file }}"
INPUT_NAME = "output_file"

# The credential in reach. Unlike the GITHUB_ENV lanes it is in the injected
# step's OWN `env:`, which is what this module asserts rather than assumes.
TOKEN_VAR = "WPMUDEV_API_TOKEN"

# Deliberately NOT shaped like a real secret: a value a scanner treats as a
# credential comes back REDACTED, and the one place it is printed is an assertion
# message on a failing run — exactly when the reader needs to see whether the
# sentinel holds the credential or an empty string.
FAKE_TOKEN = "wpmudev-token-placeholder-not-a-real-token"
SENTINEL = "STOLEN-601.txt"

# The callee's own default for -OutputFile, read from the script rather than
# restated: it is the value a DROPPED argument would fall through to, and the
# measurement below turns on the empty string NOT reaching it.
CALLEE_DEFAULT_OUTPUT_FILE = "wpmudev_domains.csv"

# How many times each blank form is sampled. Both were deterministic here, unlike
# 222/224's four-outcome shift — but determinism is a result, not an assumption,
# and sampling is how it stays one.
BLANK_RUNS = 8

# GitHub's `shell: pwsh` wrapper, from Runner.Worker/Handlers/ScriptHandlerHelpers.cs.
# The appended line is what makes a failed NATIVE call fail the step; a bare
# `pwsh -File body.ps1` reports 0 while $LASTEXITCODE is 1, so a module that omits
# it pins the wrong exit code and vouches for it (recorded on #1080's lane 9).
RUNNER_PREAMBLE = "$ErrorActionPreference = 'stop'\n"
RUNNER_EPILOGUE = (
    "\nif ((Test-Path -LiteralPath variable:\\LASTEXITCODE)) { exit $LASTEXITCODE }\n"
)

# The body opens with a diagnostics call to the live WPMUDEV Hub. Running it as
# shipped makes every behavioural case below depend on wpmudev.com being
# reachable AND on it answering the same way from a runner as from here — a test
# that measures someone else's uptime. It is redirected to a closed local port,
# which takes the SAME catch branch (the block swallows every failure and prints
# a diagnostic), so control flow through the call site is unchanged.
#
# Asserted before substituting (ledger L47): a reworded base URL must fail here
# loudly rather than leave the live call in place and quietly re-introduce the
# network dependency.
LIVE_BASE = "$base = 'https://wpmudev.com/api/hub/v1'"
OFFLINE_BASE = "$base = 'http://127.0.0.1:1/hub/v1'"

# A stand-in for the real exporter that records what it was BOUND — the only
# discriminator that works here, because a marker-string search over stdout is
# not one: pwsh echoes the offending source line back in a ParserError, so any
# substring predicate matches the payload text on a run that executed nothing.
#
# It keeps the real script's `-OutputFile` default and then WRITES that file,
# because the body's summary half reads it back; a stub that only printed would
# make every run report `output file not found` and hide the difference between
# a good path and an empty one.
STUB = """[CmdletBinding()]
param(
    [Parameter()][string]$ApiToken,
    [Parameter()][string]$BaseUrl = 'https://wpmudev.com/api',
    [Parameter()][string]$OutputFile = 'wpmudev_domains.csv',
    [Parameter()][ValidateRange(1, 100)][int]$PerPage = 100
)
Write-Output "CALLED OutputFile=[$OutputFile] PerPage=[$PerPage]"
Set-Content -Path $OutputFile -Value 'domain,sitesCount'
"""


def _shipped_body() -> str:
    """The step body as it ships, from the tree — never a copy kept here."""
    return find_step(load_workflow(WORKFLOW), JOB, STEP).get("run", "")


def _offline(body: str) -> str:
    assert body.count(LIVE_BASE) == 1, (
        f"expected exactly one diagnostics base URL matching {LIVE_BASE!r}, found "
        f"{body.count(LIVE_BASE)} — this substitution would otherwise leave the "
        f"LIVE call in place and every behavioural case would depend on "
        f"wpmudev.com. Body: {body!r}"
    )
    swapped = body.replace(LIVE_BASE, OFFLINE_BASE)
    assert "wpmudev.com/api/hub" not in swapped, (
        f"the live hub URL survived the swap: {swapped!r}"
    )
    return swapped


# The body as it shipped BEFORE the burn-down, verbatim from origin/main at
# 7b52aa6, with the one substitution point marked. It is the positive control:
# without it, "the fixed body does not execute the payload" is a claim about a
# body that might never have executed anything.
PRE_FIX = """Write-Host "::group::WPMUDEV Diagnostics"
try {
  $base = 'https://wpmudev.com/api/hub/v1'
  $headers = @{ AUTHORIZATION = $env:WPMUDEV_API_TOKEN }
  $uri = "$base/account"
  Write-Host "GET $uri"
  Invoke-RestMethod -Method Get -Uri $uri -Headers $headers -ErrorAction Stop | Out-Null
  Write-Host "Diagnostics: success"
}
catch {
  Write-Host "Diagnostics: request failed"
}
Write-Host "::endgroup::"

$out = 'OUTPUT_FILE_HERE'
pwsh -NoProfile -File scripts/wpmudev-sites-export.ps1 -OutputFile $out

Write-Host "::group::WPMUDEV Summary"
if (Test-Path $out) {
  Write-Host "Summary: ok"
}
else {
  Write-Host "Summary: output file not found: $out"
}
Write-Host "::endgroup::"
"""

# The fail-closed check, as an anchor. Stripping it is how the control for the
# blank cases is built, and its count is asserted before substituting (L47).
GUARD_ANCHOR = "if ([string]::IsNullOrWhiteSpace($env:IN_OUTPUT_FILE)) {"


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


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


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


def _pre_fix(value: str) -> str:
    """The pre-fix body with GitHub's substitution performed at its one site."""
    assert PRE_FIX.count("OUTPUT_FILE_HERE") == 1, (
        f"the pre-fix body must carry exactly one call site; found "
        f"{PRE_FIX.count('OUTPUT_FILE_HERE')}"
    )
    rendered = PRE_FIX.replace("OUTPUT_FILE_HERE", value)
    assert "_HERE" not in rendered, f"substitution left a marker behind: {rendered!r}"
    return _offline(rendered)


def _strip_guard(body: str) -> str:
    """Remove the fail-closed check, leaving the rest of the body intact.

    This is the control for every blank case: it is the shape a lane
    "simplifying" the remedy would land. The anchor's count is asserted BEFORE
    the strip (L47), so a reword fails loudly here instead of silently leaving
    the body unchanged and scoring a control that expects a DEFECT as
    reassurance.
    """
    assert body.count(GUARD_ANCHOR) == 1, (
        f"expected exactly one fail-closed check anchored on {GUARD_ANCHOR!r}, "
        f"found {body.count(GUARD_ANCHOR)} — this control cannot be applied, so "
        f"it would otherwise measure an unmodified body. Body: {body!r}"
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


def _run(body: str, *, stub: str = None, extra_files: dict = None, **env_overrides: str):
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
        (tmp / "scripts" / CALLEE).write_text(
            STUB if stub is None else stub, encoding="utf-8"
        )
        for name, contents in (extra_files or {}).items():
            (tmp / name).write_text(contents, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(RUNNER_PREAMBLE + body + RUNNER_EPILOGUE, encoding="utf-8")
        env = child_env(**env_overrides)
        # Only what the test sets may be visible: an inherited IN_OUTPUT_FILE
        # would make the blank cases pass for the wrong reason, and an inherited
        # token would let a theft assertion pass without the workflow supplying
        # anything.
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


def _bound_output_file(output: str) -> str | None:
    """What the callee was actually bound, or None if it never ran."""
    called = next(
        (line.strip() for line in output.splitlines() if "CALLED" in line), ""
    )
    if not called:
        return None
    marker = "OutputFile=["
    start = called.index(marker) + len(marker)
    return called[start : called.index("]", start)]


def _assert_wiring(step: dict) -> None:
    """The input travels in env, the body reads it, and it is not interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES this variable (ledger L199): delete the
    workflow's `env:` mapping and the step still sees it from the harness, so
    every behavioural test below would keep passing over plumbing that no longer
    exists.
    """
    env = step.get("env") or {}
    body = step.get("run", "")
    assert env.get(ENV_VAR) == MAPPED_EXPRESSION, (
        f"step {step.get('name')!r} must map {ENV_VAR} to {MAPPED_EXPRESSION} — "
        f"its env: mapping is {env!r}"
    )
    assert f"$env:{ENV_VAR}" in body, (
        f"the body must read $env:{ENV_VAR}; it does not: {body!r}"
    )
    assert INPUT_NAME not in _interpolated_inputs(body), (
        f"{INPUT_NAME} is still interpolated into the script body"
    )


# --------------------------------------------------------------------------
# Wiring — pure YAML, must run on a host with no pwsh
# --------------------------------------------------------------------------


def test_the_output_file_input_travels_in_env_not_in_the_body():
    _assert_wiring(_step())


def test_the_body_no_longer_interpolates_any_free_text_input():
    """Not just `output_file` — any of them. A lane that moved the one input it
    was named after, while leaving a sibling interpolated, would satisfy the
    narrower assertion above and leave the workflow in the freeze."""
    free_text = set(guard.free_text_inputs(load_workflow(WORKFLOW)))
    still = _interpolated_inputs(_step().get("run", "")) & free_text
    assert not still, f"the body still interpolates free-text input(s): {sorted(still)}"


def test_output_file_is_free_text_which_is_what_makes_it_a_finding():
    """A `choice`/`boolean` input could not carry a payload; this one can.

    Read from the declaration rather than asserted in prose, so a later change
    of `type:` retires this lane's premise loudly instead of leaving a test that
    guards a value GitHub now constrains.
    """
    free_text = guard.free_text_inputs(load_workflow(WORKFLOW))
    assert INPUT_NAME in free_text, (
        f"{INPUT_NAME} is no longer a free-text dispatch input (free-text set: "
        f"{sorted(free_text)}) — this lane's premise has changed"
    )


def test_the_fail_closed_check_precedes_the_diagnostics_call():
    """Placement is the point, not presence: a blank must cost nothing.

    The check sits before the Hub call, so a blank dispatch refuses without
    spending a credentialled request. A later edit that moves it below the
    diagnostics block would still refuse, and would still pass a presence test.
    """
    body = _step().get("run", "")
    assert GUARD_ANCHOR in body, f"the fail-closed check is gone: {body!r}"
    assert body.index(GUARD_ANCHOR) < body.index("WPMUDEV Diagnostics"), (
        "the fail-closed check must precede the diagnostics call so a blank "
        f"dispatch spends nothing. Body: {body!r}"
    )


def test_the_environment_is_the_one_that_made_this_a_write_entry():
    """601 is a [W] only because `wpmudev-prod` lacks a `-read` suffix.

    Pinned because the lane's whole argument turns on it: if the environment is
    ever renamed (a repo-admin change this lane deliberately did not make), the
    classification changes and the note in the guard should be re-read rather
    than inherited.
    """
    envs = guard.environments(load_workflow(WORKFLOW))
    assert ENVIRONMENT in envs, f"expected {ENVIRONMENT} in {envs}"
    assert guard.is_write_environment(ENVIRONMENT), (
        f"{ENVIRONMENT} no longer reads as a write environment — if it was "
        f"renamed to a `-read` suffix, this lane's [W] framing is stale"
    )


def test_the_credential_is_in_the_injected_steps_own_env():
    """What distinguishes this lane from the GITHUB_ENV ones — asserted, not said.

    Every earlier lane's note explains that the credential arrives invisibly.
    Here it does not, and that is the claim: an L213 read of this step's own
    `env:` finds the token. If a later refactor moves it to a composite action,
    this test fails and the note that calls this "the easy-to-see shape" stops
    being true.
    """
    env = _step().get("env") or {}
    assert TOKEN_VAR in env, (
        f"{TOKEN_VAR} is no longer in the injected step's own env: {env!r}"
    )
    assert "secrets." in str(env[TOKEN_VAR]), (
        f"{TOKEN_VAR} no longer reads from secrets.*: {env[TOKEN_VAR]!r}"
    )


def test_the_callee_default_is_not_what_an_empty_value_falls_through_to():
    """Read the exporter's own default, so the measurement below has a foil.

    A reader who knows only that the callee defaults to 'wpmudev_domains.csv'
    would reasonably conclude a blank is harmless. The behavioural test shows it
    is not, because the empty string is PRESERVED rather than dropped — so the
    default never applies. This pins the default that intuition rests on.
    """
    source = (_REPO_ROOT / "scripts" / CALLEE).read_text(encoding="utf-8")
    assert f"$OutputFile = '{CALLEE_DEFAULT_OUTPUT_FILE}'" in source, (
        f"{CALLEE}'s -OutputFile default is no longer "
        f"{CALLEE_DEFAULT_OUTPUT_FILE!r}; the blank-case reasoning cites it"
    )


def test_the_checker_agrees_this_workflow_is_burned_down():
    """The guard is the arbiter, so ask it rather than restating its answer."""
    findings, _, _ = guard.scan_all()
    current = guard.current_map(findings)
    assert WORKFLOW not in current, (
        f"{WORKFLOW} still interpolates a free-text input: {current.get(WORKFLOW)}"
    )
    assert WORKFLOW not in guard.KNOWN_UNGUARDED, (
        f"{WORKFLOW} is burned down but still listed in KNOWN_UNGUARDED — that is "
        f"a stale entry, which the guard itself exits 1 on"
    )


def test_no_frozen_entry_enters_a_write_environment_any_more():
    """The milestone this lane exists for, asserted against the tree.

    Not a count: the claim is that the [W] set is EMPTY. If a new write-entering
    workflow is added to the freeze later, this fails and names it — which is
    the right outcome, because the "write half is done" note in the guard and in
    the report line would then be wrong.
    """
    findings, _, _ = guard.scan_all()
    current = guard.current_map(findings)
    write = sorted(
        w
        for w in current
        if any(guard.is_write_environment(e) for e in guard.environments(_wf(w)))
    )
    assert not write, (
        f"the freeze has write-environment entries again: {write}. #1080's write "
        f"half was closed by lane 20; a new one needs burning down, and the "
        f"'write half is done' notes need re-reading"
    )


def _wf(name: str) -> dict:
    return load_workflow(name)


# --------------------------------------------------------------------------
# Behaviour — the injection
# --------------------------------------------------------------------------


def test_the_pre_fix_body_stole_the_token_and_exited_zero():
    """The positive control. Without it the fixed body proves nothing."""
    out, stolen, rc = _run(
        _pre_fix(_payload(CALLEE_DEFAULT_OUTPUT_FILE)), **{TOKEN_VAR: FAKE_TOKEN}
    )
    assert stolen is not None, (
        f"the pre-fix body did NOT execute the payload, so every assertion about "
        f"the fixed body below is vacuous. Output: {out[:600]}"
    )
    assert stolen.strip() == FAKE_TOKEN, (
        f"the payload ran but did not reach the WPMUDEV token — it wrote "
        f"{stolen!r}. The claim under test is which credential it reached."
    )
    assert _bound_output_file(out) == CALLEE_DEFAULT_OUTPUT_FILE, (
        f"the exporter should still have been handed a LEGAL value — that is what "
        f"makes the theft invisible. It was bound "
        f"{_bound_output_file(out)!r}. Output: {out[:600]}"
    )
    assert rc == 0, (
        f"the injected step should exit 0, which is what an approver watching the "
        f"run would see. rc={rc}. Output: {out[:600]}"
    )


def test_the_naive_subexpression_payload_is_inert_here():
    """Single quotes make `$( )` inert, so the payload every double-quoted lane
    used reports THIS body as harmless. Pinned so a future reader who reaches for
    it does not conclude the site was never injectable — it is the payload that
    is wrong, not the finding."""
    naive = "wpmudev_domains.csv$(Set-Content -Path " + SENTINEL + " -Value naive)"
    out, stolen, rc = _run(_pre_fix(naive), **{TOKEN_VAR: FAKE_TOKEN})
    assert stolen is None, (
        f"the naive $( ) payload executed inside single quotes, which pwsh does "
        f"not do. If this now fires, the site's quoting has changed and the "
        f"breakout reasoning above needs re-deriving. Output: {out[:600]}"
    )
    assert _bound_output_file(out) == naive, (
        f"the naive payload should arrive at the callee verbatim, as data. It was "
        f"bound {_bound_output_file(out)!r}"
    )
    assert rc == 0, f"expected exit 0, got {rc}. Output: {out[:600]}"


def test_the_shipped_body_binds_the_payload_as_data():
    """The same payload, supplied the way a dispatcher supplies it now."""
    step = _step()
    _assert_wiring(step)
    payload = _payload(CALLEE_DEFAULT_OUTPUT_FILE)
    out, stolen, rc = _run(
        _offline(step["run"]), **{TOKEN_VAR: FAKE_TOKEN, ENV_VAR: payload}
    )
    assert stolen is None, (
        f"the payload EXECUTED against the fixed body — it wrote {stolen!r}. "
        f"Output: {out[:600]}"
    )
    assert _bound_output_file(out) == payload, (
        f"the payload should reach the callee verbatim, as one argument. It was "
        f"bound {_bound_output_file(out)!r}. Output: {out[:600]}"
    )


def test_the_shipped_body_still_passes_an_ordinary_value_through():
    """The remedy must not break the workflow it protects."""
    step = _step()
    _assert_wiring(step)
    out, stolen, rc = _run(
        _offline(step["run"]),
        **{TOKEN_VAR: FAKE_TOKEN, ENV_VAR: CALLEE_DEFAULT_OUTPUT_FILE},
    )
    assert stolen is None, f"an ordinary value wrote the sentinel: {stolen!r}"
    assert _bound_output_file(out) == CALLEE_DEFAULT_OUTPUT_FILE, (
        f"expected the exporter to be bound {CALLEE_DEFAULT_OUTPUT_FILE!r}, got "
        f"{_bound_output_file(out)!r}. Output: {out[:600]}"
    )
    assert rc == 0, f"an ordinary run must exit 0, got {rc}. Output: {out[:600]}"


# --------------------------------------------------------------------------
# Behaviour — the blank, and why it is refused rather than defaulted
# --------------------------------------------------------------------------


def test_every_blank_form_is_refused_before_the_callee_runs():
    step = _step()
    _assert_wiring(step)
    body = _offline(step["run"])
    for label, overrides in (
        ("empty", {ENV_VAR: ""}),
        ("whitespace", {ENV_VAR: "   "}),
        ("unset", {}),
    ):
        out, _stolen, rc = _run(body, **{TOKEN_VAR: FAKE_TOKEN, **overrides})
        assert rc != 0, (
            f"{label}: the guarded body exited {rc} — a blank output_file must "
            f"fail closed. Output: {out[:600]}"
        )
        assert _bound_output_file(out) is None, (
            f"{label}: the exporter ran anyway, bound "
            f"{_bound_output_file(out)!r}. Output: {out[:600]}"
        )
        assert "output_file is blank" in out, (
            f"{label}: exited non-zero without the refusal message, so this "
            f"passes on any failure — including a broken harness (the rule from "
            f"the L214 note: a test asserting a non-zero exit must also assert on "
            f"the output). Output: {out[:600]}"
        )


def test_the_unguarded_empty_value_is_preserved_and_exits_zero():
    """The control, and the measurement that bounds L254's scope.

    An empty string is NOT dropped from this native invocation: the callee is
    bound an empty path, its write fails, and the step still exits 0 — the silent
    no-op export the guard exists to prevent. Sampled, because "deterministic" is
    a result rather than an assumption.

    If a later change adds the missing `$LASTEXITCODE` check after the native
    call, this test fails on the `rc == 0` assertion. That is the good kind of
    failure: update it, and note that the empty case became loud.
    """
    stripped = _strip_guard(_offline(_step()["run"]))
    outcomes = []
    for _ in range(BLANK_RUNS):
        out, _stolen, rc = _run(stripped, **{TOKEN_VAR: FAKE_TOKEN, ENV_VAR: ""})
        outcomes.append((_bound_output_file(out), rc))
    assert all(o == ("", 0) for o in outcomes), (
        f"expected every unguarded EMPTY run to bind an empty OutputFile at exit "
        f"0 — the defect the guard removes. Got {outcomes}"
    )
    assert all(o[0] != CALLEE_DEFAULT_OUTPUT_FILE for o in outcomes), (
        f"the empty value fell through to the callee's own default, so it WAS "
        f"dropped after all and the reasoning in this module is wrong: {outcomes}"
    )


def test_the_unguarded_unset_value_is_dropped_and_fails_loudly():
    """The other half of the asymmetry: `$null` IS dropped, which is L254's shape.

    Pinned alongside the empty case because the pair is the finding. A remedy
    argued from either one alone picks the wrong gate: `-ne ''` looks sufficient
    against the loud form and passes the quiet one.
    """
    stripped = _strip_guard(_offline(_step()["run"]))
    outcomes = []
    for _ in range(BLANK_RUNS):
        out, _stolen, rc = _run(stripped, **{TOKEN_VAR: FAKE_TOKEN})
        outcomes.append((_bound_output_file(out), rc))
    assert all(bound is None and rc != 0 for bound, rc in outcomes), (
        f"expected every unguarded UNSET run to fail before binding — got "
        f"{outcomes}"
    )


def test_the_unguarded_whitespace_value_reaches_the_callee_at_exit_zero():
    """Why `IsNullOrWhiteSpace` and not `-ne ''` (#1213), measured on this site."""
    stripped = _strip_guard(_offline(_step()["run"]))
    out, _stolen, rc = _run(stripped, **{TOKEN_VAR: FAKE_TOKEN, ENV_VAR: "   "})
    assert _bound_output_file(out) == "   " and rc == 0, (
        f"expected an all-spaces value to reach the callee at exit 0 — the case "
        f"`-ne ''` would pass. Got bound={_bound_output_file(out)!r} rc={rc}"
    )


# --------------------------------------------------------------------------
# The summary block reads `$out` as a LITERAL path, not as a glob
#
# Moving the value into `env:` stops it being parsed as CODE. It does not stop
# it being parsed as a PATTERN — a separate interpretation with its own opt-out,
# and the one a burn-down lane is most likely to leave behind, because the
# injection is fixed and the read still looks innocuous. Raised by Copilot on
# #1288.
# --------------------------------------------------------------------------

# A stub that writes NOTHING, i.e. an export that failed. That is the state in
# which a glob is dangerous: the file the operator named does not exist, so the
# wildcard is free to match something else.
STUB_WRITES_NOTHING = """[CmdletBinding()]
param(
    [Parameter()][string]$ApiToken,
    [Parameter()][string]$BaseUrl = 'https://wpmudev.com/api',
    [Parameter()][string]$OutputFile = 'wpmudev_domains.csv',
    [Parameter()][ValidateRange(1, 100)][int]$PerPage = 100
)
Write-Output "CALLED OutputFile=[$OutputFile] PerPage=[$PerPage]"
"""

DECOY_NAME = "prior-run-inventory.csv"
DECOY_BODY = "domain,sitesCount\na.org,3\nb.org,7\n"
SINGLE_MATCH_GLOB = "prior-*.csv"


def test_the_summary_block_reads_the_path_literally():
    """Wiring: both reads take -LiteralPath, and neither takes a bare -Path."""
    body = _step().get("run", "")
    assert "Test-Path -LiteralPath $out" in body, (
        f"the summary's existence check must use -LiteralPath: {body!r}"
    )
    assert "Import-Csv -LiteralPath $out" in body, (
        f"the summary's read must use -LiteralPath: {body!r}"
    )
    # The negative half. Without it, ADDING a -LiteralPath call elsewhere would
    # satisfy the assertions above while a wildcard-aware read survived.
    for forbidden in ("Test-Path $out", "Import-Csv -Path $out"):
        assert forbidden not in body, (
            f"{forbidden!r} is wildcard-aware and $out is dispatcher-supplied "
            f"text — use -LiteralPath. Body: {body!r}"
        )


def test_a_single_match_glob_does_not_become_the_summarys_answer():
    """Behaviour: the export wrote nothing, so the summary must say so.

    The control below shows this body reporting a DIFFERENT file's contents as
    its own result when the read is wildcard-aware, which is the whole finding.
    """
    step = _step()
    _assert_wiring(step)
    out, _stolen, rc = _run(
        _offline(step["run"]),
        stub=STUB_WRITES_NOTHING,
        extra_files={DECOY_NAME: DECOY_BODY},
        **{TOKEN_VAR: FAKE_TOKEN, ENV_VAR: SINGLE_MATCH_GLOB},
    )
    assert "output file not found" in out, (
        f"the export wrote nothing, so the summary must report the file as "
        f"missing. Output: {out[:700]}"
    )
    assert "domains=" not in out, (
        f"the summary reported a domain count for a run that exported nothing — "
        f"it has read {DECOY_NAME} through a wildcard. Output: {out[:700]}"
    )
    assert rc == 0, f"expected exit 0, got {rc}. Output: {out[:700]}"


def test_without_literalpath_the_glob_reports_an_unrelated_files_rows():
    """The control. Without it the test above passes on any body that cannot
    find the file for ANY reason — including one where the glob never matched.

    Reverting just the two parameters must make the same input report the decoy
    file's two domains as the export's result, at exit 0.
    """
    body = _offline(_step()["run"])
    for literal, wildcard in (
        ("Test-Path -LiteralPath $out", "Test-Path $out"),
        ("Import-Csv -LiteralPath $out", "Import-Csv -Path $out"),
    ):
        assert body.count(literal) == 1, (
            f"expected exactly one {literal!r} to revert, found "
            f"{body.count(literal)} — this control cannot be applied and would "
            f"otherwise measure the fixed body (ledger L47)"
        )
        body = body.replace(literal, wildcard)
    # Both CALL forms, not a bare `-LiteralPath` substring: the step's comments
    # name the parameter too, so the loose form fails on prose and reports it as
    # a surviving literal read. It did exactly that when this control was
    # written — a correct revert scored as a broken one.
    for spelling in ("Test-Path -LiteralPath $out", "Import-Csv -LiteralPath $out"):
        assert spelling not in body, (
            f"a literal read survived the revert ({spelling!r}): {body!r}"
        )

    out, _stolen, rc = _run(
        body,
        stub=STUB_WRITES_NOTHING,
        extra_files={DECOY_NAME: DECOY_BODY},
        **{TOKEN_VAR: FAKE_TOKEN, ENV_VAR: SINGLE_MATCH_GLOB},
    )
    assert "domains=2" in out, (
        f"expected the wildcard-aware body to read {DECOY_NAME} and report its 2 "
        f"domains as the export's own result — the defect -LiteralPath removes. "
        f"Output: {out[:700]}"
    )
    assert rc == 0, (
        f"the defect is silent, which is what makes it worth fixing; expected "
        f"exit 0, got {rc}. Output: {out[:700]}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

# Only the behavioural cases spawn a pwsh subprocess; the wiring, declaration,
# credential and checker-agreement cases are pure YAML and must run on a host
# with no pwsh (#1182 — a whole-module `shutil.which` gate turns "could not run"
# into "everything passed"). This set is scoped to exactly the cases that call
# `_run`.
NEEDS_PWSH = {
    "test_a_single_match_glob_does_not_become_the_summarys_answer",
    "test_every_blank_form_is_refused_before_the_callee_runs",
    "test_without_literalpath_the_glob_reports_an_unrelated_files_rows",
    "test_the_naive_subexpression_payload_is_inert_here",
    "test_the_pre_fix_body_stole_the_token_and_exited_zero",
    "test_the_shipped_body_binds_the_payload_as_data",
    "test_the_shipped_body_still_passes_an_ordinary_value_through",
    "test_the_unguarded_empty_value_is_preserved_and_exits_zero",
    "test_the_unguarded_unset_value_is_dropped_and_fails_loudly",
    "test_the_unguarded_whitespace_value_reaches_the_callee_at_exit_zero",
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
