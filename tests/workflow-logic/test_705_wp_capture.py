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
            INPUT_IGNORE="",
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


def test_non_numeric_max_items_is_refused():
    """Unvalidated these reach parseInt, where NaN is silently catastrophic:
    a NaN cap paginates zero times and reports an empty site as complete."""
    proc, _ = run_resolve(INPUT_DOMAIN="vpmin.org", INPUT_MAX="lots")
    assert proc.returncode != 0, proc.stdout
    assert "max_items must be a whole number" in proc.stdout, proc.stdout


def test_non_numeric_delay_is_refused():
    proc, _ = run_resolve(INPUT_DOMAIN="vpmin.org", INPUT_DELAY="fast")
    assert proc.returncode != 0, proc.stdout
    assert "delay_ms must be a whole number" in proc.stdout, proc.stdout


def test_absurd_delay_is_refused():
    proc, _ = run_resolve(INPUT_DOMAIN="vpmin.org", INPUT_DELAY="999999")
    assert proc.returncode != 0, proc.stdout
    assert "delay_ms must be a whole number" in proc.stdout, proc.stdout


def test_zero_delay_is_allowed_but_max_zero_is_not():
    """0ms is impolite yet occasionally deliberate; a 0 cap is never meaningful."""
    ok, outputs = run_resolve(INPUT_DOMAIN="vpmin.org", INPUT_DELAY="0")
    assert ok.returncode == 0, ok.stdout
    assert "delay=0" in outputs, outputs
    bad, _ = run_resolve(INPUT_DOMAIN="vpmin.org", INPUT_MAX="0")
    assert bad.returncode != 0, bad.stdout


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



def test_ignore_hosts_are_lowercased_and_normalized():
    """The list is a DROP list: a reference to one of these hosts is not
    fetched and does not count against the capture's asset gate."""
    proc, outputs = run_resolve(
        INPUT_DOMAIN="viewpointministriesinternational.org", INPUT_IGNORE="Drop.Example"
    )
    assert proc.returncode == 0, proc.stdout
    assert "ignore_hosts=drop.example" in outputs, outputs


def test_ignore_hosts_reject_a_comma_typo():
    """`a,org` looks like one hostname with a typo but parses as TWO entries
    that match nothing. A list that matches nothing fails silently — the
    capture goes on reporting the very failures the list was added to drop,
    which reads as "the fix did not work" rather than "the input was wrong"."""
    proc, _ = run_resolve(INPUT_DOMAIN="vpmi.org", INPUT_IGNORE="a,org")
    assert proc.returncode != 0, proc.stdout
    assert "is not a bare hostname" in proc.stdout, proc.stdout


def test_ignore_hosts_tolerate_trailing_comma_and_dedupe():
    proc, outputs = run_resolve(
        INPUT_DOMAIN="vpmi.org", INPUT_IGNORE="a.org,,www.a.org,b.org,"
    )
    assert proc.returncode == 0, proc.stdout
    assert "ignore_hosts=a.org,b.org" in outputs, outputs


def test_ignore_hosts_accept_multiple_hosts():
    proc, outputs = run_resolve(INPUT_DOMAIN="vpmi.org", INPUT_IGNORE="a.org,b.example.com")
    assert proc.returncode == 0, proc.stdout
    assert "ignore_hosts=a.org,b.example.com" in outputs, outputs


def test_ignore_hosts_reject_a_pasted_url():
    proc, _ = run_resolve(INPUT_DOMAIN="vpmi.org", INPUT_IGNORE="https://a.org/")
    assert proc.returncode != 0, proc.stdout
    assert "is not a bare hostname" in proc.stdout, proc.stdout


def test_ignore_hosts_default_to_empty():
    proc, outputs = run_resolve(INPUT_DOMAIN="vpmi.org")
    assert proc.returncode == 0, proc.stdout
    assert "ignore_hosts=" in outputs, outputs


def test_capture_passes_the_ignore_list_through_to_the_script():
    """The resolve job can validate perfectly and still be inert if the capture
    job never forwards the value."""
    run = step_run(WORKFLOW, "capture", "Capture the site")
    assert "--ignore-hosts" in run, run
    assert "$IGNORE_HOSTS" in run, run


# --- whitespace must be rejected or separate, never deleted ------------------
#
# 706 was fixed for this first and 705 was left with the identical code — the
# partial fix is the thing worth guarding against, so both now carry the tests.


def test_whitespace_separates_ignore_hosts_rather_than_vanishing():
    """`a.org b.org` must become two validated entries. Deleting the space
    yields the single host `a.orgb.org`, which PASSES the hostname pattern,
    matches nothing during the capture, and so produces exactly the silent
    no-op that this input's per-entry validation exists to prevent."""
    proc, outputs = run_resolve(INPUT_DOMAIN="vpmi.org", INPUT_IGNORE="a.org b.org")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ignore_hosts=a.org,b.org" in outputs, outputs
    assert "a.orgb.org" not in outputs, (
        "whitespace was deleted rather than treated as a separator:\n" + outputs
    )


