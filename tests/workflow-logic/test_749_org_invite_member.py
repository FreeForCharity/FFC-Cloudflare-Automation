"""Tests for 749's org-invitation script (`scripts/org-invite-member.sh`) and
the two-job shape of `749-org-invite-member.yml`.

Behavioural tests run the real script file against the fake `gh` in
`harness/`, with every outcome asserted on OUTPUT as well as exit code — a
harness that cannot start also exits non-zero (CLAUDE.md, #1139), so
`rc != 0` alone cannot tell the script from a broken host.

Structural tests pin what makes the dry run safe (#983): the ungated
`preflight` job on the read lane runs the SAME file the gated `invite` job
runs, `invite` is skipped at JOB level on a dry run, and `dry_run` defaults
to true.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, load_workflow

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "org-invite-member.sh"
WORKFLOW = "749-org-invite-member.yml"


def _bash() -> str:
    """Resolve a bash that can open a script named by a Windows-style path.

    This module runs the script from a FILE rather than `bash -c <text>`, so
    on the Windows Conductor host a bare `bash` (the MSYS binary) eats the
    backslashes in the path argument and exits 127 — indistinguishable from a
    rejection if only the exit code is read. Same resolver as
    test_704_analytics_wire_validation.py / test_722_large_blob_guard.py.
    """
    if sys.platform == "win32":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if pathlib.Path(candidate).exists():
                return candidate
    return "bash"


def run_script(env_overrides: dict) -> tuple[str, str, str, str, int]:
    """Run the script. Returns (summary, stdout, stderr, gh_log, rc)."""
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        summary = td / "summary.md"
        gh_log = td / "gh.log"
        summary.touch()
        gh_log.touch()
        env = child_env(
            HARNESS_DIR,
            GITHUB_STEP_SUMMARY=str(summary),
            TEST_GH_LOG=str(gh_log),
            HOME=str(td),
            GH_TOKEN="fake-token",
            IN_ORG="FreeForCharity",
            IN_USER="octocat",
            IN_ROLE="member",
            IN_DRY="false",
        )
        env.update(env_overrides)
        proc = subprocess.run(
            [_bash(), str(SCRIPT)],
            env=env,
            cwd=str(td),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
        )
        return (
            summary.read_text(encoding="utf-8"),
            proc.stdout,
            proc.stderr,
            gh_log.read_text(encoding="utf-8"),
            proc.returncode,
        )


# --- behaviour ---------------------------------------------------------------


def test_a_dry_run_plans_and_sends_nothing():
    summary, out, err, log, rc = run_script({"IN_DRY": "true"})
    assert rc == 0, out + err
    assert "[DRY RUN] Would invite octocat to FreeForCharity as member." in out, out
    assert "octocat (would invite octocat to FreeForCharity as member)" in summary, summary
    assert "-X PUT" not in log, log
    # It still looked before planning: the user and the current membership.
    assert "users/octocat" in log, log
    assert "orgs/FreeForCharity/memberships/octocat" in log, log


def test_a_non_member_is_invited_as_pending():
    summary, out, err, log, rc = run_script({})
    assert rc == 0, out + err
    assert "Invited octocat to FreeForCharity as member; pending acceptance." in out, out
    assert "octocat (invited as member, pending)" in summary, summary
    assert "-X PUT orgs/FreeForCharity/memberships/octocat -f role=member" in log, log


def test_the_admin_role_is_passed_through():
    _summary, out, err, log, rc = run_script(
        {"IN_ROLE": "admin", "TEST_ORG_MEMBERSHIP_PUT_BODY": '{"state":"pending","role":"admin"}'}
    )
    assert rc == 0, out + err
    assert "-f role=admin" in log, log
    assert "Invited octocat to FreeForCharity as admin" in out, out


def test_an_active_member_with_the_role_is_left_alone():
    summary, out, err, log, rc = run_script({"TEST_ORG_MEMBERSHIP": "active member"})
    assert rc == 0, out + err
    assert "octocat is already an active member of FreeForCharity; nothing to do." in out, out
    assert "octocat (already member)" in summary, summary
    assert "-X PUT" not in log, log


def test_an_active_member_gets_a_role_change_not_an_invitation():
    summary, out, err, log, rc = run_script(
        {
            "TEST_ORG_MEMBERSHIP": "active member",
            "IN_ROLE": "admin",
            "TEST_ORG_MEMBERSHIP_PUT_BODY": '{"state":"active","role":"admin"}',
        }
    )
    assert rc == 0, out + err
    assert "-f role=admin" in log, log
    assert "octocat is now an active admin of FreeForCharity." in out, out
    assert "octocat (admin)" in summary, summary
    assert "pending" not in out, out


def test_a_dry_run_names_a_role_change_as_such():
    _summary, out, err, log, rc = run_script(
        {"TEST_ORG_MEMBERSHIP": "active member", "IN_ROLE": "admin", "IN_DRY": "true"}
    )
    assert rc == 0, out + err
    assert "Would change role of octocat in FreeForCharity from member to admin." in out, out
    assert "-X PUT" not in log, log


def test_a_pending_invitation_is_not_resent():
    summary, out, err, log, rc = run_script({"TEST_ORG_MEMBERSHIP": "pending member"})
    assert rc == 0, out + err
    assert "already has a pending invitation" in out, out
    assert "octocat (invitation already pending as member)" in summary, summary
    assert "-X PUT" not in log, log


def test_an_unreadable_membership_is_unknown_never_a_state():
    """L02: a 403 on the pre-read must not be reported as the user's state. The
    live PUT still goes ahead — GitHub's answer to it is authoritative."""
    summary, out, err, log, rc = run_script({"TEST_ORG_MEMBERSHIP": "error"})
    assert rc == 0, out + err
    assert "Could not read octocat's membership" in out, out
    assert "proceeding as if unknown" in out, out
    assert "-X PUT orgs/FreeForCharity/memberships/octocat" in log, log
    assert "Invited octocat to FreeForCharity as member" in out, out
    # The error body is in the warning, not mistaken for a membership state.
    assert "already" not in out, out
    assert "403" not in summary, summary


