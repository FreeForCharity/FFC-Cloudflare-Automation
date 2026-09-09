"""Unit tests for 120's three post-cutover bash steps (run with a fake gh).

Regression anchor: ledger L02 / #854 / #889 — `gh` writes its error body to
STDOUT, so `gh … 2>/dev/null || echo <default>` captures the error text *with*
the default appended. The variable then equals neither the data nor the
default, and every downstream comparison takes the wrong branch.

In this workflow that shape had four sites and two distinct consequences:

* the cert-state reads reported an API error as if it were a measured state;
* the smoke `run list` read carried an error body forward **as a run id**, so
  the poll step then failed on every request and reported a smoke FAILURE for a
  run that was very likely fine.

Every scenario here is run against the *shipped* step text (extracted from the
YAML), with `sleep` shimmed out so the real 10s settles don't slow the suite.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, find_step, load_workflow, runner_temp, step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = "120-bulk-cutover-to-github-pages.yml"
JOB = "post-cutover-smoke"
DOMAIN = "example.org"
# The dispatch step writes this and the poll step reads it, so it has to
# outlive a single `run_step` call — hence the per-PROCESS RUNNER_TEMP the
# harness hands the child, not the per-call TemporaryDirectory below.
# Hardcoding `/tmp/smoke-dispatched.tsv` here is what kept the module
# uncontainable even once the workflow was fixed: two concurrent copies
# read and truncated one file, and the resulting failure lands on
# whichever side of an L191 treatment/control comparison loses the race
# (#1247).
SMOKE_TSV = pathlib.Path(runner_temp()) / "smoke-dispatched.tsv"


# Every step that consumes `domains` and the env var it must read it through.
# `domains` is free text, so an interpolated `${{ }}` would be the dispatcher's
# code pasted into the body and run after the approval (#1080). All four sites
# moved to step-level `env:`; the two pwsh ones have no behavioural module, so
# this list is the only thing standing between them and a silent regression.
DOMAINS_ENV = "IN_DOMAINS"
DOMAIN_CONSUMERS = (
    ("cname-flip", "Flip public/CNAME"),
    ("dns-flip", "Flip Cloudflare apex DNS"),
    (JOB, "Bind Pages custom domain"),
    (JOB, "Dispatch Post-Deploy Smoke Test"),
)


def _assert_domains_wiring(job_id: str, step_name: str) -> None:
    """The step reads `domains` from env and does not interpolate it.

    Asserted separately from behaviour, and asserted before every behavioural
    run below, because the fixture SUPPLIES this value (ledger L199): delete the
    workflow's `env:` block and the step still sees `IN_DOMAINS` from the test
    harness, so all six behavioural tests keep passing over plumbing that no
    longer exists. A real dispatch would run with an empty domain list.
    """
    step = find_step(load_workflow(WORKFLOW), job_id, step_name)
    env = step.get("env") or {}
    assert env.get(DOMAINS_ENV) == "${{ inputs.domains }}", (
        f"step {step.get('name')!r} in job {job_id!r} must map "
        f"{DOMAINS_ENV} to the domains input; its env: block is {env!r}"
    )
    body = step.get("run", "")
    assert DOMAINS_ENV in body, (
        f"step {step.get('name')!r} in job {job_id!r} maps {DOMAINS_ENV} but "
        f"never reads it — the env: block is decoration"
    )
    assert "inputs.domains" not in body, (
        f"step {step.get('name')!r} in job {job_id!r} still interpolates the "
        f"domains input into its script body (#1080)"
    )


def _script(step_name: str) -> str:
    # Re-assert the wiring on every behavioural run of a step that consumes
    # `domains` — the poll step reads the TSV the dispatch step wrote and takes
    # no input, so it is deliberately not in DOMAIN_CONSUMERS.
    if (JOB, step_name) in DOMAIN_CONSUMERS:
        _assert_domains_wiring(JOB, step_name)
    return step_run(WORKFLOW, JOB, step_name)


def run_step(step_name: str, env_overrides: dict) -> tuple[str, str, str, int]:
    """Run a step with the fake gh. Returns (stdout, stderr, gh_log, rc)."""
    script = _script(step_name)
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        # A local `sleep` shim, not one in harness/: the real steps settle for
        # 10s per domain, and every other module shares that harness directory.
        shim = td / "sleep"
        shim.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        shim.chmod(0o755)
        gh_log = td / "gh.log"
        summary = td / "summary.md"
        summary.touch()
        env = child_env(
            td,
            HARNESS_DIR,
            GITHUB_STEP_SUMMARY=str(summary),
            TEST_GH_LOG=str(gh_log),
            HOME=str(td),
            # Supplied the way the workflow supplies it — through env, not by
            # substituting text into the body. See _assert_domains_wiring.
            **{DOMAINS_ENV: DOMAIN},
        )
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            cwd=str(REPO_ROOT),  # the steps read config/ffc-ex-cutover-domains.json
            capture_output=True,
            text=True, encoding="utf-8",
            timeout=180,
        )
        return proc.stdout, proc.stderr, gh_log.read_text(encoding="utf-8"), proc.returncode


# ---------------------------------------------------------------------------
# Bind + cert kick — two of the four sites
# ---------------------------------------------------------------------------


def test_unreadable_cert_state_reports_unknown_and_never_a_403_body():
    out, err, _log, _rc = run_step(
        "Bind Pages custom domain", {"TEST_PAGES_GET_FAIL": "1"}
    )
    assert "cert state after bind: unknown" in out, out
    # The old form produced `{"message":"…403…"}unknown`; the state line must
    # carry the word and nothing else.
    assert "403" not in out, out
    assert "Could not read the Pages cert state" in out, out
    # The error is not discarded — it goes to stderr, where it cannot be
    # mistaken for data by a downstream comparison.
    assert "403" in err, err


def test_unreadable_cert_state_does_not_trigger_a_rebind():
    """An unreadable read must not be treated as evidence of a stalled cert.

    The re-bind is a real mutation (cname -> null -> cname) that restarts
    Let's Encrypt issuance; performing it on a guess would disrupt a
    perfectly healthy certificate.
    """
    _out, _err, log, _rc = run_step(
        "Bind Pages custom domain", {"TEST_PAGES_GET_FAIL": "1"}
    )
    puts = [line for line in log.splitlines() if "-X PUT" in line]
    assert len(puts) == 1, f"expected only the initial bind PUT, got:\n{log}"
    assert "cname:null" not in log and '"cname":null' not in log, log


def test_a_genuinely_stalled_cert_still_rebinds():
    """The counterpart: 'none' is a measured state and must keep re-binding."""
    out, _err, log, _rc = run_step(
        "Bind Pages custom domain", {"TEST_PAGES_CERT_STATE": "none"}
    )
    assert "cert stalled (none)" in out, out
    puts = [line for line in log.splitlines() if "-X PUT" in line]
    assert len(puts) == 3, f"expected bind + null + re-bind, got:\n{log}"


def test_a_healthy_cert_is_left_alone():
    out, _err, log, _rc = run_step(
        "Bind Pages custom domain", {"TEST_PAGES_CERT_STATE": "approved"}
    )
    assert "cert state after bind: approved" in out, out
    assert "cert stalled" not in out, out
    puts = [line for line in log.splitlines() if "-X PUT" in line]
    assert len(puts) == 1, log


# ---------------------------------------------------------------------------
# Dispatch — the site whose error body became a run id
# ---------------------------------------------------------------------------


def test_unreadable_run_list_records_no_run_id_rather_than_an_error_body():
    out, err, _log, _rc = run_step(
        "Dispatch Post-Deploy Smoke Test", {"TEST_RUN_LIST_FAIL": "1"}
    )
    rows = SMOKE_TSV.read_text(encoding="utf-8").splitlines()
    assert rows == [f"{DOMAIN}\tNO_RUN_ID"], rows
    assert "Could not locate dispatched smoke run" in out, out
    assert "401" in err, err


def test_a_located_run_id_is_recorded():
    _out, _err, _log, _rc = run_step(
        "Dispatch Post-Deploy Smoke Test", {"TEST_RUN_ID": "4242"}
    )
    rows = SMOKE_TSV.read_text(encoding="utf-8").splitlines()
    assert rows == [f"{DOMAIN}\t4242"], rows


def test_an_empty_run_list_is_still_no_run_id():
    """`gh run list` succeeding with no rows is a different fact from failing,
    and both have to reach the same NO_RUN_ID branch — a run that was never
    located cannot be polled either way."""
    out, _err, _log, _rc = run_step("Dispatch Post-Deploy Smoke Test", {"TEST_RUN_ID": ""})
    rows = SMOKE_TSV.read_text(encoding="utf-8").splitlines()
    assert rows == [f"{DOMAIN}\tNO_RUN_ID"], rows
    assert "Could not locate dispatched smoke run" in out, out


# ---------------------------------------------------------------------------
# Poll — the fourth site
# ---------------------------------------------------------------------------


def _seed_dispatched(run_id: str = "4242") -> None:
    SMOKE_TSV.write_text(f"{DOMAIN}\t{run_id}\n", encoding="utf-8")


def test_unreadable_run_view_reports_error_null_not_a_404_body():
    _seed_dispatched()
    out, err, _log, rc = run_step("Poll each smoke", {"TEST_RUN_VIEW_FAIL": "1"})
    assert "final state: error:null" in out, out
    assert "404" not in out, out
    assert "404" in err, err
    assert rc != 0, "an unreadable smoke must not report success"


def test_a_successful_smoke_passes():
    _seed_dispatched()
    out, _err, _log, rc = run_step(
        "Poll each smoke", {"TEST_RUN_STATUS": "completed:success"}
    )
    assert "smoke passed" in out, out
    assert rc == 0, out


def test_a_failed_smoke_fails_the_step():
    _seed_dispatched()
    out, _err, _log, rc = run_step(
        "Poll each smoke", {"TEST_RUN_STATUS": "completed:failure"}
    )
    assert "final state: completed:failure" in out, out
    assert rc != 0, out


# ---------------------------------------------------------------------------
# The shape itself
# ---------------------------------------------------------------------------


def test_every_step_reading_domains_takes_it_from_env():
    """Covers the two pwsh jobs as well, which have no behavioural module here.

    cname-flip holds github-prod and dns-flip holds cloudflare-prod-write, so
    the same input was code in two different credential contexts.
    """
    for job_id, step_name in DOMAIN_CONSUMERS:
        _assert_domains_wiring(job_id, step_name)


def test_the_domains_input_is_not_interpolated_anywhere_in_120():
    """Whole-file, so a NEW step consuming `domains` is caught as well.

    The per-step check above can only see the steps this module already knows
    about; this one has no list to fall out of date. The only legal spelling of
    `inputs.domains` in this file is the env: mapping itself.
    """
    text = (REPO_ROOT / ".github" / "workflows" / WORKFLOW).read_text(encoding="utf-8")
    legal = DOMAINS_ENV + ": ${{ inputs.domains }}"
    sites = [
        (n, line.strip())
        for n, line in enumerate(text.splitlines(), 1)
        if "inputs.domains" in line and not line.lstrip().startswith("#")
    ]
    illegal = [f"{n}: {body}" for n, body in sites if body != legal]
    assert not illegal, (
        f"`inputs.domains` may only appear as `{legal}`; found {illegal}"
    )
    assert len(sites) == len(DOMAIN_CONSUMERS), (
        f"expected one env: mapping per consuming step "
        f"({len(DOMAIN_CONSUMERS)}), found {len(sites)}"
    )


def test_the_bash_reads_are_bare_so_a_deleted_env_block_aborts():
    """`"$IN_DOMAINS"`, never `"${IN_DOMAINS:-}"` — the remedy has a direction.

    Both bash consumers run under `set -u`, and both fall back to the FLEET-WIDE
    default list when the value is empty. So a `:-` default would turn a deleted
    or renamed `env:` mapping into "cut over all 55 sites" instead of an abort:
    the safe-looking spelling picks the loudest possible wrong behaviour, and it
    is the spelling a reviewer adds to silence a `set -u` worry. Bare is correct
    here for the same reason the fixture assertions above exist — the failure has
    to be visible.
    """
    for job_id, step_name in DOMAIN_CONSUMERS:
        step = find_step(load_workflow(WORKFLOW), job_id, step_name)
        if step.get("shell") != "bash":
            continue
        body = step["run"]
        assert "set -u" in body, (
            f"step {step.get('name')!r} must keep `set -u` — it is what makes a "
            f"missing {DOMAINS_ENV} abort instead of defaulting to the fleet list"
        )
        for bad in (f"${{{DOMAINS_ENV}:-", f"${{{DOMAINS_ENV}:="):
            assert bad not in body, (
                f"step {step.get('name')!r} reads {DOMAINS_ENV} with a shell "
                f"default ({bad}…); a deleted env: block would then silently "
                f"select the fleet-wide default domain list"
            )


def test_no_step_in_120_swallows_a_gh_error_again():
    """Local to this workflow, so a reintroduction fails here as well as in the
    ledger's fleet-wide scan — the module that owns these steps should be the
    one that reports it."""
    text = (REPO_ROOT / ".github" / "workflows" / WORKFLOW).read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in text.splitlines()
        if not line.strip().startswith("#")
        and "2>/dev/null" in line
        and ("|| echo" in line or "|| true" in line)
    ]
    assert not offenders, offenders


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    if SMOKE_TSV.exists():
        os.unlink(SMOKE_TSV)
    sys.exit(1 if failures else 0)
