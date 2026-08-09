"""Unit tests for 704's input-validation step (#1080 burn-down).

704 runs on `github-prod` and holds `wr-all-cbm-github-pat`, the credential that
opens pull requests across every `FFC-EX-*` site repo. Its two free-text
dispatch inputs used to be interpolated straight into the bash body of the step
named **`Validate inputs`**:

    if [[ ! "${{ inputs.gtm_id }}" =~ ^GTM-[A-Z0-9]{5,9}$ ]]; then

which is not a value being tested — it is the dispatcher's text pasted into the
program doing the testing, run after the approver spent the write credential.
Both inputs now arrive through step-level `env:`.

WHY THIS CALL SITE WAS WORTH TAKING BEFORE THE OTHER SEVENTEEN
    The exploited run is SILENT. Measured against the shipped body,
    `gtm_id = "GTM-ABCDE$(printf ran > f)"` executed the substitution and left
    `GTM-ABCDE` behind as the residue, so the validator it was smuggled through
    passed and the step exited **0** — no annotation, no failed step, nothing an
    approver reviewing the run afterwards would see. Contrast #1137's 112, where
    the payload executed and the callee's `[ValidatePattern]` then rejected the
    residue, at least leaving a red step in the log. Here the only trace is the
    side effect itself. `test_interpolated_body_is_the_positive_control` pins
    both halves — that it ran, and that the step reported success — so the
    inertness test below cannot pass vacuously.

    It also runs BEFORE the Key Vault step that loads the PAT, in a job that has
    already checked the repository out. Injected code does not need the
    credential in hand: it can rewrite `scripts/analytics-wire.ps1`, which the
    next step invokes once `GH_TOKEN` is in the job environment.

WHY THIS MODULE EXISTS AT ALL, GIVEN THE CHECKER
    `scripts/check-workflow-input-interpolation.py` proves the fix landed ONCE.
    It is a detector defined over the defect's spelling, so it goes quiet the
    moment the spelling is gone — including when the REMEDY is gone too (ledger
    L202). Delete the `env:` block and nothing is interpolated, so the checker
    stays honestly green. Only a per-workflow assertion on the wiring can see
    that, which is what `_assert_wiring` is for.

NOT COVERED HERE, DELIBERATELY
    The neighbouring `Wire analytics + open PR` step already passed all four of
    its values through `env:` — it is why the file read as if the pattern were
    followed. Its own mapping is not asserted here because a deletion there is
    already loud: every consumer is read under `set -u` in a position with no
    default, so an unset `DOMAIN`/`GTM_ID` aborts the step by name. The
    validation step had no such property, which is why the fail-fast checks
    below are part of the fix rather than decoration.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow

WORKFLOW = "704-website-analytics-wire.yml"
JOB = "wire"
STEP = "Validate inputs"

# The env var each input must travel in, and the expression it must be mapped from.
WIRING = {
    "GTM_ID": "${{ inputs.gtm_id }}",
    "MEASUREMENT_ID": "${{ inputs.measurement_id }}",
}

# A payload valid in the position it lands in, and — the point of this one — one
# whose RESIDUE still satisfies the check it is smuggled through. `$( )` inside a
# double-quoted `[[ ]]` operand is expanded before the match, and contributes
# nothing to the string, so what the validator ends up testing is `GTM-ABCDE`.
SENTINEL = "INJECTED-704.txt"
PAYLOAD = "GTM-ABCDE$(printf ran > " + SENTINEL + ")"

# The body as it shipped BEFORE the burn-down, kept verbatim as the positive
# control for the inertness test below.
PRE_FIX_BODY = """set -euo pipefail
if [[ ! "GTM_ID_HERE" =~ ^GTM-[A-Z0-9]{5,9}$ ]]; then
  echo "::error::gtm_id 'GTM_ID_HERE' is not a valid GTM-XXXXXXX id."
  exit 1
