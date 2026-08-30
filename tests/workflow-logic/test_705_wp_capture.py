"""Unit tests for 705's input-resolution step and the capture script's own logic.

Two things are pinned here.

The workflow step validates the ONE value that reaches a network call — the
domain — before the script ever runs. That guard exists because the natural
way to type this input is wrong: an operator reading "domain" pastes
`https://vpmin.org/`, and a URL threaded into the REST candidates produces
`https://https://vpmin.org//wp-json/`, which fails as an unhelpful DNS error
several hundred lines into a run. It also refuses shell metacharacters, since
the value is interpolated into a shell variable that is later passed to node.

The script's own pure logic has its own offline suite (`--self-test`), which
runs in the `resolve` job that `capture` depends on; this module asserts that
the suite exists, passes, and gates every live request — so a run cannot
exercise broken classification logic against a real charity's site and produce
a confident, wrong artifact.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env, load_workflow, step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = "705-website-wordpress-capture.yml"


def run_resolve(**env_overrides: str) -> tuple[subprocess.CompletedProcess, str]:
    """Run the 'Resolve inputs' step. Returns (proc, GITHUB_OUTPUT contents)."""
    script = step_run(WORKFLOW, "resolve", "Resolve inputs")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        outputs = tdp / "output.txt"
        outputs.touch()
        env = child_env(
            HARNESS_DIR,
            GITHUB_OUTPUT=str(outputs),
            HOME=str(tdp),
            INPUT_DOMAIN="",
            INPUT_MODE="",
            INPUT_MAX="",
            INPUT_DELAY="",
            INPUT_POSTS="",
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
        return proc, outputs.read_text(encoding="utf-8")


def test_bare_domain_is_accepted_and_echoed():
    proc, outputs = run_resolve(INPUT_DOMAIN="vpmin.org", INPUT_MODE="inspect")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "domain=vpmin.org" in outputs, outputs
    assert "mode=inspect" in outputs, outputs


def test_empty_domain_refuses_rather_than_guessing():
    """There is no safe default: a substituted domain would point a live
    capture at a site nobody asked about."""
    proc, _ = run_resolve(INPUT_DOMAIN="")
    assert proc.returncode != 0, proc.stdout
    assert "Refusing to guess" in proc.stdout, proc.stdout


def test_pasted_url_is_refused_with_a_specific_message():
    # The mistake this guard exists for: "domain" invites a pasted URL.
    proc, _ = run_resolve(INPUT_DOMAIN="https://vpmin.org/")
    assert proc.returncode != 0, proc.stdout
    assert "bare hostname" in proc.stdout, proc.stdout


def test_www_prefix_is_stripped_by_the_step_and_by_the_script():
    """`www.` is accepted and stripped. It must be stripped HERE as well as in
    the script, because the concurrency group keys on this step's output: if
    the step emitted `www.x.org` while the script captured `x.org`, the two
    forms would crawl the same origin concurrently."""
    proc, outputs = run_resolve(INPUT_DOMAIN="www.vpmin.org")
    assert proc.returncode == 0, proc.stdout
    assert "domain=vpmin.org" in outputs, outputs

    normalized = subprocess.run(
        [
            "node",
            "-e",
            "import('./scripts/capture-wordpress-api.mjs')"
            ".then(m => console.log(m.normalizeDomain('www.vpmin.org')))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        env=child_env(),
        timeout=60,
    )
    assert normalized.stdout.strip() == "vpmin.org", normalized.stdout + normalized.stderr


def test_uppercase_domain_is_accepted_and_lowercased():
    """Hostnames are case-insensitive and the script lowercases anyway, so
    refusing `VpMin.org` would be a gratuitous refusal of a correct value."""
    proc, outputs = run_resolve(INPUT_DOMAIN="ViewPointMinistriesInternational.ORG")
    assert proc.returncode == 0, proc.stdout
    assert "domain=viewpointministriesinternational.org" in outputs, outputs


def test_shell_metacharacters_are_refused():
    proc, _ = run_resolve(INPUT_DOMAIN="vpmin.org$(id)")
    assert proc.returncode != 0, proc.stdout
    assert "bare hostname" in proc.stdout, proc.stdout
    # The injected command must not have run.
    assert "uid=" not in proc.stdout, proc.stdout


def test_path_suffix_is_refused():
    proc, _ = run_resolve(INPUT_DOMAIN="vpmin.org/about")
    assert proc.returncode != 0, proc.stdout
    assert "bare hostname" in proc.stdout, proc.stdout


def test_unknown_mode_is_refused():
    proc, _ = run_resolve(INPUT_DOMAIN="vpmin.org", INPUT_MODE="download-everything")
    assert proc.returncode != 0, proc.stdout
    assert "mode must be" in proc.stdout, proc.stdout


def test_mode_defaults_to_inspect_when_nothing_is_set():
    # The safe default matters: `inspect` makes a handful of probes, `capture`
    # walks the whole site. An empty input must never fall through to the
    # heavier one.
    proc, outputs = run_resolve(INPUT_DOMAIN="vpmin.org")
    assert proc.returncode == 0, proc.stdout
    assert "mode=inspect" in outputs, outputs


def test_numeric_defaults_are_applied_when_inputs_are_empty():
    proc, outputs = run_resolve(INPUT_DOMAIN="vpmin.org")
    assert proc.returncode == 0, proc.stdout
    assert "max=500" in outputs, outputs
    assert "delay=250" in outputs, outputs
    assert "posts=false" in outputs, outputs


def test_capture_script_self_test_passes():
    proc = subprocess.run(
        ["node", str(REPO_ROOT / "scripts" / "capture-wordpress-api.mjs"), "--self-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO_ROOT),
        env=child_env(),
        timeout=120,
    )
    # Assert on the output, not only the exit code: --self-test exits 2 for a
    # failed assertion AND for a crash, so a script that cannot start would
    # otherwise be indistinguishable from one whose checks caught something.
    assert "all self-tests passed" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_self_test_gates_every_live_request():
    """The offline suite must gate the live steps, not merely accompany them.

    It lives in `resolve`, and `capture` — which holds every network call —
    declares `needs: resolve`, so a self-test failure stops the run before any
    request reaches the charity's server."""
    wf = load_workflow(WORKFLOW)
    resolve_names = [s.get("name", "") for s in wf["jobs"]["resolve"]["steps"]]
    selftest_at = next(i for i, n in enumerate(resolve_names) if "Self-test" in n)
    assert "if" not in wf["jobs"]["resolve"]["steps"][selftest_at], "self-test must not be skippable"

    needs = wf["jobs"]["capture"].get("needs")
    needs = [needs] if isinstance(needs, str) else needs
    assert "resolve" in needs, needs

    # No live request may sit in the resolve job alongside the self-test.
    assert not any("Inspect the live" in n or n == "Capture the site" for n in resolve_names), resolve_names
    capture_names = [s.get("name", "") for s in wf["jobs"]["capture"]["steps"]]
    inspect_at = next(i for i, n in enumerate(capture_names) if "Inspect the live" in n)
    capture_at = next(i for i, n in enumerate(capture_names) if n == "Capture the site")
    assert inspect_at < capture_at, capture_names


