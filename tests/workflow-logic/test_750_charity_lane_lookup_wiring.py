"""Wiring tests for workflow 750 (charity-lane duplicate lookup, epic #1618).

The lookup logic is pinned by test_charity_lane_lookup.py. This module pins the
workflow around it: no environment or credential, free text only through env,
the issue_number guard, the argument assembly, and that the job's one write (the
issue comment) happens only when an issue number is given. The run body is
executed with stub `python3` and `gh` on PATH, so nothing reaches the network.
"""

from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow

WORKFLOW = "750-charity-lane-lookup.yml"
STEP = "Look up the charity"


def _bash() -> str:
    if sys.platform == "win32":
        for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files (x86)\Git\bin\bash.exe"):
            if pathlib.Path(candidate).exists():
                return candidate
    return "bash"


def _step() -> dict:
    return find_step(load_workflow(WORKFLOW), "lookup", STEP)


def _stub(path: pathlib.Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run_body(**env_in: str) -> tuple[int, str, str, str]:
    """Run the step body. Returns (rc, stdout+stderr, python args log, gh log)."""
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        bin_dir = td / "bin"
        bin_dir.mkdir()
        py_log, gh_log = td / "py.log", td / "gh.log"
        _stub(bin_dir / "python3", f'printf "%s\\n" "$@" >> "{py_log.as_posix()}"\necho "### lookup: **NO_MATCH**"\n')
        _stub(bin_dir / "gh", f'printf "%s\\n" "$*" >> "{gh_log.as_posix()}"\n')
        script = td / "body.sh"
        script.write_text(_step()["run"], encoding="utf-8", newline="\n")
        summary = td / "summary.md"
        summary.touch()
        env = dict(os.environ)
        env.update(
            PATH=f"{bin_dir.as_posix()}{os.pathsep}{env.get('PATH', '')}",
            RUNNER_TEMP=td.as_posix(),
            GITHUB_STEP_SUMMARY=summary.as_posix(),
            GITHUB_SERVER_URL="https://github.com",
            GITHUB_REPOSITORY="FreeForCharity/FFC-Cloudflare-Automation",
            GITHUB_RUN_ID="1",
            GH_TOKEN="fake",
            IN_ORG="",
            IN_DOMAIN="",
            IN_EIN="",
            IN_ISSUE="",
        )
        env.update(env_in)
        p = subprocess.run([_bash(), script.as_posix()], env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        read = lambda f: f.read_text(encoding="utf-8") if f.exists() else ""  # noqa: E731
        return p.returncode, p.stdout + p.stderr, read(py_log), read(gh_log)


# --- shape -------------------------------------------------------------------


def test_no_environment_and_least_permissions():
    wf = load_workflow(WORKFLOW)
    job = wf["jobs"]["lookup"]
    assert "environment" not in job
    assert wf["permissions"] == {"contents": "read", "issues": "write"}


def test_free_text_reaches_the_body_only_through_env():
    step = _step()
    assert "${{" not in step["run"]
    for key in ("IN_ORG", "IN_DOMAIN", "IN_EIN", "IN_ISSUE"):
        assert key in step["env"], key


def test_runs_for_the_same_issue_queue_but_other_lookups_do_not():
    """Same issue: serialized, so a retry cannot post a duplicate comment.
    The group must not be workflow-wide: GitHub keeps one pending run per group
    and cancels the older one, which would drop back-to-back lookups.
    """
    conc = load_workflow(WORKFLOW)["concurrency"]
    assert conc["group"] == "750-charity-lane-lookup-${{ inputs.issue_number || github.run_id }}"
    assert conc["cancel-in-progress"] is False


# --- behaviour ---------------------------------------------------------------


def test_refuses_when_neither_org_nor_domain():
    rc, out, py, gh = run_body(IN_ORG="  ")
    assert rc != 0 and "Give org_name and/or domain" in out
    assert py == "" and gh == ""


def test_refuses_a_non_numeric_issue_number():
    rc, out, py, gh = run_body(IN_ORG="Hope House", IN_ISSUE="12; rm -rf /")
    assert rc != 0 and "issue_number must be digits" in out
    assert py == "" and gh == ""


def test_builds_args_only_from_given_inputs_and_writes_nothing_without_issue():
    rc, out, py, gh = run_body(IN_ORG="Hope House", IN_EIN="12-3456789")
    assert rc == 0, out
    args = py.split("\n")
    assert "--org" in args and "Hope House" in args
    assert "--ein" in args and "12-3456789" in args
    assert "--domain" not in args
    assert gh == "", "no issue number: the job must not write"


def test_posts_one_comment_when_an_issue_is_given():
    rc, out, py, gh = run_body(IN_DOMAIN="hopehouse.org", IN_ISSUE="1618")
    assert rc == 0, out
    calls = [c for c in gh.split("\n") if c]
    assert len(calls) == 1
    assert calls[0].startswith("api repos/FreeForCharity/FFC-Cloudflare-Automation/issues/1618/comments -X POST")


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
