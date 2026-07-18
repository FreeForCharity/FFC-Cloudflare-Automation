"""Unit tests for the 702 clone-deploy preflight (bash, fake gh + fake curl).

Regression anchors from the 2026-07-18 incident: a clone was dispatched
against FFC-EX-AllTypeTowing.com — an already-completed migration cut over
on its apex — because a stale inventory and a wrong-case Pages probe passed
for verification. The preflight must: require the repo to exist, resolve
canonical casing via the API, and refuse to re-clone a live site without
force=true.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"

CANONICAL_META = '{"full_name": "FreeForCharity/FFC-EX-AllTypeTowing.com", "archived": false}'


def run_preflight(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str, str]:
    """Run the preflight step. Returns (proc, summary, outputs)."""
    script = step_run("702-ffc-ex-clone-deploy.yml", "preflight", "Verify target state")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        outputs = tdp / "output.txt"
        summary.touch()
        outputs.touch()
        env = {
            "PATH": f"{HARNESS_DIR}:/usr/bin:/bin",
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(outputs),
            "HOME": str(tdp),
            "TARGET_ORG": "FreeForCharity",
            "IN_DOMAIN": "alltypetowing.com",
            "IN_REPO": "",
            "IN_FORCE": "false",
        }
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return proc, summary.read_text(), outputs.read_text()


def test_missing_repo_fails_fast_with_create_guidance():
    proc, _, _ = run_preflight({"TEST_REPO_META": "404"})
    assert proc.returncode != 0, proc.stdout
    assert "does not exist" in proc.stdout and "720" in proc.stdout, proc.stdout


def test_unparseable_repo_response_fails_safe():
    # Fake gh's default *repos/* response is a non-JSON settings string.
    proc, _, _ = run_preflight({})
    assert proc.returncode != 0, proc.stdout
    assert "unparseable" in proc.stdout.lower() or "Failing safe" in proc.stdout, proc.stdout


def test_live_on_default_url_refused_without_force():
    proc, summary, _ = run_preflight(
        {"TEST_REPO_META": CANONICAL_META, "TEST_PAGES_CODE": "200", "TEST_APEX_SERVER": "cloudflare"}
    )
    assert proc.returncode != 0, proc.stdout
    assert "already serves a live site" in proc.stdout, proc.stdout
    assert "force=true" in proc.stdout, proc.stdout
    assert "Refused" in summary, summary


def test_cutover_apex_refused_without_force():
    # Default URL 404 (custom-domain builds move to root paths) but apex serves
    # from GitHub Pages — the AllTypeTowing shape exactly.
    proc, _, _ = run_preflight(
        {
            "TEST_REPO_META": CANONICAL_META,
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_CODE": "200",
            "TEST_APEX_SERVER": "GitHub.com",
        }
    )
    assert proc.returncode != 0, proc.stdout
    assert "already serves a live site" in proc.stdout, proc.stdout


def test_force_overrides_live_refusal_and_emits_canonical_name():
    proc, _, outputs = run_preflight(
        {
            "TEST_REPO_META": CANONICAL_META,
            "TEST_PAGES_CODE": "200",
            "TEST_APEX_CODE": "200",
            "TEST_APEX_SERVER": "GitHub.com",
            "IN_FORCE": "true",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=FFC-EX-AllTypeTowing.com" in outputs, outputs


def test_not_yet_live_proceeds_with_canonical_casing():
    # Lower-case input resolves to the canonical CamelCase name via the API —
    # github.io paths are case-sensitive, so downstream must use this value.
    proc, _, outputs = run_preflight(
        {
            "TEST_REPO_META": CANONICAL_META,
            "TEST_PAGES_CODE": "404",
            "TEST_APEX_CODE": "200",
            "TEST_APEX_SERVER": "cloudflare",
            "IN_REPO": "ffc-ex-alltypetowing.com",
        }
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo_name=FFC-EX-AllTypeTowing.com" in outputs, outputs


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