fi
if [ -n "MEASUREMENT_ID_HERE" ] && [[ ! "MEASUREMENT_ID_HERE" =~ ^G-[A-Z0-9]{6,12}$ ]]; then
  echo "::error::measurement_id 'MEASUREMENT_ID_HERE' is not a valid G-XXXXXXXXXX id."
  exit 1
fi
"""


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), JOB, STEP)


def _assert_wiring(step: dict) -> None:
    """Both inputs travel in env, the body reads them, and neither is interpolated.

    Asserted separately from behaviour and re-asserted before every behavioural
    run, because the fixture SUPPLIES these variables (ledger L199): delete the
    workflow's `env:` block and the step still sees them from the harness, so
    every behavioural test below keeps passing over plumbing that no longer
    exists — while a real dispatch would validate an empty string.
    """
    env = step.get("env") or {}
    body = step.get("run", "")
    for var, expression in WIRING.items():
        assert env.get(var) == expression, (
            f"step {step.get('name')!r} in job {JOB!r} must map {var} to "
            f"{expression} — its env: mapping is {env!r}"
        )
        assert f"${var}" in body or f"${{{var}" in body, (
            f"step {step.get('name')!r} maps {var} but never reads it — the "
            f"env: block is decoration and the value reaches no check"
        )
    for spelling in ("inputs.gtm_id", "inputs.measurement_id"):
        assert spelling not in body, (
            f"step {step.get('name')!r} interpolates {spelling} into its script "
            f"body again (#1080): on github-prod that is dispatcher text executed "
            f"after the approval, one step before the PAT is loaded"
        )


def _run(body: str, **env_overrides: str) -> tuple[str, bool, int]:
    """Run a bash body in a temp cwd. Returns (output, sentinel_written, rc).

    The sentinel is the only discriminator used for execution. A marker
    substring over stdout is not one: every rejection path here echoes the
    offending value back in its own `::error::` text, so a substring predicate
    reports the payload as executed on a run that executed nothing.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = pathlib.Path(td)
        script = tmp / "step.sh"
        script.write_text(body, encoding="utf-8")
        env = child_env(**env_overrides)
        # Only what the test sets may be visible: an inherited GTM_ID would make
        # the missing-mapping tests pass for the wrong reason.
        for var in WIRING:
            if var not in env_overrides:
                env.pop(var, None)
        proc = subprocess.run(
            ["bash", str(script)],
            cwd=tmp, env=env, capture_output=True,
            text=True, encoding="utf-8", timeout=60,
        )
        return proc.stdout + proc.stderr, (tmp / SENTINEL).exists(), proc.returncode


def test_both_inputs_are_wired_through_env():
    _assert_wiring(_step())


def test_valid_ids_pass():
    step = _step()
    _assert_wiring(step)
    out, _, rc = _run(step["run"], GTM_ID="GTM-ABC1234", MEASUREMENT_ID="G-AB12CD34EF")
    assert rc == 0, f"a valid pair was rejected (rc={rc}): {out}"


def test_omitted_measurement_id_is_still_optional():
    """An empty MEASUREMENT_ID must skip its format check, not fail the step.

    GitHub maps an omitted optional input to an empty string, so this is the
    ordinary dispatch shape for a site with GTM but no separate GA4 id. It is
    also what makes the unset/empty distinction below load-bearing.
    """
    step = _step()
    _assert_wiring(step)
    out, _, rc = _run(step["run"], GTM_ID="GTM-ABC1234", MEASUREMENT_ID="")
    assert rc == 0, f"an omitted measurement_id failed the step (rc={rc}): {out}"


def test_malformed_ids_are_rejected():
    step = _step()
    _assert_wiring(step)
    out, _, rc = _run(step["run"], GTM_ID="nope", MEASUREMENT_ID="")
    assert rc != 0 and "is not a valid GTM-XXXXXXX id" in out, out
    out, _, rc = _run(step["run"], GTM_ID="GTM-ABC1234", MEASUREMENT_ID="nope")
    assert rc != 0 and "is not a valid G-XXXXXXXXXX id" in out, out


