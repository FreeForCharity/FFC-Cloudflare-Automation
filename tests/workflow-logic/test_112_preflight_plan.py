"""Unit tests for 112's ungated preflight plan (#1509).

HOW 112 DIFFERS FROM 119, AND WHY IT STILL NEEDED THIS
    112 already surfaces both IPs in `run-name` (#1107), so at first reading
    its gate looks adequately disclosed. It is not, and the reason is worth
    stating: the IPs are not the blast radius. `scripts/bulk-replace-a-record
    -ip.ps1` takes NO zone or domain argument -- it lists every zone each
    account token can see, across BOTH Cloudflare accounts, and rewrites every
    A record whose content matches. So the affected set is "whatever currently
    holds that address", which no input names and the run name cannot show.

    The script does build exactly the table an approver wants -- `Account |
    Zone | Name | Proxied | TTL`, every match -- and prints it inside the
    GATED job, i.e. only after the approval it should have informed. That is
    the gap: the information exists and arrives one human decision too late.

WHAT THESE TESTS PIN
    The step body is run and the summary it writes is read, as in
    `test_702_preflight.py`. The load-bearing assertions are that the plan
    states the whole-estate scope in words (an approver who reads "A records
    1.2.3.4 -> 5.6.7.8" and nothing else has no idea how many zones that is),
    and that a refused preflight prints no plan -- a plan on a refused run
    reads as "checks passed, safe to approve".
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT, child_env, load_workflow, step_run

WORKFLOW = "112-dns-bulk-replace-a-ip.yml"
PREFLIGHT_JOB = "preflight"
GATED_JOB = "bulk-replace"
CALLEE = REPO_ROOT / "scripts" / "bulk-replace-a-record-ip.ps1"

PASSING = {"IN_OLD_IP": "204.44.192.77", "IN_NEW_IP": "216.222.200.253", "IN_DRY_RUN": "true"}


def run_preflight(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str]:
    """Run the preflight step. Returns (proc, summary)."""
    script = step_run(WORKFLOW, PREFLIGHT_JOB, "approval plan")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        summary.touch()
        env = child_env(GITHUB_STEP_SUMMARY=str(summary), HOME=str(tdp), **PASSING)
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        return proc, summary.read_text(encoding="utf-8")


# --- structure --------------------------------------------------------------


def test_preflight_job_is_ungated_and_uncredentialed():
    job = load_workflow(WORKFLOW)["jobs"][PREFLIGHT_JOB]
    assert "environment" not in job, job
    body = step_run(WORKFLOW, PREFLIGHT_JOB, "approval plan")
    assert "cloudflare-tokens-from-kv" not in body, body
    assert "secrets." not in body, body


def test_gated_job_needs_the_preflight():
    job = load_workflow(WORKFLOW)["jobs"][GATED_JOB]
    needs = job.get("needs")
    needs = [needs] if isinstance(needs, str) else (needs or [])
    assert PREFLIGHT_JOB in needs, needs
    assert job.get("environment") == "cloudflare-prod-write", job.get("environment")


def test_preflight_interpolates_no_input_into_its_body():
    # #1080: an input interpolated into an ungated body is still source text
    # pasted into a program GitHub runs.
    body = step_run(WORKFLOW, PREFLIGHT_JOB, "approval plan")
    assert "inputs.old_ip" not in body, body
    assert "inputs.new_ip" not in body, body
    assert "inputs.dry_run" not in body, body
    env = {}
    for step in load_workflow(WORKFLOW)["jobs"][PREFLIGHT_JOB]["steps"]:
        if "approval plan" in str(step.get("name", "")).lower():
            env = step.get("env", {})
    assert env.get("IN_OLD_IP") == "${{ inputs.old_ip }}", env
    assert env.get("IN_NEW_IP") == "${{ inputs.new_ip }}", env
    assert env.get("IN_DRY_RUN") == "${{ inputs.dry_run }}", env


def test_run_name_is_left_alone():
    # Already satisfies #1107 for all three inputs; #1509 AC8 says leave it.
    run_name = load_workflow(WORKFLOW)["run-name"]
    assert "inputs.old_ip" in run_name and "inputs.new_ip" in run_name, run_name
    assert "inputs.dry_run" in run_name, run_name


# --- rendering --------------------------------------------------------------


def test_plan_names_both_ips():
    proc, summary = run_preflight({})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "`204.44.192.77`" in summary, summary
    assert "`216.222.200.253`" in summary, summary


def test_plan_states_the_whole_estate_zone_scope_in_words():
    # AC4, and the whole reason 112 is in this issue. "A records x -> y" does
    # not tell an approver that the sweep is every zone in both accounts.
    proc, summary = run_preflight({})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lowered = summary.lower()
    assert "every zone in both cloudflare accounts" in lowered, summary
    assert "ffc and cm" in lowered, summary
    # It must also be honest that the count cannot be known before approval --
    # claiming a number here would be a guess, and 112 takes no zone input.
    assert "not known before approval" in lowered, summary
    assert "takes **no zone or domain input**" in summary, summary


def test_plan_states_what_the_rewrite_does_and_does_not_do():
    proc, summary = run_preflight({})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lowered = summary.lower()
    # In-place PUT, preserving name/TTL/proxy: bounds the blast radius.
    assert "updated in place" in lowered, summary
    assert "never creates or deletes a record" in lowered, summary
    assert "never touches a non-a record" in lowered, summary
    # The collateral an operator most needs warned about: a shared old IP.
    assert "did not intend to move" in lowered, summary


def test_the_plan_names_the_http_verb_THE_SCRIPT_ACTUALLY_USES():
    """The verb in the plan is read out of the callee, not hardcoded here.

    Caught by review on #1586: the first version of this plan said the record
    is "PUT to" the new IP, twice, while `bulk-replace-a-record-ip.ps1` issues
    `Invoke-CfApi -Method PATCH`. Harmless to the run and not harmless to the
    disclosure — in a preflight whose entire purpose is to state accurately
    what the gated job will do, a wrong HTTP verb is the defect, and nothing in
    the suite could see it because every other assertion was about the plan's
    own wording.

    So this derives the expectation from the script: if the callee ever moves
    to PUT (a full replace, which would NOT preserve the fields the plan
    promises are preserved), the plan has to move with it.
    """
    source = CALLEE.read_text(encoding="utf-8")
    # The mutation call: the one with a record id in the path and a -Body.
    verbs = re.findall(
        r"Invoke-CfApi\s+-Method\s+([A-Z]+)\s+[^\n]*dns_records/\$\([^\n]*-Body", source
    )
    assert verbs, f"could not find the record-mutating Invoke-CfApi call in {CALLEE.name}"
    assert len(set(verbs)) == 1, f"more than one verb mutates a record: {verbs}"
    verb = verbs[0]
    assert verb == "PATCH", (
        f"{CALLEE.name} now mutates records with {verb}, not PATCH. That changes what the "
        "gated job does to a record's other fields, so the preflight's 'preserving its name, "
        "TTL and proxy setting' claim must be re-checked, not just the verb."
    )

    _, summary = run_preflight({"IN_DRY_RUN": "false"})
    assert verb in summary, f"the plan does not name {verb}:\n{summary}"
    # ...and must not claim a verb the script does not use.
    for wrong in ("PUT", "POST", "DELETE"):
        if wrong != verb:
            assert wrong not in summary, f"the plan claims {wrong}, which the script never sends:\n{summary}"


def test_plan_distinguishes_writing_from_not_writing():
    _, dry = run_preflight({"IN_DRY_RUN": "true"})
    assert "nothing is written" in dry.lower(), dry
    proc, wet = run_preflight({"IN_DRY_RUN": "false"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "this run writes" in wet.lower(), wet
    assert "::notice title=Approving cloudflare-prod-write will::" in proc.stdout, proc.stdout
    assert "EVERY A record holding 204.44.192.77" in proc.stdout, proc.stdout


# --- refusals ---------------------------------------------------------------


def test_empty_dry_run_mapping_fails_closed_with_no_plan():
    proc, summary = run_preflight({"IN_DRY_RUN": ""})
    assert proc.returncode != 0, proc.stdout
    assert "IN_DRY_RUN is empty" in proc.stdout, proc.stdout
    assert "Preflight passed" not in summary, summary
    assert "Refused" in summary, summary


def test_empty_old_ip_mapping_fails_closed_with_no_plan():
    # Both IPs are `required: true`, so blank means the env: mapping was
    # deleted or misspelled -- the #1080 remedy silently removed. A plan
    # naming no source address discloses nothing about which records change.
    proc, summary = run_preflight({"IN_OLD_IP": ""})
    assert proc.returncode != 0, proc.stdout
    assert "IN_OLD_IP / IN_NEW_IP is empty" in proc.stdout, proc.stdout
    assert "Preflight passed" not in summary, summary


def test_empty_new_ip_mapping_fails_closed_with_no_plan():
    proc, summary = run_preflight({"IN_NEW_IP": "   "})
    assert proc.returncode != 0, proc.stdout
    assert "IN_OLD_IP / IN_NEW_IP is empty" in proc.stdout, proc.stdout
    assert "Preflight passed" not in summary, summary


def test_a_passing_preflight_is_the_positive_control_for_those_refusals():
    # Without this the three refusals above would pass against a body that
    # refuses unconditionally and can never print a plan at all.
    proc, summary = run_preflight({})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Preflight passed" in summary, summary
    assert "Refused" not in summary, summary


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
    sys.exit(1 if failures else 0)
