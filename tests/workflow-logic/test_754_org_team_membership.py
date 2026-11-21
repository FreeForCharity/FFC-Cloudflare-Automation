"""Tests for 754's team-membership script (`scripts/org-team-membership.sh`)
and the two-job shape of `754-org-team-membership.yml`.

Behavioural tests run the real script file against a fake `gh` written by
this module, with every outcome asserted on OUTPUT as well as exit code: a
harness that cannot start also exits non-zero (CLAUDE.md, #1139).

Structural tests pin what makes the dry run and the list safe (#983): the
ungated `preflight` job on the read lane runs the SAME file the gated `apply`
job runs, `apply` is skipped at JOB level on a dry run and for action=list,
and `dry_run` defaults to true.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "org-team-membership.sh"
WORKFLOW = "754-org-team-membership.yml"

# A fake `gh` that answers the endpoints the script calls from environment
# variables, and logs every invocation to $TEST_GH_LOG.
FAKE_GH = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >>"$TEST_GH_LOG"
args="$*"
case "$args" in
  "api -X PUT orgs/"*"/teams/"*"/memberships/"*)
    printf '%s' "${TEST_PUT_BODY:-{\"state\":\"active\",\"role\":\"member\"}}"; exit 0 ;;
  "api -X DELETE orgs/"*"/teams/"*"/memberships/"*)
    exit 0 ;;
  "api -X POST orgs/"*"/teams "*)
    [ "${TEST_POST_FAIL:-}" = "1" ] && { echo "gh: Must have admin rights (HTTP 403)" >&2; exit 1; }
    echo "${TEST_NEW_SLUG:-sponsoring-admins}"; exit 0 ;;
  "api --paginate orgs/"*"/teams?per_page=100 "*)
    printf '%b' "${TEST_TEAMS:-}"; exit 0 ;;
  "api --paginate orgs/"*"/teams/"*"/members?per_page=100 "*)
    slug="${args#api --paginate orgs/*/teams/}"; slug="${slug%%/members*}"
    [ "${TEST_MEMBERS_FAIL:-}" = "$slug" ] && { echo "gh: Not Found (HTTP 404)"; exit 1; }
    var="TEST_MEMBERS_${slug//-/_}"; printf '%b' "${!var:-}"; exit 0 ;;
  "api orgs/"*"/teams/"*"/memberships/"*)
    if [ -n "${TEST_MEMBERSHIP:-}" ]; then echo "$TEST_MEMBERSHIP"; exit 0; fi
    echo "gh: Not Found (HTTP 404)"; exit 1 ;;
  "api orgs/"*"/teams/"*)
    if [ "${TEST_TEAM_EXISTS:-1}" = "1" ]; then echo "team"; exit 0; fi
    echo "gh: Not Found (HTTP 404)"; exit 1 ;;
  "api orgs/"*)
    echo "FreeForCharity"; exit 0 ;;
  "api users/"*)
    [ "${TEST_USER_MISSING:-}" = "1" ] && { echo "gh: Not Found (HTTP 404)"; exit 1; }
    [ "${TEST_USER_MISSING:-}" = "5xx" ] && { echo "gh: Server Error (HTTP 502)"; exit 1; }
    echo "user"; exit 0 ;;
esac
echo "fake gh: unhandled: $args" >&2
exit 3
"""


def run_script(env_overrides: dict) -> tuple[str, str, str, str, int]:
    """Run the script. Returns (summary, stdout, stderr, gh_log, rc)."""
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        bindir = td / "bin"
        bindir.mkdir()
        gh = bindir / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        summary = td / "summary.md"
        gh_log = td / "gh.log"
        summary.touch()
        gh_log.touch()
        env = child_env(
            bindir,
            GITHUB_STEP_SUMMARY=str(summary),
            TEST_GH_LOG=str(gh_log),
            HOME=str(td),
            GH_TOKEN="fake-token",
            IN_ORG="FreeForCharity",
            IN_ACTION="add",
            IN_TEAM="sponsoring-admins",
            IN_USER="octocat",
            IN_ROLE="member",
            IN_CREATE="false",
            IN_DRY="false",
        )
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", str(SCRIPT)],
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


# --- list --------------------------------------------------------------------


def test_list_prints_every_team_with_its_members_and_writes_nothing():
    summary, out, err, log, rc = run_script(
        {
            "IN_ACTION": "list",
            "IN_TEAM": "",
            "IN_USER": "",
            "TEST_TEAMS": "ffc-orgwide-writers\\tFFC-ORGWIDE-Writers\\tclosed\\t-\\tWrite\\n"
            "volunteers\\tVolunteers\\tclosed\\t-\\tFFC volunteers\\n",
            "TEST_MEMBERS_ffc_orgwide_writers": "alice\\nbob\\n",
            "TEST_MEMBERS_volunteers": "",
        }
    )
    assert rc == 0, out + err
    assert "TEAM ffc-orgwide-writers | FFC-ORGWIDE-Writers | closed | parent=- | members: alice bob" in out, out
    assert "TEAM volunteers | Volunteers | closed | parent=- | members: (none)" in out, out
    assert "Listed 2 team(s) in FreeForCharity." in out, out
    assert "| `volunteers` |" in summary, summary
    assert "-X " not in log, log


