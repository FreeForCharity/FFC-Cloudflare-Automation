"""Tests for workflow 752 (Repo - Merge Queue Backfill), epic #1618.

The plan step is run for real in bash against a fake `gh` (answers from a
fixture) and a fake `jq` (only the two filters the plan uses), so these tests
check which repos it calls ready and why, without the network. The apply job is
pinned structurally: it only creates the shipped ruleset, re-checks before
writing, and confirms the queue reads back.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WF_FILE = "752-repo-merge-queue-backfill.yml"
RULESET = REPO_ROOT / "config" / "merge-queue-ruleset.json"

FAKE_GH = r'''
import json, os, sys
fx = json.load(open(os.environ["FAKE_GH_FIXTURE"], encoding="utf-8"))
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(args) + "\n")
path = args[1] if len(args) > 1 else ""
repo = path.split("/")[2] if path.startswith("repos/") else ""
r = fx.get(repo)
if r is None:
    sys.exit(1)
if "/rules/branches/" in path:
    if r.get("rules_error"):
        sys.exit(1)
    print(sum(1 for t in r.get("rules", []) if t == "merge_queue"))
else:
    print("\t".join([r["visibility"], str(r["archived"]).lower(), str(r["merge_commit"]).lower(), r["branch"]]))
'''

FAKE_JQ = r'''
import json, sys
args = sys.argv[1:]
data = json.loads(sys.stdin.read() or "null")
if args == ["length"]:
    print(len(data)); sys.exit(0)
if args[:1] == ["-c"]:
    named = {}
    i = 1
    while i < len(args) and args[i] == "--arg":
        named[args[i + 1]] = args[i + 2]; i += 3
    assert args[i] == ". + [{repo: $r, branch: $b}]", args
    print(json.dumps(data + [{"repo": named["r"], "branch": named["b"]}], separators=(",", ":"))); sys.exit(0)
sys.exit("fake jq: unsupported " + repr(args))
'''


def _bash() -> str:
    """Git Bash on Windows (a bare `bash` there can be WSL's), else `bash`.
    Same resolver as test_749_org_invite_member.py."""
    if sys.platform == "win32":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if pathlib.Path(candidate).exists():
                return candidate
    return "bash"


def _wf() -> dict:
    return load_workflow(WF_FILE)


def _wf_text() -> str:
    return (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")


def run_plan(targets: str, fixture: dict, dry_run: str = "true") -> tuple[int, str, str, str, str]:
    """Returns (rc, stdout, ready_output, summary, gh_log)."""
    step = find_step(_wf(), "plan", "Plan each repo")
    with tempfile.TemporaryDirectory() as d:
        root = pathlib.Path(d)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        py = sys.executable.replace("\\", "/")
        for name, src in (("gh", FAKE_GH), ("jq", FAKE_JQ)):
            (root / f"{name}.py").write_text(src, encoding="utf-8")
            shim = bin_dir / name
            shim.write_text(f'#!/usr/bin/env bash\nexec "{py}" "{(root / (name + ".py")).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
            shim.chmod(0o755)
        (root / "fixture.json").write_text(json.dumps(fixture), encoding="utf-8")
        for f in ("summary.md", "output.txt", "gh.log"):
            (root / f).write_text("", encoding="utf-8")
        env = {
            **os.environ,
            "ORG": "FreeForCharity",
            "TARGET_REPOS": targets,
            "DRY_RUN": dry_run,
            "GITHUB_STEP_SUMMARY": str(root / "summary.md"),
            "GITHUB_OUTPUT": str(root / "output.txt"),
            "FAKE_GH_FIXTURE": str(root / "fixture.json"),
            "FAKE_GH_LOG": str(root / "gh.log"),
        }
        bin_path = bin_dir.as_posix()
        if sys.platform == "win32" and len(bin_path) > 1 and bin_path[1] == ":":
            # PATH splits on ':', so Git Bash needs /c/... rather than C:/...
            bin_path = "/" + bin_path[0].lower() + bin_path[2:]
        script = f'export PATH="{bin_path}:$PATH"\n' + step["run"]
        proc = subprocess.run([_bash(), "-c", script], capture_output=True, text=True, encoding="utf-8", cwd=d, env=env)
        out = (root / "output.txt").read_text(encoding="utf-8")
        ready = next((l.split("=", 1)[1] for l in out.splitlines() if l.startswith("ready=")), "")
        return (
            proc.returncode,
            proc.stdout + proc.stderr,
            ready,
            (root / "summary.md").read_text(encoding="utf-8"),
            (root / "gh.log").read_text(encoding="utf-8"),
        )


def repo(visibility="public", archived=False, merge_commit=True, branch="main", rules=(), rules_error=False) -> dict:
    return {"visibility": visibility, "archived": archived, "merge_commit": merge_commit, "branch": branch, "rules": list(rules), "rules_error": rules_error}


# --- plan ---------------------------------------------------------------------


def test_a_public_repo_without_a_queue_is_ready():
    rc, out, ready, summary, _ = run_plan("FFC-EX-a.org", {"FFC-EX-a.org": repo(rules=["pull_request"])})
    assert rc == 0, out
    assert json.loads(ready) == [{"repo": "FFC-EX-a.org", "branch": "main"}]
    assert "752-plan: FFC-EX-a.org ready - no merge queue on main" in out
    assert "| FFC-EX-a.org | ready - no merge queue on main |" in summary


def test_a_repo_that_already_has_a_queue_is_left_alone():
    rc, out, ready, _, _ = run_plan("FFC-EX-a.org", {"FFC-EX-a.org": repo(rules=["merge_queue"])})
    assert rc == 0 and json.loads(ready) == []
    assert "752-plan: FFC-EX-a.org current - main already has a merge queue" in out


def test_blocked_cases_are_reported_not_applied():
    fx = {
        "FFC-EX-private.org": repo(visibility="private"),
        "FFC-EX-old.org": repo(archived=True),
        "FFC-EX-squash.org": repo(merge_commit=False),
        "FFC-EX-unread.org": repo(rules_error=True),
    }
    rc, out, ready, _, _ = run_plan(",".join(fx), fx)
    assert rc == 0, out
    assert json.loads(ready) == []
    assert "752-plan: FFC-EX-private.org blocked - visibility is private" in out
    assert "752-plan: FFC-EX-old.org blocked - archived" in out
    assert "752-plan: FFC-EX-squash.org blocked - merge commits are disabled" in out
    assert "752-plan: FFC-EX-unread.org blocked - could not read the rules on main" in out


def test_a_missing_repo_is_blocked_and_the_rest_still_plan():
    rc, out, ready, _, _ = run_plan("FFC-EX-gone.org, FFC-EX-a.org", {"FFC-EX-a.org": repo(branch="master")})
    assert rc == 0, out
    assert "752-plan: FFC-EX-gone.org blocked - repo not found or not readable" in out
    assert json.loads(ready) == [{"repo": "FFC-EX-a.org", "branch": "master"}]


def test_the_rules_read_asks_for_every_rule():
    _, _, _, _, log = run_plan("FFC-EX-a.org", {"FFC-EX-a.org": repo()})
    assert "repos/FreeForCharity/FFC-EX-a.org/rules/branches/main?per_page=100" in log


def test_input_limits():
    rc, out, _, _, log = run_plan("FFC-IN-ffcadmin.org", {})
    assert rc != 0 and "is not an FFC-EX repo name" in out and log == ""
    many = ",".join(f"FFC-EX-r{i}.org" for i in range(11))
    rc, out, _, _, _ = run_plan(many, {})
    assert rc != 0 and "must name 1 to 10 repos" in out


# --- apply and shape ------------------------------------------------------------


def test_the_shipped_ruleset_is_exactly_one_merge_queue_rule():
    rs = json.loads(RULESET.read_text(encoding="utf-8"))["ruleset"]
    assert rs["name"] == "Merge Queue + Branch Up-to-Date"
    assert rs["target"] == "branch" and rs["enforcement"] == "active"
    assert rs["conditions"]["ref_name"]["include"] == ["~DEFAULT_BRANCH"]
    assert [r["type"] for r in rs["rules"]] == ["merge_queue"]
    assert rs["rules"][0]["parameters"]["merge_method"] == "MERGE"


def test_apply_only_creates_and_confirms():
    run = find_step(_wf(), "apply", "Create the merge queue ruleset")["run"]
    recheck = run.index('[.[] | select(.type == "merge_queue")] | length')
    create = run.index('gh api -X POST "repos/${ORG}/${r}/rulesets" --input ruleset.json')
    confirm = run.rindex('[.[] | select(.type == "merge_queue")] | length')
    assert recheck < create < confirm, "re-check before writing, read back after"
    assert "jq '.ruleset' config/merge-queue-ruleset.json > ruleset.json" in run
    for forbidden in ("-X PUT", "-X PATCH", "-X DELETE"):
        assert forbidden not in _wf_text(), forbidden


def test_only_the_apply_job_is_gated_and_can_mint_a_token():
    wf = _wf()
    assert wf["permissions"] == {"contents": "read"}
    assert "environment" not in wf["jobs"]["plan"]
    assert wf["jobs"]["apply"]["environment"] == "github-prod"
    assert wf["jobs"]["apply"]["permissions"]["id-token"] == "write"
    assert "inputs.dry_run == false" in str(wf["jobs"]["apply"]["if"])


def test_inputs_reach_run_bodies_only_through_env():
    wf = _wf()
    for job in wf["jobs"].values():
        for s in job["steps"]:
            assert "${{ inputs." not in s.get("run", ""), s.get("name")


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