def test_workflow_is_read_only_and_ungated():
    """705 loads no credentials and touches nothing outside the run artifact."""
    wf = load_workflow(WORKFLOW)
    assert wf["permissions"] == {"contents": "read"}, wf["permissions"]
    for name, job in wf["jobs"].items():
        assert "environment" not in job, (name, job.get("environment"))
        for step in job["steps"]:
            assert "secrets." not in str(step.get("env", "")), (name, step)
            assert "azure/login" not in str(step.get("uses", "")), (name, step)


def test_capture_mode_is_gated_on_the_resolved_mode():
    """`capture` must never run off an unvalidated raw input."""
    wf = load_workflow(WORKFLOW)
    step = next(s for s in wf["jobs"]["capture"]["steps"] if s.get("name") == "Capture the site")
    cond = step["if"]
    assert "needs.resolve.outputs.mode" in cond, cond
    assert "inputs.mode" not in cond, cond


def test_concurrency_group_keys_on_the_normalized_domain():
    """Keying on the raw input would let `VPMin.org` and `vpmin.org` crawl the
    same origin at once — exactly what the group exists to prevent."""
    wf = load_workflow(WORKFLOW)
    group = wf["jobs"]["capture"]["concurrency"]["group"]
    assert "needs.resolve.outputs.domain" in group, group
    assert "inputs.domain" not in group, group
    assert wf["jobs"]["capture"]["concurrency"]["cancel-in-progress"] is False
    # A workflow-level group would reintroduce the raw input.
    assert "concurrency" not in wf, wf.get("concurrency")


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