def test_surrounding_whitespace_in_the_domain_is_trimmed():
    proc, outputs = run_resolve(INPUT_DOMAIN="  example.org\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "domain=example.org" in outputs, outputs


def test_internal_whitespace_in_the_domain_still_fails_closed():
    proc, outputs = run_resolve(INPUT_DOMAIN="example.org attacker.com")
    assert proc.returncode != 0, f"accepted a two-token domain:\n{outputs}"
    assert "example.orgattacker.com" not in outputs, outputs


def _self_test(script: str) -> subprocess.CompletedProcess:
    """Run a shipped script's own offline self-test suite."""
    import os

    return subprocess.run(
        ["node", str(REPO_ROOT / "scripts" / script), "--self-test"],
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_the_capture_strips_cdn_injected_instrumentation():
    """Every page of the first delivery failed the self-containment gate on a
    same-origin request to `/cdn-cgi/rum?`. Nothing in the captured HTML
    referenced it — the capture's asset inventory never saw the URL — because
    Cloudflare's beacon fabricates it at runtime. Only removing the beacon tag
    stops the request, and `/cdn-cgi/*` is answered by the edge, so no capture
    could mirror it either.

    Asserted against the shipped suite's OUTPUT rather than restated here: this
    checks the coverage still exists, which a copy of the assertions could not.
    """
    proc = _self_test("capture-wordpress-api.mjs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    for case in (
        "the Cloudflare beacon script is removed",
        "a same-origin /cdn-cgi/ script is removed",
        "the site's own scripts survive",
    ):
        assert f"ok   {case}" in out, f"coverage for {case!r} is gone:\n{out[-1500:]}"


def test_the_capture_decodes_obfuscated_addresses_before_dropping_the_decoder():
    """Cloudflare rewrites `mailto:` into `/cdn-cgi/l/email-protection#<hex>`
    and ships the decoder from `/cdn-cgi/`, which a static host cannot serve.
    Removing that script without decoding first would leave every obfuscated
    address on a charity's contact page rendered as hex — so the decode is what
    makes the strip safe, not a separate nicety."""
    proc = _self_test("capture-wordpress-api.mjs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    for case in (
        "an obfuscated address span decodes to the real address",
        "an email-protection href becomes a real mailto",
        "a decode that does not yield an address leaves the markup untouched",
    ):
        assert f"ok   {case}" in out, f"coverage for {case!r} is gone:\n{out[-1500:]}"


def test_the_capture_refuses_a_page_that_redirected_off_the_site():
    """Delivery attempt 6 shipped `public/index.html` as a parked WordPress.com
    landing page for a domain the operator had explicitly excluded, while the
    other 588 pages were correct.

    `redirect: 'follow'` means a 200 says nothing about whose page came back.
    The source's WordPress declares `home` as a domain it does not serve, so it
    answered its own root with a canonical redirect off-site and the capture
    stored the stranger's page under the charity's URL. Every gate was green:
    the page loaded, its assets resolved, and nothing about a 200 is suspicious.

    A gap is visible; a substitution is not. Hence refuse, and say so.
    """
    proc = _self_test("capture-wordpress-api.mjs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    for case in (
        "isSiteHost REFUSES the stale home host \u2014 the parked-page case",
        "classifyPageResponse REFUSES a 200 that redirected to the stale home host",
        "classifyPageResponse skips a non-200 without calling it off-site",
    ):
        assert f"ok   {case}" in out, f"coverage for {case!r} is gone:\n{out[-1500:]}"


def test_the_capture_brings_navigation_home_and_gates_on_what_it_missed():
    """562 of 589 delivered pages navigated to the decommissioned domain.

    The rewrite compared an entry's link against the page's raw href STRINGS,
    and those are written in different alphabets whenever a site declares a
    stale `home`: entries are normalized onto the serving host, the markup
    still says the stale one. `present.has(e.link)` was false for every link on
    the site, so not one was rewritten.

    No gate could see it. The self-containment gate loads each page with the
    source blocked, which exercises subresources; a nav href is fetched only on
    a click, so a clone whose entire menu points off-site passes cleanly. The
    front page is gated separately for the same reason a percentage cannot
    express it \u2014 losing it costs 1 of 590 and every visitor sees it first.
    """
    proc = _self_test("capture-wordpress-api.mjs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    for case in (
        "normalizedLinkIndex resolves a STALE-host href onto the entry key",
        "the old string comparison would have matched nothing",
        "normalizedLinkIndex folds absolute, root-absolute and relative spellings together",
        "captureVerdict fails a clone whose navigation still points at the stale host",
        "captureVerdict does NOT fail on links to the serving domain \u2014 /feed/ has no local copy",
        "captureVerdict names the front page as its own problem, not just a count",
        "captureVerdict fails on a missing front page even when nothing else is wrong",
    ):
        assert f"ok   {case}" in out, f"coverage for {case!r} is gone:\n{out[-1500:]}"


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