def test_list_fails_when_a_teams_members_cannot_be_read():
    _s, out, err, _log, rc = run_script(
        {
            "IN_ACTION": "list",
            "TEST_TEAMS": "secret-team\\tSecret\\tsecret\\t-\\t\\n",
            "TEST_MEMBERS_FAIL": "secret-team",
        }
    )
    assert rc == 1, out + err
    assert "1 team(s) listed without their members; the list is incomplete." in out, out


# --- add ---------------------------------------------------------------------


def test_a_dry_run_add_plans_and_sends_nothing():
    summary, out, err, log, rc = run_script({"IN_DRY": "true"})
    assert rc == 0, out + err
    assert "[DRY RUN] Would add octocat to sponsoring-admins as member." in out, out
    assert "-X PUT" not in log, log
    assert "orgs/FreeForCharity/teams/sponsoring-admins/memberships/octocat" in log, log
    assert "Planned: octocat (would add octocat to sponsoring-admins as member)" in summary, summary


def test_add_puts_the_membership_and_reports_active():
    _s, out, err, log, rc = run_script({})
    assert rc == 0, out + err
    assert "-X PUT orgs/FreeForCharity/teams/sponsoring-admins/memberships/octocat -f role=member" in log, log
    assert "octocat is now an active member of sponsoring-admins." in out, out


def test_add_of_a_non_org_member_reports_pending():
    _s, out, err, _log, rc = run_script({"TEST_PUT_BODY": '{"state":"pending","role":"member"}'})
    assert rc == 0, out + err
    assert "pending until they accept the org invitation" in out, out


def test_add_leaves_an_existing_member_alone():
    _s, out, err, log, rc = run_script({"TEST_MEMBERSHIP": "active member"})
    assert rc == 0, out + err
    assert "octocat is already an active member of sponsoring-admins; nothing to do." in out, out
    assert "-X PUT" not in log, log


def test_add_never_resends_to_a_pending_member():
    _s, out, err, log, rc = run_script({"TEST_MEMBERSHIP": "pending member"})
    assert rc == 0, out + err
    assert "not re-sending" in out, out
    assert "-X PUT" not in log, log


def test_add_to_a_missing_team_refuses_unless_create_is_set():
    _s, out, err, log, rc = run_script({"TEST_TEAM_EXISTS": "0"})
    assert rc == 1, out + err
    assert "Team 'sponsoring-admins' does not exist in FreeForCharity." in out, out
    assert "-X " not in log, log


def test_create_team_makes_a_closed_team_then_adds():
    _s, out, err, log, rc = run_script({"TEST_TEAM_EXISTS": "0", "IN_CREATE": "true"})
    assert rc == 0, out + err
    assert "-X POST orgs/FreeForCharity/teams -f name=sponsoring-admins -f privacy=closed" in log, log
    assert "Created team 'sponsoring-admins'" in out, out
    assert "-X PUT orgs/FreeForCharity/teams/sponsoring-admins/memberships/octocat" in log, log


def test_create_team_uses_the_slug_github_returns():
    _s, out, err, log, rc = run_script(
        {"TEST_TEAM_EXISTS": "0", "IN_CREATE": "true", "TEST_NEW_SLUG": "sponsoring-admins-1"}
    )
    assert rc == 0, out + err
    assert "using 'sponsoring-admins-1'" in out, out
    assert "-X PUT orgs/FreeForCharity/teams/sponsoring-admins-1/memberships/octocat" in log, log


def test_a_failed_user_lookup_is_not_reported_as_not_found():
    _s, out, err, log, rc = run_script({"TEST_USER_MISSING": "5xx"})
    assert rc == 1, out + err
    assert "Could not look up GitHub user 'octocat'" in out and "was not found" not in out, out
    assert "-X PUT" not in log, log


def test_a_glob_username_is_not_expanded_to_file_names():
    _s, out, err, log, rc = run_script({"IN_USER": "*"})
    assert rc == 1, out + err
    assert "Invalid GitHub username: '*'." in out, out
    assert "users/" not in log, log


def test_a_pending_member_with_another_role_gets_the_role_change():
    _s, out, err, log, rc = run_script({"TEST_MEMBERSHIP": "pending member", "IN_ROLE": "maintainer"})
    assert rc == 0, out + err
    assert "-f role=maintainer" in log, log