def test_a_failed_invitation_fails_the_run_and_names_the_scope():
    summary, out, err, _log, rc = run_script({"TEST_ORG_MEMBERSHIP_PUT_FAIL": "1"})
    assert rc == 1, out + err
    assert "::error::Could not invite 'octocat' to FreeForCharity" in out, out
    assert "Members: write" in out, out
    assert "Failed: octocat" in summary, summary
    assert "Invited/in place" not in summary, summary
    assert "1 user(s) could not be processed" in out, out


def test_an_unknown_user_fails_without_a_put():
    summary, out, err, log, rc = run_script({"TEST_USER_NOT_FOUND": "1"})
    assert rc == 1, out + err
    assert "::error::GitHub user 'octocat' was not found" in out, out
    assert "octocat (not found)" in summary, summary
    assert "-X PUT" not in log, log


def test_a_bad_role_is_refused_before_any_api_call():
    _summary, out, err, log, rc = run_script({"IN_ROLE": "owner"})
    assert rc == 1, out + err
    assert "::error::Invalid role 'owner'" in out, out
    assert log.strip() == "", log


def test_an_empty_username_is_refused():
    _summary, out, err, log, rc = run_script({"IN_USER": "   "})
    assert rc == 1, out + err
    assert "::error::No username given" in out, out
    assert log.strip() == "", log


def test_an_unreadable_org_fails_before_any_user_is_touched():
    _summary, out, err, log, rc = run_script({"TEST_ORG_GET_FAIL": "1"})
    assert rc == 1, out + err
    assert "::error::Organization 'FreeForCharity' is not readable" in out, out
    assert "users/octocat" not in log, log


def test_a_list_of_users_is_split_and_deduped():
    summary, out, err, log, rc = run_script({"IN_USER": "@octocat, hubot\n octocat"})
    assert rc == 0, out + err
    assert log.count("-X PUT orgs/FreeForCharity/memberships/") == 2, log
    assert "-X PUT orgs/FreeForCharity/memberships/hubot" in log, log
    assert "octocat (invited as member, pending)" in summary, summary
    assert "hubot (invited as member, pending)" in summary, summary


def test_a_bad_login_in_a_list_does_not_stop_the_others():
    summary, out, err, log, rc = run_script({"IN_USER": "-bad-, octocat"})
    assert rc == 1, out + err  # one failure fails the run…
    assert "::error::Invalid GitHub username: '-bad-'" in out, out
    assert "-X PUT orgs/FreeForCharity/memberships/octocat" in log, log  # …but the rest ran
    assert "Failed: -bad- (invalid login)" in summary, summary
    assert "octocat (invited as member, pending)" in summary, summary


# --- shape (#983: the dry run must never reach the gate) ---------------------


def _jobs() -> dict:
    return load_workflow(WORKFLOW)["jobs"]


def _run_bodies(job: dict) -> str:
    return "\n".join(str(s.get("run") or "") for s in job.get("steps") or [])


def test_both_jobs_run_the_same_script_file():
    jobs = _jobs()
    for job_id in ("preflight", "invite"):
        assert "bash scripts/org-invite-member.sh" in _run_bodies(jobs[job_id]), job_id
    assert SCRIPT.exists(), SCRIPT


def test_preflight_is_ungated_on_the_read_lane_and_only_plans():
    preflight = _jobs()["preflight"]
    assert preflight.get("environment") == "github-prod-read", preflight.get("environment")
    body = _run_bodies(preflight)
    assert "read-all-cbm-ffc-copilot-mcp-github-pat" in body, body
    assert "wr-all-" not in body, "preflight must never load the writer PAT"
    plan = next(s for s in preflight["steps"] if "org-invite-member.sh" in str(s.get("run")))
    assert str(plan["env"]["IN_DRY"]).lower() == "true", plan["env"]


def test_invite_is_gated_and_skipped_at_job_level_on_a_dry_run():
    invite = _jobs()["invite"]
    assert invite.get("environment") == "github-prod", invite.get("environment")
    assert "inputs.dry_run != true" in str(invite.get("if")), invite.get("if")
    assert invite.get("needs") == "preflight", invite.get("needs")
    assert "wr-all-cbm-github-pat" in _run_bodies(invite)


def test_dry_run_defaults_to_true_on_both_triggers():
    wf = load_workflow(WORKFLOW)
    on = wf.get("on") if "on" in wf else wf.get(True)
    for trigger in ("workflow_dispatch", "workflow_call"):
        dry = on[trigger]["inputs"]["dry_run"]
        assert dry["type"] == "boolean", (trigger, dry)
        assert dry["default"] is True, (trigger, dry)


def test_inputs_reach_the_script_as_env_never_as_text():
    """#1080: dispatch inputs must arrive as data, not be pasted into a body."""
    for job_id, job in _jobs().items():
        for step in job["steps"]:
            run = str(step.get("run") or "")
            assert "inputs." not in run, (job_id, step.get("name"))


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
