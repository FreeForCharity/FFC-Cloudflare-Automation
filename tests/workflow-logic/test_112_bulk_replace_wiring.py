"""Unit tests for 112's bulk A-record replacement step (#1080 burn-down).

112 rewrites A records across **every zone in both Cloudflare accounts** on the
`cloudflare-prod-write` lane. Its two free-text dispatch inputs used to be
interpolated straight into the pwsh body:

    OldIp = '${{ inputs.old_ip }}'

which is not an argument — it is the dispatcher's text pasted into the program,
run after the approver spent the write token. Both now arrive through
step-level `env:`.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete the `env:` block and nothing is interpolated, so the checker
    is honestly green while the step runs with empty parameters. Only a
    per-workflow assertion on the wiring can see that, which is what
    `_assert_wiring` is for.

WHY THE CALLEE'S OWN VALIDATION IS NOT THE ANSWER
    `scripts/bulk-replace-a-record-ip.ps1` declares both parameters
    `[Parameter(Mandatory)]` + `[ValidatePattern]` for a dotted-quad. Measured
    against the pre-fix body, a payload in `old_ip` executed and THEN the
    validator rejected the resulting value — so the run failed with a message
    describing a malformed IP address on a run where arbitrary code had already
    executed under the write credential. A validator downstream of the
    injection point cannot prevent the injection; it can only make the log
    look like a control worked. `test_interpolated_body_is_the_positive_control`
    pins that fact so the inertness assertion below cannot pass vacuously.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow

WORKFLOW = "112-dns-bulk-replace-a-ip.yml"
JOB = "bulk-replace"
STEP = "Run bulk A-record replacement"

# The env var each input must travel in, and the expression it must be mapped from.
WIRING = {
    "IN_OLD_IP": "${{ inputs.old_ip }}",
    "IN_NEW_IP": "${{ inputs.new_ip }}",
}

# A payload valid in the position it lands in. The injection point sits inside a
# `@{ }` hash literal, where a bare `;` is a PARSE error — so a naive
# `x'; Write-Host 'pwned'; $z = 'y` aborts the script instead of running, and
# reads as "not exploitable". `$( )` in an expression position is valid there and
# its last statement supplies the value.
SENTINEL = "INJECTED-112.txt"
PAYLOAD = "9.9.9.9' + $(Set-Content -Path '" + SENTINEL + "' -Value 'ran'; '') + '"

# The body as it shipped BEFORE the burn-down, kept verbatim as the positive
# control for the inertness test below.
PRE_FIX_BODY = """$params = @{
  OldIp = 'OLD_IP_HERE'
  NewIp = 'NEW_IP_HERE'
}
if ('true' -eq 'true') { $params.DryRun = $true }
./scripts/bulk-replace-a-record-ip.ps1 @params
"""

# Permissive stand-in for the real script: it records what it was BOUND, which is
# the only discriminator that works here. A marker-string search over stdout is
# not one — pwsh echoes the offending source line back in a ParserError, so any
# substring predicate matches the payload text on a run that executed nothing.
STUB = """[CmdletBinding()]
param(
    [string]$OldIp,
    [string]$NewIp,
    [switch]$DryRun
)
Write-Output "CALLED OldIp=[$OldIp] NewIp=[$NewIp] DryRun=[$DryRun]"
"""


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


def _assert_wiring(step: dict) -> None:
    """Both inputs travel in env, the body reads them, and neither is interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES these variables (ledger L199): delete the
    workflow's `env:` block and the step still sees them from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists — while a real dispatch would abort with empty parameters.
    """
    env = step.get("env") or {}
    body = step.get("run", "")
    for var, expression in WIRING.items():
        assert env.get(var) == expression, (
            f"step {step.get('name')!r} in job {JOB!r} must map {var} to "
            f"{expression} — its env: block is {env!r}"
        )
        assert f"$env:{var}" in body, (
            f"step {step.get('name')!r} maps {var} but never reads $env:{var} — "
            f"the env: block is decoration and the value reaches nothing"
        )
    for spelling in ("inputs.old_ip", "inputs.new_ip"):
        assert spelling not in body, (
            f"step {step.get('name')!r} interpolates {spelling} into its script "
            f"body again (#1080): on cloudflare-prod-write that is dispatcher "
            f"text executed after the approval"
        )


def _render_dry_run(body: str, value: str) -> str:
    """Substitute only the `dry_run` expression, the way GitHub would.

    `dry_run` is a `boolean`, which GitHub generates from the declared options,
    so it stays interpolated on purpose and the guard does not count it. The
    anchor is asserted before substituting (ledger L47) so that a body which
    stopped containing it fails loudly instead of silently testing nothing.
    """
    anchor = "'${{ inputs.dry_run }}'"
    assert body.count(anchor) == 1, (
        f"expected exactly one {anchor} in the step body, found "
        f"{body.count(anchor)} — the dry_run branch moved"
    )
    return body.replace(anchor, f"'{value}'")


def _run(body: str, **env_overrides: str) -> tuple[str, bool, int]:
    """Run a pwsh body in a temp cwd holding the stub. Returns (output, sentinel_written, rc)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "bulk-replace-a-record-ip.ps1").write_text(STUB, encoding="utf-8")
        script = tmp / "step.ps1"
        script.write_text(body, encoding="utf-8")
        env = child_env(**env_overrides)
        # Only what the test sets may be visible: an inherited IN_OLD_IP would
        # make the missing-mapping test pass for the wrong reason.
        for var in WIRING:
            if var not in env_overrides:
                env.pop(var, None)
        proc = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script)],
            cwd=tmp, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=120,
        )
        return proc.stdout + proc.stderr, (tmp / SENTINEL).exists(), proc.returncode