def test_injected_payload_arrives_as_data():
    """The whole point: a payload in `gtm_id` is compared as text and runs nothing."""
    step = _step()
    _assert_wiring(step)
    out, injected, rc = _run(step["run"], GTM_ID=PAYLOAD, MEASUREMENT_ID="")
    assert not injected, (
        f"the payload EXECUTED — {SENTINEL} was written from a value that "
        f"reached the body through env:. Output: {out}"
    )
    assert rc != 0, (
        f"the payload was accepted as a valid GTM id (rc=0): with the value "
        f"compared as text its `$( )` is literal, so it cannot match. Output: {out}"
    )
    assert PAYLOAD in out, (
        f"the rejection did not name the value it rejected, so something "
        f"re-parsed it on the way through. Output: {out}"
    )


def test_interpolated_body_is_the_positive_control():
    """The same payload against the PRE-FIX body executes — and the step exits 0.

    Without this, `test_injected_payload_arrives_as_data` proves nothing: a
    payload that never worked also writes no sentinel. The `rc == 0` half is the
    part worth keeping — this call site's exploited run left no failed step and
    no annotation, so nothing in the run log distinguished it from a normal one.
    """
    body = PRE_FIX_BODY.replace("GTM_ID_HERE", PAYLOAD).replace("MEASUREMENT_ID_HERE", "")
    out, injected, rc = _run(body)
    assert injected, (
        f"the pre-fix body did NOT execute the payload, so this control cannot "
        f"support the inertness test above. Output: {out}"
    )
    assert rc == 0, (
        f"expected the exploited pre-fix run to report SUCCESS (the residue is a "
        f"legal GTM id); it exited {rc}, so the payload no longer models the "
        f"silent case this module is about. Output: {out}"
    )


def test_missing_gtm_mapping_aborts_before_validating_an_empty_string():
    """A deleted or misspelled `env:` mapping must be loud, not silently empty.

    The checker cannot see this (nothing is interpolated either way, ledger
    L202). Matched against the `::error::` ANNOTATION rather than any shell
    diagnostic: an assertion on a shell's own unbound-variable wording is an
    assertion about the host that produced it (ledger L207).
    """
    step = _step()
    out, _, rc = _run(step["run"], MEASUREMENT_ID="")
    assert rc != 0, f"a missing GTM_ID mapping did not fail the step. Output: {out}"
    assert "::error::GTM_ID is empty" in out, (
        f"step failed but did not emit the annotation naming the cause: {out}"
    )
    assert "the step-level env: mapping is missing or misnamed" in out, (
        f"the annotation was emitted but not with the full diagnosis: {out}"
    )


def test_unset_measurement_mapping_aborts_rather_than_skipping_its_check():
    """The asymmetric half: an unset MEASUREMENT_ID must not read as "omitted".

    An omitted optional input and a deleted mapping both arrive at `[ -n ... ]`
    as an empty string, and the difference matters: the first legitimately skips
    the format check, the second skips it while `Wire analytics + open PR` keeps
    its own mapping and hands the unvalidated id to the script. Only the
    unset/set-but-empty distinction separates them.
    """
    step = _step()
    out, _, rc = _run(step["run"], GTM_ID="GTM-ABC1234")
    assert rc != 0, f"an unset MEASUREMENT_ID mapping did not fail the step: {out}"
    assert "::error::MEASUREMENT_ID is unset" in out, (
        f"step failed but did not emit the annotation naming the cause: {out}"
    )
    assert "is not a valid G-XXXXXXXXXX id" not in out, (
        f"the step reached the format check with no mapping at all: {out}"
    )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    if shutil.which("bash") is None:
        print("  SKIP all (bash not installed in this environment; runs in CI)")
        sys.exit(0)
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:600]}")
    sys.exit(1 if failures else 0)
