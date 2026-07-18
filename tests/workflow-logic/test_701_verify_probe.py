"""Unit tests for the 701 verify job's serving probe (bash, fake curl).

Regression anchor: HTTP header lines end in CR; the original parse kept it
in the status code ("200\\r" != "200"), which would have reported every
live site as not serving. The fake curl emits real CRLF headers so this
stays pinned.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"


def run_probe(env_overrides: dict) -> tuple[subprocess.CompletedProcess, str, str]:
    script = step_run("701-website-provision.yml", "verify", "Probe the live site")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        summary = tdp / "summary.md"
        outputs = tdp / "output.txt"
        summary.touch()
        outputs.touch()
        env = {
            # Harness first: fake curl (CRLF headers) and no-op sleep.
            "PATH": f"{HARNESS_DIR}:/usr/bin:/bin",
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_OUTPUT": str(outputs),
            "HOME": str(tdp),
            "DOMAIN": "example.org",
        }
        env.update(env_overrides)
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return proc, summary.read_text(), outputs.read_text()


def test_serving_site_detected_despite_crlf_headers():
    proc, summary, outputs = run_probe({"TEST_APEX_CODE": "200", "TEST_APEX_SERVER": "GitHub.com"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "served=true" in outputs, outputs
    assert "served_host=example.org" in outputs, outputs
    assert "Live on GitHub Pages" in summary, summary


def test_non_github_server_reports_not_serving():
    proc, summary, outputs = run_probe({"TEST_APEX_CODE": "200", "TEST_APEX_SERVER": "cloudflare"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "served=false" in outputs, outputs
    assert "Not serving yet" in summary, summary


def test_non_200_reports_not_serving():
    proc, _, outputs = run_probe({"TEST_APEX_CODE": "404", "TEST_APEX_SERVER": "GitHub.com"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "served=false" in outputs, outputs


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