def test_both_inputs_are_wired_through_env():
    _assert_wiring(_step())


def test_values_reach_the_script():
    step = _step()
    _assert_wiring(step)
    out, _, rc = _run(
        _render_dry_run(step["run"], "true"),
        IN_OLD_IP="204.44.192.77", IN_NEW_IP="216.222.200.253",
    )
    assert rc == 0, f"step exited {rc}: {out}"
    assert "CALLED OldIp=[204.44.192.77] NewIp=[216.222.200.253]" in out, out


def test_injected_payload_arrives_as_data():
    """The whole point: a payload in `old_ip` binds verbatim and runs nothing."""
    step = _step()
    _assert_wiring(step)
    out, injected, rc = _run(
        _render_dry_run(step["run"], "true"),
        IN_OLD_IP=PAYLOAD, IN_NEW_IP="216.222.200.253",
    )
    assert not injected, (
        f"the payload EXECUTED — {SENTINEL} was written from a value that "
        f"reached the body through env:. Output: {out}"
    )
    assert f"CALLED OldIp=[{PAYLOAD}]" in out, (
        f"payload did not arrive verbatim, so something re-parsed it "
        f"(rc={rc}). Output: {out}"
    )


def test_interpolated_body_is_the_positive_control():
    """The same payload against the PRE-FIX body executes — and is then 'rejected'.

    Without this, `test_injected_payload_arrives_as_data` proves nothing: a
    payload that never worked in the first place also writes no sentinel. It
    also pins the reason the callee's own ValidatePattern is not a defence —
    the side effect lands before the script is ever invoked.
    """
    body = PRE_FIX_BODY.replace("OLD_IP_HERE", PAYLOAD).replace("NEW_IP_HERE", "216.222.200.253")
    out, injected, _ = _run(body)
    assert injected, (
        f"the pre-fix body did NOT execute the payload, so this control cannot "
        f"support the inertness test above. Output: {out}"
    )


def test_missing_env_mapping_aborts_before_the_script_runs():
    """A deleted or misspelled `env:` mapping must be loud, not silently empty.

    The checker cannot see this (nothing is interpolated either way), and the
    callee would report it as a malformed IP address — sending the next reader
    to the dispatch form rather than to the workflow.
    """
    step = _step()
    out, _, rc = _run(_render_dry_run(step["run"], "true"))
    assert rc != 0, f"empty env: mapping did not fail the step. Output: {out}"
    # Matched against the `::error::` ANNOTATION, not against the throw's text.
    # A thrown message reaches stdout only through pwsh's error renderer, and an
    # assertion on a render is an assertion about the host that produced it: the
    # previous version of this test asserted the throw's wording, passed here,
    # and failed on the CI runner, whose output carried the source line cut at
    # `… the step-level env: mapping  …` and not the phrase being matched.
    # `Write-Output` text is passed through unmodified — measured on strings well
    # past any plausible width — so the annotation is host-independent.
    assert "::error::IN_OLD_IP / IN_NEW_IP is empty" in out, (
        f"step failed but did not emit the annotation naming the cause: {out}"
    )
    assert "the step-level env: mapping is missing or misnamed" in out, (
        f"the annotation was emitted but not with the full diagnosis: {out}"
    )
    assert "CALLED" not in out, f"the script ran with empty parameters: {out}"


def test_dry_run_true_sets_the_switch():
    step = _step()
    out, _, rc = _run(
        _render_dry_run(step["run"], "true"),
        IN_OLD_IP="204.44.192.77", IN_NEW_IP="216.222.200.253",
    )
    assert rc == 0, f"step exited {rc}: {out}"
    assert "DryRun=[True]" in out, out


def test_dry_run_false_omits_the_switch():
    step = _step()
    out, _, rc = _run(
        _render_dry_run(step["run"], "false"),
        IN_OLD_IP="204.44.192.77", IN_NEW_IP="216.222.200.253",
    )
    assert rc == 0, f"step exited {rc}: {out}"
    assert "DryRun=[False]" in out, out


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
