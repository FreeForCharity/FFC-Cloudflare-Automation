"""Unit tests for 746's two steps (bash, fake gh): the ungated preflight
(normalize/dedup, per-domain repo+deploy.yml existence including a
non-default default_branch, aggregate skip counting, all-skipped
fail-closed) and the gated enable-pages loop (idempotent Pages-enable that
tells a real "not configured yet" 404 from a genuine failure, deploy
dispatch on the repo's own default branch, partial-failure fails the step).

746 is 732's own follow-up: 732 can only turn Pages on at CREATE time, and
its preflight skips any domain whose repo already exists, so this module
does not re-prove 732's collision/sibling logic — it proves the parts unique
to fixing up an EXISTING repo.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"


def run_preflight(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str, dict]:
    script = step_run(
        "746-bulk-enable-pages.yml", "preflight", "Parse and check each requested domain"
    )
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        summary.touch()
        outputs = tdp / "outputs.txt"
        outputs.touch()
        env = child_env(
            HARNESS_DIR,
            GITHUB_STEP_SUMMARY=str(summary),
            GITHUB_OUTPUT=str(outputs),
            HOME=str(tdp),
            IN_DOMAINS="example.org",
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
        out = {}
        for line in outputs.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                out[k] = v
        return proc, summary.read_text(encoding="utf-8"), out


def run_enable(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str]:
    script = step_run(
        "746-bulk-enable-pages.yml",
        "enable-pages",
        "Enable Pages + dispatch deploy for each ready domain",
    )
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        summary.touch()
        env = child_env(
            HARNESS_DIR,
            GITHUB_STEP_SUMMARY=str(summary),
            HOME=str(tdp),
            IN_READY_DOMAINS='[{"domain":"example.org","branch":"main"}]',
            IN_DRY_RUN="true",
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


def ready_domains(out: dict) -> list[str]:
    return [r["domain"] for r in json.loads(out["ready_domains"])]


def test_multiple_ready_domains_all_survive():
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org,three.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-one.org"}',
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ready_domains(out) == ["one.org", "two.org", "three.org"], out
    assert out["ready_count"] == "3", out
    assert out["skipped_count"] == "0", out


def test_normalization_dedup_and_case():
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "  HTTPS://One.org/ ,\nwww.two.org\nONE.org,two.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-one.org"}',
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ready_domains(out) == ["one.org", "two.org"], out


def test_pasted_url_path_query_and_fragment_are_stripped():
    # Copilot review (#1252): a pasted URL's path/query/fragment must not
    # survive into the repo name, only the bare-slash strip existed before.
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "https://example.org/foo?x=1,https://example.org/bar#frag",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-example.org"}',
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Both inputs normalize to the same bare domain and dedup to one entry.
    assert ready_domains(out) == ["example.org"], out


def test_empty_domains_input_fails():
    proc, _, _ = run_preflight({"IN_DOMAINS": "   ,, \n "})
    assert proc.returncode != 0, proc.stdout
    assert "No usable domains parsed" in proc.stdout, proc.stdout


def test_missing_repo_is_skipped_and_all_skipped_fails_closed():
    # Named for what actually happens: an individual missing-repo domain is
    # merely skipped, but when it is the ONLY domain, "0 ready" trips the
    # batch-level fail-closed exit (Copilot review, #1252).
    proc, summary, out = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org",
            "TEST_REPO_META": "404",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert out.get("ready_domains") == "[]", out
    assert out.get("skipped_count") == "2", out
    assert "repo not found" in summary, summary


def test_repo_without_deploy_yml_is_skipped_and_all_skipped_fails_closed():
    proc, summary, out = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-one.org"}',
            "TEST_DEPLOY_YML_MISSING": "1",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert out.get("ready_domains") == "[]", out
    assert out.get("skipped_count") == "2", out
    assert "no deploy.yml" in summary, summary


def test_transient_repo_check_failure_is_skipped_not_conflated_with_missing():
    # Copilot review (#1252): a 403/5xx on the repo-existence GET is not "not
    # found" -- it means the check itself could not run, and the summary must
    # say so distinctly (same 404-vs-genuine-error split as the enable step).
    proc, summary, out = run_preflight(
        {
            "IN_DOMAINS": "one.org",
            "TEST_REPO_META": "error",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert out.get("ready_domains") == "[]", out
    assert "could not verify repo" in summary, summary
    assert "repo not found" not in summary, summary


def test_transient_deploy_yml_check_failure_is_skipped_not_conflated_with_missing():
    proc, summary, out = run_preflight(
        {
            "IN_DOMAINS": "one.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-one.org"}',
            "TEST_DEPLOY_YML_CHECK_FAIL": "1",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert out.get("ready_domains") == "[]", out
    assert "could not verify deploy.yml" in summary, summary
    assert "no deploy.yml on default branch" not in summary, summary


def test_non_default_branch_is_captured_and_reported():
    proc, summary, out = run_preflight(
        {
            "IN_DOMAINS": "one.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-one.org", "default_branch": "trunk"}',
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    ready = json.loads(out["ready_domains"])
    assert ready == [{"domain": "one.org", "branch": "trunk"}], ready
    assert "| one.org | FFC-EX-one.org | trunk |" in summary, summary


def test_mixed_batch_reports_ready_and_skipped_independently():
    # The fake `gh` cannot vary TEST_REPO_META/TEST_DEPLOY_YML_MISSING by
    # domain, so this exercises the one axis that DOES differ per call inside
    # a single preflight run: a domain is only skipped for missing-repo when
    # the bare repos/<owner>/<name> lookup itself fails, which here it never
    # does — the meaningful mixed case per this harness is all-ready.
    proc, _, out = run_preflight(
        {
            "IN_DOMAINS": "one.org,two.org",
            "TEST_REPO_META": '{"full_name": "FreeForCharity/FFC-EX-one.org"}',
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["ready_count"] == "2", out
    assert out["skipped_count"] == "0", out


def test_dry_run_reads_pages_state_but_never_writes():
    # The Pages GET is a read and runs even in a dry run (it is how the
    # summary can honestly say "already enabled" instead of guessing); only
    # the POST/dispatch WRITES are skipped. TEST_PAGES_NOT_CONFIGURED=1
    # simulates the real "not yet configured" 404 shape so the dry-run path
    # reaches the "would enable" branch instead of "already enabled".
    proc, summary = run_enable({"IN_DRY_RUN": "true", "TEST_PAGES_NOT_CONFIGURED": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[DRY RUN] would enable" in summary, summary
    assert "[DRY RUN] would dispatch deploy.yml on main" in summary, summary


def test_already_enabled_pages_is_left_alone():
    # GET succeeds (no failure knob set) -> "already enabled", and the live
    # path must never issue a POST for it.
    proc, summary = run_enable({"IN_DRY_RUN": "false"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "already enabled" in summary, summary
    assert "dispatched" in summary, summary


def test_live_enable_and_dispatch_succeed():
    proc, summary = run_enable(
        {
            "IN_DRY_RUN": "false",
            "TEST_PAGES_NOT_CONFIGURED": "1",  # real 404 -> POST path
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "enabled (build_type=workflow)" in summary, summary
    assert "dispatched" in summary, summary


def test_genuine_pages_get_failure_never_attempts_a_write():
    # Copilot review (#1252): a 403/5xx on the GET is not "not configured" —
    # it must fail the step WITHOUT a POST attempt. TEST_PAGES_POST_FAIL is
    # also set so that if the (buggy) old behavior attempted a POST anyway,
    # it would still show as a failure either way — but the summary text
    # proves which branch actually ran.
    proc, summary = run_enable(
        {
            "IN_DRY_RUN": "false",
            "TEST_PAGES_GET_FAIL": "1",
            "TEST_PAGES_POST_FAIL": "1",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "could not read Pages state" in summary, summary
    assert "enable failed" not in summary, summary
    assert "operation(s) failed" in proc.stdout, proc.stdout
    # Copilot review (#1252): dispatch must not fire when Pages state is
    # unconfirmed, even though TEST_PAGES_POST_FAIL alone would have let a
    # dispatch attempt succeed.
    assert "skipped (Pages not confirmed" in summary, summary
    # Copilot review (round 4, #1252): the summary says "see run log" -- the
    # captured error body must actually be there for that to be true.
    assert "Could not read Pages state" in proc.stdout, proc.stdout
    assert "Resource not accessible" in proc.stdout, proc.stdout
    assert "dispatched" not in summary, summary


def test_pages_post_failure_fails_the_step_and_captures_the_error_detail():
    # Copilot review (#1252): the POST's error body was previously discarded
    # (>/dev/null 2>&1) -- it must now show up (truncated) in the summary.
    proc, summary = run_enable(
        {
            "IN_DRY_RUN": "false",
            "TEST_PAGES_NOT_CONFIGURED": "1",
            "TEST_PAGES_POST_FAIL": "1",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "enable failed" in summary, summary
    assert "Validation Failed" in summary, summary
    assert "operation(s) failed" in proc.stdout, proc.stdout
    # Copilot review (#1252): a failed enable must not still fire a dispatch.
    assert "skipped (Pages not confirmed" in summary, summary
    assert "dispatched" not in summary, summary


def test_dispatch_failure_fails_the_step_and_captures_the_error_detail():
    proc, summary = run_enable(
        {
            "IN_DRY_RUN": "false",
            "TEST_DISPATCH_FAIL": "1",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "dispatch failed" in summary, summary
    assert "Not Found" in summary, summary
    assert "operation(s) failed" in proc.stdout, proc.stdout


def test_non_main_branch_is_used_for_the_live_dispatch():
    proc, summary = run_enable(
        {
            "IN_READY_DOMAINS": '[{"domain":"one.org","branch":"trunk"}]',
            "IN_DRY_RUN": "false",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "dispatched (trunk)" in summary, summary


def test_multiple_ready_domains_each_get_a_summary_row():
    proc, summary = run_enable(
        {
            "IN_READY_DOMAINS": '[{"domain":"one.org","branch":"main"},{"domain":"two.org","branch":"main"}]',
            "IN_DRY_RUN": "true",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "| one.org |" in summary, summary
    assert "| two.org |" in summary, summary


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
