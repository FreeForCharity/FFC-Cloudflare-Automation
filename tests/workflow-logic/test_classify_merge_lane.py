"""Unit tests for scripts/classify-merge-lane.py (AGENTS.md, Merge categories).

The checker decides which PRs Claude may enqueue without Clarke. Its dangerous
failure is a false AGENT: a diff that changes what a credential can do, or that
weakens a guard, reported as safe. So most tests here assert a CLARKE verdict,
and every "could not tell" case (truncated patch, unreadable workflow, short
file list) must come back CLARKE too.

Fixtures are trimmed shapes of `GET pulls/{n}/files` entries, modelled on real
PRs: #1611 (740 logic edit), #1634 (hook that only adds a rule), #1728 (new
whmcs-prod workflow), #1590 (722 permission line), #1733 (sites-list data).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "classify-merge-lane.py"
_spec = importlib.util.spec_from_file_location("_classify_merge_lane", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

classify = _mod.classify
classify_file = _mod.classify_file
write_credentials = _mod.write_credentials
evaluate_pr = _mod.evaluate_pr

READ_ONLY_WORKFLOW = """\
name: '740. Repo - Scheduled Workflow Failure Alert'
# Ambient GITHUB_TOKEN only, no environment: here on purpose
on:
  schedule:
    - cron: '9,39 * * * *'
permissions:
  issues: write
jobs:
  sweep:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/github-script@v8
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          script: |
            core.info('hello')
"""

WRITE_WORKFLOW = """\
name: '701. Website - Provision'
on: workflow_dispatch
jobs:
  repo:
    runs-on: ubuntu-latest
    environment:
      name: github-prod
    steps:
      - run: echo provisioning
"""

READ_ENV_WORKFLOW = """\
name: '801. Candid - Charity Check'
on: workflow_dispatch
jobs:
  check:
    runs-on: ubuntu-latest
    environment: candid-prod-read
    steps:
      - run: echo checking
