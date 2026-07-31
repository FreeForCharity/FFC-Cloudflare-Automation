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
from wf_extract import child_env, step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = "120-bulk-cutover-to-github-pages.yml"
JOB = "post-cutover-smoke"
DOMAIN = "example.org"
SMOKE_TSV = pathlib.Path("/tmp/smoke-dispatched.tsv")


def _script(step_name: str) -> str:
    # The steps interpolate the dispatch input; bash cannot parse `${{ … }}`.
    return step_run(WORKFLOW, JOB, step_name).replace("${{ inputs.domains }}", DOMAIN)


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
        )
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            cwd=str(REPO_ROOT),  # the steps read config/ffc-ex-cutover-domains.json
            capture_output=True,
            text=True,
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