def test_create_team_dry_run_creates_nothing():
    _s, out, err, log, rc = run_script({"TEST_TEAM_EXISTS": "0", "IN_CREATE": "true", "IN_DRY": "true"})
    assert rc == 0, out + err
    assert "[DRY RUN] Would create team 'sponsoring-admins'" in out, out
    assert "-X " not in log, log


def test_a_failed_create_names_the_scope():
    _s, out, err, _log, rc = run_script({"TEST_TEAM_EXISTS": "0", "IN_CREATE": "true", "TEST_POST_FAIL": "1"})
    assert rc == 1, out + err
    assert "lacks organization members WRITE" in out, out


def test_several_users_are_deduped_and_a_bad_login_fails_the_run():
    _s, out, err, log, rc = run_script({"IN_USER": "Tyler-Carlotto, phoganuci phoganuci bad!login"})
    assert rc == 1, out + err
    assert log.count("memberships/phoganuci -f role=member") == 1, log
    assert "memberships/Tyler-Carlotto -f role=member" in log, log
    assert "Invalid GitHub username: 'bad!login'." in out, out


def test_an_unknown_user_is_not_added():
    _s, out, err, log, rc = run_script({"TEST_USER_MISSING": "1"})
    assert rc == 1, out + err
    assert "GitHub user 'octocat' was not found" in out, out
    assert "-X PUT" not in log, log


# --- remove ------------------------------------------------------------------


def test_remove_deletes_only_the_team_membership():
    _s, out, err, log, rc = run_script({"IN_ACTION": "remove", "TEST_MEMBERSHIP": "active member"})
    assert rc == 0, out + err
    assert "-X DELETE orgs/FreeForCharity/teams/sponsoring-admins/memberships/octocat" in log, log
    assert "orgs/FreeForCharity/memberships" not in log, log  # never the org membership
    assert "Removed octocat from sponsoring-admins." in out, out


def test_remove_of_someone_not_on_the_team_is_a_no_op():
    _s, out, err, log, rc = run_script({"IN_ACTION": "remove"})
    assert rc == 0, out + err
    assert "octocat is not on sponsoring-admins; nothing to remove." in out, out
    assert "-X DELETE" not in log, log


def test_remove_never_creates_a_team():
    _s, out, err, log, rc = run_script({"IN_ACTION": "remove", "TEST_TEAM_EXISTS": "0", "IN_CREATE": "true"})
    assert rc == 1, out + err
    assert "does not exist in FreeForCharity, so there is nobody to remove" in out, out
    assert "-X " not in log, log


# --- input validation --------------------------------------------------------


def test_bad_inputs_are_refused_before_any_write():
    for over, text in (
        ({"IN_ACTION": "promote"}, "Invalid action 'promote'"),
        ({"IN_TEAM": "Bad Team!"}, "Invalid team slug"),
        ({"IN_TEAM": ""}, "'team' (the team slug) is required for add"),
        ({"IN_ROLE": "owner"}, "Invalid team role 'owner'"),
        ({"IN_USER": ""}, "'username' is required for add"),
        ({"IN_DRY": "maybe"}, "Invalid dry_run value"),
        ({"GH_TOKEN": ""}, "GH_TOKEN is not set"),
    ):
        _s, out, err, log, rc = run_script(over)
        assert rc == 1, (over, out + err)
        assert text in out, (over, out)
        assert "-X " not in log, (over, log)


# --- workflow shape ----------------------------------------------------------


def test_workflow_shape_keeps_list_and_dry_run_off_the_gate():
    wf = load_workflow(WORKFLOW)
    on = wf.get("on") or wf.get(True)
    inputs = on["workflow_dispatch"]["inputs"]
    assert inputs["dry_run"]["default"] is True
    assert inputs["action"]["default"] == "list"
    pre, app = wf["jobs"]["preflight"], wf["jobs"]["apply"]
    assert pre["environment"] == "github-prod-read"
    assert app["environment"] == "github-prod"
    assert app["needs"] == "preflight"
    assert app["if"] == "inputs.dry_run != true && inputs.action != 'list'"

    def runs(job):
        return [s for s in job["steps"] if "org-team-membership.sh" in str(s.get("run", ""))]

    (p,), (a,) = runs(pre), runs(app)
    assert p["env"]["IN_DRY"] == "true"
    assert a["env"]["IN_DRY"] == "${{ inputs.dry_run }}"
    pre_kv = " ".join(str(s.get("run", "")) for s in pre["steps"])
    app_kv = " ".join(str(s.get("run", "")) for s in app["steps"])
    assert "read-all-cbm-ffc-copilot-mcp-github-pat" in pre_kv and "wr-all-cbm-github-pat" not in pre_kv
    assert "wr-all-cbm-github-pat" in app_kv
    assert wf["concurrency"]["cancel-in-progress"] is False


def main():
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"  FAIL {name}: {e!r}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
