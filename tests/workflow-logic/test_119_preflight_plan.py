"""Unit tests for 119's ungated preflight plan (#1509).

WHAT THE PREFLIGHT IS FOR
    119 writes `staging.<domain>` CNAMEs across many FFC-EX zones on the
    `cloudflare-prod-write` lane. Before clicking "Approve" the reviewer sees
    the run name and the run summary and nothing else — and measured on run
    `37137973071` (2026-10-03, dry_run=false) that was a workflow name, a
    `dry_run` flag, and NO scope: `domains`/`target` are dispatch inputs, and
    the REST run object carries no dispatch inputs at all, while a `waiting`
    job has no logs. #1107 made the MODE visible in `run-name`; the blast
    radius stayed invisible.

    `docs/workflow-safety-and-approvals.md` tells that approver to read the
    dry-run preview first. For this workflow one could not be made: it is
    `concurrency: cancel-in-progress: false`, so a rehearsal dispatch queues
    BEHIND the waiting run it was meant to inform. The plan therefore has to
    be produced by the run itself, before its own gate.

WHY THESE ARE BEHAVIOURAL TESTS AND NOT A GREP FOR THE JOB
    The job's existence is cheap to assert and proves almost nothing: the
    value is entirely in whether the rendered text names the zones. So these
    run the step body and read the summary it writes, the same way
    `test_702_preflight.py` proves #1506's rendering without dispatching 702.

    Two properties are the ones worth protecting, because both fail quietly:

    * a plan that prints the word "default" instead of the 13 domain names is
      indistinguishable, to a reviewer, from one that names them -- it looks
      informative and discloses nothing (AC2);
    * a REFUSED preflight that still prints a plan is worse than no plan at
      all, because the plan reads as "checks passed, safe to approve" (AC5).
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, load_workflow, step_run

WORKFLOW = "119-bulk-staging-cname-github-pages.yml"
PREFLIGHT_JOB = "preflight"
GATED_JOB = "bulk-staging-cname"

# The 13 default domains, as this test's own independent copy. Deliberately NOT
# read from either body: a guard that derives its expectation from the thing it
# checks passes when both sides are wrong together, which is the one failure a
# duplicated list actually has.
DEFAULT_DOMAINS = [
    "aprilhansen.com",
    "armstrongacesbaseball.org",
    "coronadonationalforestheritagesociety.org",
    "falloutshelterecovillage.org",
    "nj4israel.org",
    "savewatersaveplanet.org",
    "bucktownbullsbaseball.org",
    "amargraves.org",
    "bulldogztowing.com",
    "browncanyonranch.org",
    "instituteofforgiveness.org",
    "americanlegionpost64.org",
    "southamptonfriends.org",
]

PASSING = {"IN_DOMAINS": "", "IN_TARGET": "", "IN_DRY_RUN": "true"}


def run_preflight(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str]:
    """Run the preflight step. Returns (proc, summary)."""
    script = step_run(WORKFLOW, PREFLIGHT_JOB, "approval plan")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        summary.touch()
        env = child_env(
            GITHUB_STEP_SUMMARY=str(summary),
            HOME=str(tdp),
            **PASSING,
        )
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


# --- structure: the plan must be reachable without the gate -----------------


def test_preflight_job_is_ungated_and_uncredentialed():
    # The whole point is a plan the approver can read BEFORE approving. A
    # preflight behind the same environment could only ever print its plan
    # after the decision it exists to inform.
    job = load_workflow(WORKFLOW)["jobs"][PREFLIGHT_JOB]
    assert "environment" not in job, job
    body = step_run(WORKFLOW, PREFLIGHT_JOB, "approval plan")
    # No credential action, and no Key Vault / OIDC wiring in the ungated lane.
    assert "cloudflare-tokens-from-kv" not in body, body
    assert "secrets." not in body, body


def test_gated_job_needs_the_preflight():
    # Without `needs:` the refusals below are decorative: the write job would
    # run anyway and the approver would see a summary saying "Refused".
    job = load_workflow(WORKFLOW)["jobs"][GATED_JOB]
    needs = job.get("needs")
    needs = [needs] if isinstance(needs, str) else (needs or [])
    assert PREFLIGHT_JOB in needs, needs
    assert job.get("environment") == "cloudflare-prod-write", job.get("environment")


def test_preflight_interpolates_no_input_into_its_body():
    # #1080 binds in an ungated job too: an input interpolated here is still
    # source text pasted into a program GitHub runs on a repo-scoped runner.
    body = step_run(WORKFLOW, PREFLIGHT_JOB, "approval plan")
    assert "inputs.domains" not in body, body
    assert "inputs.target" not in body, body
    assert "inputs.dry_run" not in body, body
    env = {}
    for step in load_workflow(WORKFLOW)["jobs"][PREFLIGHT_JOB]["steps"]:
        if "approval plan" in str(step.get("name", "")).lower():
            env = step.get("env", {})
    assert env.get("IN_DOMAINS") == "${{ inputs.domains }}", env
    assert env.get("IN_TARGET") == "${{ inputs.target }}", env
    assert env.get("IN_DRY_RUN") == "${{ inputs.dry_run }}", env


def test_run_name_is_left_alone():
    # #1107 is already satisfied, and a free-text input in `run-name` would be
    # a new rendering path nobody has audited (#1509 AC7/AC8).
    run_name = load_workflow(WORKFLOW)["run-name"]
    assert "inputs.dry_run" in run_name, run_name
    assert "inputs.domains" not in run_name, run_name
    assert "inputs.target" not in run_name, run_name


def test_the_two_default_domain_lists_have_not_drifted():
    """The preflight's copy of the default list must equal the gated job's.

    The authoritative list lives in a pwsh body that runs only after approval,
    so naming the zones beforehand means a second copy. That copy is the whole
    disclosure: if it drifts, the plan names zones the run will not touch and
    omits zones it will, while every other check in the repo stays green. The
    gated job is deliberately not fed from the preflight (a plan the write
    path depends on is not a read-only preflight), so nothing but this test
    couples them.
    """
    gated = step_run(WORKFLOW, GATED_JOB, "Run bulk staging CNAME wiring")
    gated_list = re.search(r"\$defaultDomains\s*=\s*@\((.*?)\)", gated, re.S)
    assert gated_list, "could not find $defaultDomains in the gated step body"
    from_gated = re.findall(r"'([^']+)'", gated_list.group(1))

    pre = step_run(WORKFLOW, PREFLIGHT_JOB, "approval plan")
    pre_list = re.search(r'default_domains="([^"]+)"', pre)
    assert pre_list, "could not find default_domains= in the preflight body"
    from_pre = [d.strip() for d in pre_list.group(1).split(",") if d.strip()]

    # Against the test's own copy first, so the two bodies agreeing on a wrong
    # list cannot pass, then against each other.
    assert from_gated == DEFAULT_DOMAINS, from_gated
    assert from_pre == DEFAULT_DOMAINS, from_pre
    assert from_pre == from_gated, (from_pre, from_gated)


# --- rendering: what the approver actually reads -----------------------------


def test_blank_domains_prints_all_thirteen_zones_by_name():
    # AC2: the word "default" is not a disclosure. Every zone, spelled out.
    proc, summary = run_preflight({})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for d in DEFAULT_DOMAINS:
        assert f"`staging.{d}`" in summary, f"{d} missing from the plan:\n{summary}"
    assert "**13**" in summary, summary
    # ...and it must say the list IS the default, not pass it off as input.
    assert "default FFC-EX staging list" in summary, summary


def test_overridden_domains_prints_the_given_list_and_not_the_default():
    proc, summary = run_preflight({"IN_DOMAINS": "example.org, second.com"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "`staging.example.org`" in summary, summary
    assert "`staging.second.com`" in summary, summary
    assert "**2**" in summary, summary
    # A default leaking into an overridden run would overstate the blast radius
    # by 13 zones, which trains an approver to disbelieve the plan.
    for d in DEFAULT_DOMAINS:
        assert f"`staging.{d}`" not in summary, f"default {d} leaked:\n{summary}"
    assert "`domains` input" in summary, summary


def test_blank_target_is_described_and_never_guessed():
    # AC2: the callee resolves the canonical Pages host itself
    # (Get-GhPagesWwwTarget, #778). A default spelled out here would be a
    # second copy free to drift, so the plan must say who resolves it.
    proc, summary = run_preflight({"IN_TARGET": ""})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Get-GhPagesWwwTarget" in summary, summary
    assert "does not guess" in summary, summary
    assert "freeforcharity.github.io" not in summary, (
        "the preflight spelled out a CNAME target default; that is the second "
        f"copy #778 warns about:\n{summary}"
    )


def test_supplied_target_is_printed_verbatim():
    proc, summary = run_preflight({"IN_TARGET": "someorg.github.io"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "`someorg.github.io`" in summary, summary
    assert "Get-GhPagesWwwTarget" not in summary, summary


def test_plan_states_the_destructive_delete_then_create_edge():
    # AC3. `bulk-staging-cname-github-pages.ps1` lists EVERY record at
    # staging.<domain> regardless of type and deletes all of them unless the
    # name already holds exactly one correct DNS-only CNAME. "Wire a CNAME"
    # reads as an upsert and is not one.
    proc, summary = run_preflight({})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lowered = summary.lower()
    assert "delete-then-create, not an upsert" in lowered, summary
    assert "deletes all of them" in lowered, summary
    # ...and what it does NOT touch, which is what bounds the blast radius.
    assert "apex" in lowered and "www" in lowered, summary


def test_plan_distinguishes_writing_from_not_writing():
    _, dry = run_preflight({"IN_DRY_RUN": "true"})
    assert "nothing is written" in dry.lower(), dry
    proc, wet = run_preflight({"IN_DRY_RUN": "false"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "this run writes" in wet.lower(), wet
    # The annotation carries the same verdict to the top of the run page,
    # beside the "Review deployments" button.
    assert "::notice title=Approving cloudflare-prod-write will::" in proc.stdout, proc.stdout
    assert "DELETES every record" in proc.stdout, proc.stdout


def test_the_notice_names_the_zone_count_on_a_writing_run():
    proc, _ = run_preflight({"IN_DRY_RUN": "false", "IN_DOMAINS": "a.org,b.org,c.org"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "3 zone(s)" in proc.stdout, proc.stdout


# --- refusals: a refused preflight prints no plan ---------------------------


def test_empty_dry_run_mapping_fails_closed_with_no_plan():
    # AC6. The gated job would still write, so a blank "does this write?" line
    # is the exact failure this job exists to remove.
    proc, summary = run_preflight({"IN_DRY_RUN": ""})
    assert proc.returncode != 0, proc.stdout
    assert "IN_DRY_RUN is empty" in proc.stdout, proc.stdout
    assert "Preflight passed" not in summary, summary
    assert "Zones this run acts on" not in summary, summary
    assert "Refused" in summary, summary


def test_empty_resolved_domain_list_fails_closed_with_no_plan():
    # AC5. Reachable if the default list is ever emptied: `domains` is
    # `required: false` with a blank default, so a blank box is the common
    # path and cannot itself refuse. A plan naming zero zones tells an
    # approver nothing while looking like a clean preflight.
    proc, summary = run_preflight({"IN_DOMAINS": "   ", "DEFAULTS_EMPTY": "1"})
    # The body's own default fills a blank input, so emptiness has to be forced
    # through the default itself; assert the guard exists and is reached by
    # running the body with the default removed.
    body = step_run(WORKFLOW, PREFLIGHT_JOB, "approval plan")
    emptied = re.sub(r'default_domains="[^"]+"', 'default_domains=""', body)
    assert 'default_domains=""' in emptied, "could not empty the default list for this test"
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        s = tdp / "summary.md"
        s.touch()
        env = child_env(GITHUB_STEP_SUMMARY=str(s), HOME=str(tdp), **PASSING)
        env["IN_DOMAINS"] = ""
        proc = subprocess.run(
            ["bash", "-c", emptied],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        summary = s.read_text(encoding="utf-8")
    assert proc.returncode != 0, proc.stdout
    assert "resolved domain list is empty" in proc.stdout, proc.stdout
    assert "Preflight passed" not in summary, summary


def test_a_passing_preflight_is_the_positive_control_for_those_refusals():
    # Without this, both refusal tests above would pass against a body that
    # refuses unconditionally -- including one that can never print a plan.
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
