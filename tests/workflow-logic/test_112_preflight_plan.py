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
    # In-place PATCH, preserving name/TTL/proxy: bounds the blast radius. (Said "PUT"
    # until review caught it — the sibling verb test below asserts PATCH and asserts
    # "PUT" is absent from the summary, so this comment contradicted its own module.)
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


# --- the notice is a workflow COMMAND, so its text is an injection surface --
#
# Raised by Copilot on #1586 as a High finding, and it was real. The notice
# carries both dispatcher IPs, and this step validates only that they are
# non-empty -- never their shape. Workflow commands are line-based, so an
# unescaped newline ends the `::notice` line and anything after it opening
# with `::` is a command the runner obeys: a forged annotation on the very
# run page an approver reads before releasing the write-scoped Cloudflare
# tokens. Remedy is the `esc()` helper this repo already uses in
# `704-website-analytics-wire.yml`. The sibling cases live in
# `test_119_preflight_plan.py`, where the payload arrives through `domains`.


def _notice_lines(stdout: str) -> list[str]:
    return [ln for ln in stdout.splitlines() if ln.startswith("::notice")]


def test_a_newline_in_an_ip_cannot_forge_a_second_workflow_command():
    # Asserted on the emitted bytes rather than on a runner: one `::notice`
    # line, no stdout line that IS the payload's command, and the newline
    # surviving as `%0A` so the approver can still see what was dispatched.
    #
    # That last assertion is also what pins the substitution ORDER inside
    # `esc()`, which was measured rather than assumed: substitute `%` last and
    # the `%0A` this helper just introduced is itself escaped to `%250A`, so
    # THIS test fails. Not a security property -- the newline is gone either
    # way -- but the annotation would show the literal text `%0A` instead of a
    # line break, which is the rendering `704-website-analytics-wire.yml`'s
    # comment is about.
    payload = "203.0.113.9\n::error::One record changes. Safe to approve."
    proc, _ = run_preflight({"IN_DRY_RUN": "false", "IN_OLD_IP": payload})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    notices = _notice_lines(proc.stdout)
    assert len(notices) == 1, proc.stdout
    forged = [ln for ln in proc.stdout.splitlines() if ln.startswith("::error::One record")]
    assert not forged, f"the payload reached stdout as its own command line:\n{proc.stdout}"
    assert "%0A" in notices[0], notices[0]


def test_a_dispatcher_supplied_percent_0A_is_neutralised_not_decoded():
    # The second half of the injection surface, and the one a newline test
    # alone misses: the dispatcher need not send a real newline, because the
    # runner DECODES `%0A` in a workflow command's text. So the four literal
    # characters `%0A` are a payload too, and the only thing that stops them
    # is escaping the `%` -- after which the runner prints them instead of
    # obeying them. Mutation-checked: with the notice unescaped this fails
    # alongside the newline case.
    payload = "203.0.113.9%0A::error::forged"
    proc, _ = run_preflight({"IN_DRY_RUN": "false", "IN_NEW_IP": payload})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    notices = _notice_lines(proc.stdout)
    assert len(notices) == 1, proc.stdout
    assert "%250A" in notices[0], notices[0]
    assert "%0A::error::" not in notices[0], notices[0]


def test_ordinary_input_reaches_the_notice_unmangled():
    # Positive control for both tests above: an `esc()` that escaped
    # indiscriminately, or dropped the value, would satisfy them while the
    # notice stopped disclosing which records change.
    proc, _ = run_preflight({"IN_DRY_RUN": "false"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    notices = _notice_lines(proc.stdout)
    assert len(notices) == 1, proc.stdout
    assert "204.44.192.77" in notices[0] and "216.222.200.253" in notices[0], notices[0]
    assert "%0A" not in notices[0] and "%25" not in notices[0], notices[0]


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