"""


def _wf(name, patch, status="modified"):
    return {"filename": ".github/workflows/" + name, "status": status, "patch": patch}


LOGIC_PATCH = "@@ -10,3 +10,4 @@\n             core.info('hello')\n+            core.info('duration')\n-            core.info('x')\n+            core.info('y')\n"


def test_a_logic_edit_to_a_read_only_workflow_is_agent():
    lane, cat, reason = classify_file(_wf("740-x.yml", LOGIC_PATCH), READ_ONLY_WORKFLOW)
    assert (lane, cat) == ("AGENT", "workflow"), (lane, cat, reason)


def test_a_logic_edit_to_a_read_environment_workflow_is_agent():
    lane, cat, reason = classify_file(_wf("801-x.yml", LOGIC_PATCH), READ_ENV_WORKFLOW)
    assert lane == "AGENT", (lane, reason)


def test_any_edit_to_a_workflow_holding_a_write_environment_is_clarke():
    lane, _, reason = classify_file(_wf("701-x.yml", LOGIC_PATCH), WRITE_WORKFLOW)
    assert lane == "CLARKE" and "github-prod" in reason, (lane, reason)


def test_a_workflow_reading_a_non_ambient_secret_is_clarke():
    text = READ_ONLY_WORKFLOW.replace("secrets.GITHUB_TOKEN", "secrets.CBM_TOKEN")
    reasons = write_credentials(text)
    assert reasons == ["reads secret CBM_TOKEN"], reasons


def test_a_comment_mentioning_environment_does_not_count_as_a_credential():
    assert write_credentials(READ_ONLY_WORKFLOW) == [], write_credentials(READ_ONLY_WORKFLOW)


def test_an_environment_given_as_an_expression_is_treated_as_write():
    text = READ_ENV_WORKFLOW.replace("candid-prod-read", "${{ inputs.env }}")
    assert write_credentials(text), "a dynamic environment name must not read as read-only"


def test_each_sensitive_line_kind_is_clarke():
    for line in (
        "    permissions:",
        "      contents: write",
        "      id-token: write",
        "    environment: whmcs-prod",
        "      - uses: some/action@v1",
        "on:",
        "  pull_request_target:",
        "    - cron: '0 * * * *'",
        "          GH_TOKEN: ${{ vars.X }}",
        "        continue-on-error: true",
    ):
        for sign in "+-":
            patch = "@@ -1 +1 @@\n%s%s\n" % (sign, line)
            lane, _, reason = classify_file(_wf("740-x.yml", patch), READ_ONLY_WORKFLOW)
            assert lane == "CLARKE", "%r (%s) was not flagged: %s" % (line, sign, reason)


def test_a_commented_sensitive_line_is_not_flagged():
    patch = "@@ -1 +1 @@\n+  # permissions: are unchanged here\n"
    lane, _, reason = classify_file(_wf("740-x.yml", patch), READ_ONLY_WORKFLOW)
    assert lane == "AGENT", reason


def test_new_deleted_and_renamed_workflows_are_clarke():
    for status in ("added", "removed", "renamed"):
        lane, _, reason = classify_file(_wf("235-x.yml", "@@\n+x\n", status), None)
        assert lane == "CLARKE", (status, reason)


def test_a_truncated_workflow_patch_is_clarke_not_agent():
    lane, _, reason = classify_file(_wf("740-x.yml", None), READ_ONLY_WORKFLOW)
    assert lane == "CLARKE" and "too large" in reason, reason


def test_an_unreadable_head_workflow_is_clarke_not_agent():
    lane, _, reason = classify_file(_wf("740-x.yml", LOGIC_PATCH), None)
    assert lane == "CLARKE" and "could not read" in reason, reason


def test_a_guard_workflow_may_gain_lines_but_not_lose_them():
    add = "@@ -1 +1,2 @@\n       - run: a\n+      - run: python3 tests/new_check.py\n"
    lane, _, reason = classify_file(_wf("722-ci.yml", add), READ_ONLY_WORKFLOW)
    assert lane == "AGENT", reason
    drop = "@@ -1,2 +1 @@\n       - run: a\n-      - run: python3 tests/old_check.py\n"
    lane, _, reason = classify_file(_wf("722-ci.yml", drop), READ_ONLY_WORKFLOW)
    assert lane == "CLARKE" and "required-check" in reason, reason


def test_a_hook_change_that_only_adds_lines_is_agent_and_any_deletion_is_clarke():
    f = {"filename": ".claude/hooks/guard_bash.py", "status": "modified",
         "patch": "@@ -1 +1,2 @@\n x = 1\n+y = 2\n"}
    assert classify_file(f)[0] == "AGENT"
    f["patch"] = "@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3\n"
    lane, _, reason = classify_file(f)
    assert lane == "CLARKE" and "deletes" in reason, reason
    f = {"filename": ".githooks/scan_staged.py", "status": "removed", "patch": None}
    assert classify_file(f)[0] == "CLARKE"


def test_reserved_paths_and_rule_files_are_clarke():
    for name in (
        ".github/actions/whmcs-secrets-from-kv/action.yml",
        ".claude/settings.json",
        ".mcp.json",
        "config/charity-lane-gate-policy.json",
        "config/charity-lane-adjudicated-orders.json",
        "scripts/charity_lane_gate.py",
        "scripts/classify-merge-lane.py",
    ):
        lane, _, reason = classify_file({"filename": name, "status": "modified", "patch": "@@\n+x\n"})
        assert lane == "CLARKE", (name, reason)


def test_a_rename_out_of_a_reserved_path_is_still_clarke():
    f = {"filename": "scripts/innocent.py", "previous_filename": "scripts/charity_lane_gate.py",
         "status": "renamed", "patch": ""}
    assert classify_file(f)[0] == "CLARKE"


def test_docs_code_and_data_are_agent_with_their_categories():
    rows = {n: classify_file({"filename": n, "status": "modified", "patch": "@@\n+x\n"})[1]
            for n in ("docs/runbook.md", "AGENTS.md", ".claude/skills/x/SKILL.md",
                      "scripts/x.py", "tests/workflow-logic/test_x.py",
                      "sites-list/sites_list.json", "data/dependabot-affected-repos.json")}
    assert rows == {
        "docs/runbook.md": "docs", "AGENTS.md": "docs", ".claude/skills/x/SKILL.md": "docs",
        "scripts/x.py": "code", "tests/workflow-logic/test_x.py": "code",
        "sites-list/sites_list.json": "data", "data/dependabot-affected-repos.json": "data",
    }, rows


def test_one_clarke_file_makes_the_whole_pr_clarke():
    files = [{"filename": "docs/a.md", "status": "modified", "patch": "@@\n+x\n"},
             _wf("701-x.yml", LOGIC_PATCH)]
    r = classify(files, {".github/workflows/701-x.yml": WRITE_WORKFLOW})
    assert r["lane"] == "CLARKE" and not r["scheduled_ok"], r


def test_only_a_data_only_pr_is_open_to_scheduled_runs():
    data = [{"filename": "sites-list/sites_list.csv", "status": "modified", "patch": "@@\n+x\n"},
            {"filename": "sites-list/sites_list.json", "status": "modified", "patch": "@@\n+x\n"}]
    r = classify(data)
    assert r["lane"] == "AGENT" and r["scheduled_ok"], r
    r = classify(data + [{"filename": "scripts/x.py", "status": "modified", "patch": "@@\n+x\n"}])
    assert r["lane"] == "AGENT" and not r["scheduled_ok"], r


def test_an_empty_file_list_is_clarke():
    assert classify([])["lane"] == "CLARKE"


def test_evaluate_pr_refuses_a_short_file_list():
    """The files endpoint caps out; a list shorter than changed_files must not read as small."""
    def get(path, token, raw=False):
        if path.endswith("/pulls/9"):
            return {"head": {"sha": "a" * 40}, "changed_files": 2}
        if "/files" in path:
            return [{"filename": "docs/a.md", "status": "modified", "patch": "@@\n+x\n"}]
        raise AssertionError("unexpected call %s" % path)
    r = evaluate_pr(9, None, get=get)
    assert r["lane"] == "CLARKE", r


def test_evaluate_pr_reads_the_workflow_at_the_head_sha():
    seen = []
    def get(path, token, raw=False):
        seen.append(path)
        if path.endswith("/pulls/9"):
            return {"head": {"sha": "b" * 40}, "changed_files": 1}
        if "/files" in path:
            return [_wf("740-x.yml", LOGIC_PATCH)]
        if "/contents/" in path:
            assert raw, "workflow content must be read raw"
            return READ_ONLY_WORKFLOW
        raise AssertionError(path)
    r = evaluate_pr(9, None, get=get)
    assert r["lane"] == "AGENT", r
    assert any("ref=" + "b" * 40 in p for p in seen), seen


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:500]}")
    sys.exit(1 if failures else 0)
