"""Unit tests for workflow 751 (Website - Security Overrides Sync).

751 rewrites `package.json` in charity repos, so the tests pin the ways that can
go wrong quietly, each one seen on the manual run that preceded it (#1640):

  * the wrong template: two Footer-Only sites were first given the Single Page
    block, so detection must refuse a repo that matches neither banner;
  * a dead scoped override (`nanoid@<3.3.18` next to a bare `nanoid` pin),
    which Copilot flagged on three PRs;
  * a CRLF `package.json`: reading the indent with `\\s+` swallowed the line
    break and wrote a blank line after every key, and the result still parsed.

They also pin the workflow's shape: the plan job holds no environment and no
token while it runs a charity's own build, and nothing in the file merges.

Run: python3 tests/workflow-logic/test_751_security_overrides_sync.py
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import find_step, load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "scripts" / "fleet-overrides-sync.js"
WF_FILE = "751-website-security-overrides-sync.yml"

TEMPLATE = {
    "dependencies": {"next": "^16.3.8", "postcss": "^8.5.28"},
    "pnpm": {
        "overrides": {
            "nanoid": "^3.3.18",
            "postcss": "$postcss",
            "source-map-js": "^1.2.2",
            "sharp": "^0.35.5",
        }
    },
}


def node(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(CLI), *args], capture_output=True, text=True, encoding="utf-8"
    )


def merge(target: dict, raw: str | None = None) -> tuple[dict, list[str], str]:
    with tempfile.TemporaryDirectory() as d:
        tpl = pathlib.Path(d, "template.json")
        tgt = pathlib.Path(d, "package.json")
        tpl.write_text(json.dumps(TEMPLATE), encoding="utf-8")
        text = raw if raw is not None else json.dumps(target, indent=2) + "\n"
        tgt.write_bytes(text.encode("utf-8"))
        r = node("merge", str(tpl), str(tgt))
        assert r.returncode == 0, r.stderr
        written = tgt.read_bytes().decode("utf-8")
        return json.loads(written), json.loads(r.stdout)["changes"], written


def detect(source: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as d:
        f = pathlib.Path(d, "check-drift.mjs")
        f.write_text(source, encoding="utf-8")
        return node("detect", str(f))


# --- detect -------------------------------------------------------------------


def test_detect_footer_only_banner():
    r = detect("#!/usr/bin/env node\n/**\n * FFC footer-only drift guard.\n */\n")
    assert r.returncode == 0 and r.stdout.strip() == "footer", r


def test_detect_single_page_banner():
    r = detect("/**\n * FFC drift guard — runs in CI and as a pre-commit hook.\n */\n")
    assert r.returncode == 0 and r.stdout.strip() == "single", r


def test_detect_refuses_a_repo_matching_neither_banner():
    r = detect("/** some other drift script */\n")
    assert r.returncode == 2 and not r.stdout.strip(), r


def test_detect_refuses_a_file_carrying_both_banners():
    r = detect("FFC footer-only drift guard.\nFFC drift guard — runs in CI\n")
    assert r.returncode == 2, r


# --- merge --------------------------------------------------------------------


def test_adds_missing_overrides_and_template_value_wins():
    pkg, changes, _ = merge(
        {"dependencies": {"next": "^16.3.8"}, "pnpm": {"overrides": {"sharp": "^0.35.4"}}}
    )
    ov = pkg["pnpm"]["overrides"]
    assert ov["source-map-js"] == "^1.2.2"
    assert ov["sharp"] == "^0.35.5"
    assert any("source-map-js" in c for c in changes)


def test_keeps_repo_only_override_keys():
    pkg, _, _ = merge({"dependencies": {}, "pnpm": {"overrides": {"qs": "^6.16.0"}}})
    assert pkg["pnpm"]["overrides"]["qs"] == "^6.16.0"


def test_drops_a_scoped_override_a_bare_pin_makes_dead():
    pkg, changes, _ = merge(
        {"pnpm": {"overrides": {"nanoid@<3.3.18": "^3.3.18", "postcss@<8.5.18": "^8.5.18"}}}
    )
    ov = pkg["pnpm"]["overrides"]
    assert "nanoid@<3.3.18" not in ov and "postcss@<8.5.18" not in ov
    assert sum("dropped" in c for c in changes) == 2


def test_keeps_a_scoped_override_when_the_template_has_no_bare_pin():
    pkg, _, _ = merge({"pnpm": {"overrides": {"brace-expansion@<2": "^1.1.21"}}})
    assert pkg["pnpm"]["overrides"]["brace-expansion@<2"] == "^1.1.21"


def test_scoped_package_names_are_not_read_as_scoped_overrides():
    pkg, _, _ = merge({"pnpm": {"overrides": {"@humanfs/node": "^0.16.8"}}})
    assert pkg["pnpm"]["overrides"]["@humanfs/node"] == "^0.16.8"


def test_raises_next_and_postcss_to_the_template_floor():
    pkg, changes, _ = merge({"dependencies": {"next": "^16.2.11", "postcss": "^8.5.22"}})
    assert pkg["dependencies"] == {"next": "^16.3.8", "postcss": "^8.5.28"}
    assert "next ^16.2.11 -> ^16.3.8" in changes


def test_never_lowers_a_dependency_already_above_the_floor():
    pkg, changes, _ = merge({"dependencies": {"next": "^16.4.0"}})
    assert pkg["dependencies"]["next"] == "^16.4.0"
    assert not any(c.startswith("next ") for c in changes)


def test_up_to_date_repo_reports_no_changes_and_file_is_untouched():
    current = json.loads(json.dumps(TEMPLATE))
    raw = json.dumps(current, indent=4) + "\n"
    _, changes, written = merge(current, raw)
    assert changes == [] and written == raw


def test_crlf_file_keeps_crlf_and_gains_no_blank_lines():
    raw = '{\r\n  "name": "x",\r\n  "dependencies": {\r\n    "next": "^16.2.11"\r\n  }\r\n}\r\n'
    _, _, written = merge({}, raw)
    assert "\r\n\r\n" not in written, "a blank line was written after a key"
    assert re.search(r"(?<!\r)\n", written) is None, "bare LF mixed into a CRLF file"
    assert written.startswith('{\r\n  "name": "x",\r\n'), written[:40]


def test_tab_indent_is_preserved():
    raw = '{\n\t"dependencies": {\n\t\t"next": "^16.2.11"\n\t}\n}\n'
    _, _, written = merge({}, raw)
    assert '\n\t"dependencies"' in written


def test_unparseable_package_json_is_blocked_not_rewritten():
    with tempfile.TemporaryDirectory() as d:
        tpl = pathlib.Path(d, "t.json")
        tgt = pathlib.Path(d, "p.json")
        tpl.write_text(json.dumps(TEMPLATE), encoding="utf-8")
        tgt.write_text("{ not json", encoding="utf-8")
        r = node("merge", str(tpl), str(tgt))
        assert r.returncode == 2, r
        assert tgt.read_text(encoding="utf-8") == "{ not json"


# --- workflow wiring ----------------------------------------------------------


def _wf() -> dict:
    return load_workflow(WF_FILE)


def _wf_text() -> str:
    return (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")


def test_dispatch_inputs_and_dry_run_default():
    on = _wf().get("on") or _wf().get(True)
    inputs = on["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"target_repos", "dry_run"}
    assert inputs["dry_run"]["default"] is True
    assert inputs["target_repos"]["required"] is True


def test_plan_job_is_ungated_and_apply_job_is_github_prod_only_on_live_runs():
    jobs = _wf()["jobs"]
    assert "environment" not in jobs["plan"]
    assert jobs["apply"]["environment"] == "github-prod"
    assert "inputs.dry_run == false" in jobs["apply"]["if"]
    assert jobs["apply"]["needs"] == "plan"


def test_charity_build_step_has_no_token_in_its_environment():
    step = find_step(_wf(), "plan", "Plan each repo")
    env = step.get("env") or {}
    assert not any("token" in str(v).lower() or "secret" in str(v).lower() for v in env.values())
    checkout = find_step(_wf(), "plan", "Checkout")
    assert checkout["with"]["persist-credentials"] is False


def test_only_the_apply_job_can_mint_an_oidc_token():
    wf = _wf()
    assert "id-token" not in (wf.get("permissions") or {})
    assert "id-token" not in (wf["jobs"]["plan"].get("permissions") or {})
    assert wf["jobs"]["apply"]["permissions"]["id-token"] == "write"


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


def _shell_fn(run: str, name: str) -> str:
    m = re.search(rf"^( *){name}\(\) \{{\n.*?^\1\}}\n", run, re.M | re.S)
    assert m, f"{name}() not found"
    return m.group(0)


def _call(fn_src: str, call: str) -> tuple[str, str]:
    """Run one helper from the workflow in bash; return (log, step summary).
    jq is stubbed: these tests are about where the result line goes."""
    with tempfile.TemporaryDirectory() as tmp:
        summary = pathlib.Path(tmp) / "summary.md"
        (pathlib.Path(tmp) / "out").mkdir()
        script = f"jq() {{ echo '[]'; }}\n{fn_src}\n{call}\n"
        proc = subprocess.run(
            [_bash(), "-c", script],
            capture_output=True, text=True, encoding="utf-8", cwd=tmp,
            env={**os.environ, "GITHUB_STEP_SUMMARY": str(summary)},
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout, summary.read_text(encoding="utf-8")


def test_plan_results_reach_the_log_not_only_the_summary():
    """The conductor reads job logs; GitHub has no API for step summaries (#1644)."""
    run = find_step(_wf(), "plan", "Plan each repo")["run"]
    log, summary = _call(_shell_fn(run, "record"),
                         "record FFC-EX-a.org - blocked - - 0 'drift guard matches neither template'")
    assert "751-plan: FFC-EX-a.org blocked — drift guard matches neither template" in log
    assert "| FFC-EX-a.org | - | blocked — drift guard matches neither template |" in summary


def test_apply_results_reach_the_log_not_only_the_summary():
    run = find_step(_wf(), "apply", "Open one PR")["run"]
    log, summary = _call(_shell_fn(run, "row"),
                         'row FFC-EX-a.org "🆕 PR opened" https://github.com/FreeForCharity/FFC-EX-a.org/pull/7')
    assert "751-apply: FFC-EX-a.org 🆕 PR opened https://github.com/FreeForCharity/FFC-EX-a.org/pull/7" in log
    assert "| FFC-EX-a.org | 🆕 PR opened | https://github.com/FreeForCharity/FFC-EX-a.org/pull/7 |" in summary


def test_every_apply_result_goes_through_row():
    """A result written straight to the summary would be invisible to the conductor."""
    run = find_step(_wf(), "apply", "Open one PR")["run"]
    body = run.replace(_shell_fn(run, "row"), "")
    assert '| $name |' not in body
    assert body.count('row "$name"') == 8


def test_apply_fetches_an_existing_sync_branch_before_the_lease_push():
    run = find_step(_wf(), "apply", "Open one PR")["run"]
    fetch = run.index('git fetch -q --depth 1 origin "+refs/heads/${BRANCH}:refs/remotes/origin/${BRANCH}"')
    push = run.index('git push -q -u origin "$BRANCH" --force-with-lease')
    assert fetch < push


def test_nothing_in_the_workflow_merges_or_approves():
    text = _wf_text()
    for forbidden in ("gh pr merge", "/merge", "--auto", "--admin", "pulls/reviews", "enablePullRequestAutoMerge"):
        assert forbidden not in text, forbidden


def test_only_the_owned_paths_are_allowed_in_both_jobs():
    text = _wf_text()
    guard = r"'^(package\.json|pnpm-lock\.yaml|types/[A-Za-z0-9._-]+\.d\.ts)$'"
    assert text.count(guard) == 2, "plan and apply must both check the staged paths"


def test_target_repo_names_are_validated_to_ffc_ex():
    text = _wf_text()
    assert text.count("^FFC-EX-[A-Za-z0-9.-]{3,100}$") == 2


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
