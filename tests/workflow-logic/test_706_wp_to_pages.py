"""Unit tests for 706's input-resolution step and its job wiring.

706 is the only workflow in the repo that captures a live third-party site AND
writes the result into another repository, so three properties are pinned here
rather than left to review.

1. Every input that becomes a network call, a filesystem path, or published
   markup is validated in `resolve` before any of it is used. The natural way
   to type several of these is wrong in a way that fails late and unhelpfully.

2. The offline self-tests of every script the workflow runs gate every later
   job. A
   classification regression must stop the run before it touches a real
   charity's website, not after.

3. Only `deliver` is gated, and only `deliver` writes. If the gate ever drifts
   onto `convert`, a read-only dry conversion starts requiring a human; if it
   ever drifts OFF `deliver`, an unapproved run opens a PR on another repo.
   Both are silent until someone notices.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import (
    WORKFLOWS,
    child_env,
    find_step,
    forward_slashes,
    load_workflow,
    step_run,
)

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = "706-website-wordpress-to-pages.yml"


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
            INPUT_DOMAIN="example.org",
            INPUT_REPO="FFC-EX-example.org",
            INPUT_EMAIL="",
            INPUT_MODE="",
            INPUT_MAX="",
            INPUT_DELAY="",
            INPUT_POSTS="",
            INPUT_IGNORE="",
            INPUT_PUBLISH="",
            INPUT_MINPCT="",
            # A real workflow_dispatch always SETS every input to its
            # default, so the empty string is the production shape —
            # and setting them here means an inherited INPUT_REUSE_RUN
            # from the surrounding shell cannot quietly turn these
            # tests into tests of a reuse dispatch.
            INPUT_REUSE_RUN="",
            INPUT_REUSE_MAX_AGE="",
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


# --- domain -----------------------------------------------------------------


def test_pasted_url_is_normalized_not_refused():
    """An operator pastes what the browser shows. Threaded into the REST
    candidates unchanged it becomes `https://https://example.org//wp-json/`,
    which fails several hundred lines later as a DNS error naming nothing
    useful."""
    proc, outputs = run_resolve(INPUT_DOMAIN="https://WWW.Example.org/about/")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "domain=example.org" in outputs, outputs


def test_empty_domain_refuses_rather_than_guessing():
    proc, _ = run_resolve(INPUT_DOMAIN="")
    assert proc.returncode != 0, proc.stdout
    assert "domain is required" in proc.stdout, proc.stdout


def test_shell_metacharacters_in_domain_are_refused():
    proc, _ = run_resolve(INPUT_DOMAIN="example.org;curl evil.test")
    assert proc.returncode != 0, proc.stdout
    assert "not a bare hostname" in proc.stdout, proc.stdout


def test_bare_hostname_without_a_dot_is_refused():
    proc, _ = run_resolve(INPUT_DOMAIN="localhost")
    assert proc.returncode != 0, proc.stdout


# --- target repo ------------------------------------------------------------


def test_target_repo_is_required():
    """Not derivable from `domain`: the destination repo is routinely named for
    a NEW short domain that serves nothing, so deriving either from the other
    picks the wrong one — which is exactly how a green run can capture the
    wrong website."""
    proc, _ = run_resolve(INPUT_REPO="")
    assert proc.returncode != 0, proc.stdout
    assert "target_repo is required" in proc.stdout, proc.stdout


def test_target_repo_accepts_an_owner_prefix_and_a_url():
    proc, outputs = run_resolve(INPUT_REPO="https://github.com/FreeForCharity/FFC-EX-a.org")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo=FFC-EX-a.org" in outputs, outputs


def test_target_repo_refuses_a_path_traversal():
    """The value is interpolated into a checkout `path:` and a `gh --repo`
    argument."""
    proc, _ = run_resolve(INPUT_REPO="../../etc")
    assert proc.returncode != 0, proc.stdout
    assert "not a bare repository name" in proc.stdout, proc.stdout


def test_target_repo_refuses_a_foreign_owner():
    """`someone-else/repo` must not survive: the CBM PAT is org-wide, so a
    delivery to an unintended owner would actually succeed."""
    proc, _ = run_resolve(INPUT_REPO="attacker/evil-repo")
    assert proc.returncode != 0, proc.stdout
    assert "not a bare repository name" in proc.stdout, proc.stdout


# --- contact email ----------------------------------------------------------


def test_contact_email_is_validated_when_supplied():
    """This becomes the charity's only contact channel in published markup. A
    typo does not degrade the page — it silently ends every conversation, and
    nothing downstream would ever notice."""
    proc, _ = run_resolve(INPUT_EMAIL="info@example")
    assert proc.returncode != 0, proc.stdout
    assert "not a plausible address" in proc.stdout, proc.stdout


def test_contact_email_accepts_a_normal_address():
    proc, outputs = run_resolve(INPUT_EMAIL="info@viewpointministriesinternational.org")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "email=info@viewpointministriesinternational.org" in outputs, outputs


def test_contact_email_may_be_empty():
    """Only sites that actually have forms need one; the convert job counts the
    forms first and fails there if it finds any without an address."""
    proc, outputs = run_resolve(INPUT_EMAIL="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "email=" in outputs, outputs


# --- mode and numeric inputs ------------------------------------------------


def test_mode_defaults_to_convert():
    """The default must be the non-writing mode: a mis-set default here means
    a run someone launched to LOOK at a site opens a PR on another repo."""
    proc, outputs = run_resolve(INPUT_MODE="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mode=convert" in outputs, outputs


def test_unknown_mode_is_refused():
    proc, _ = run_resolve(INPUT_MODE="publish")
    assert proc.returncode != 0, proc.stdout
    assert "is not one of" in proc.stdout, proc.stdout


def test_non_numeric_max_items_is_refused():
    """parseInt('abc') is NaN and parseInt('12abc') is 12; both reach a fetch
    loop as a silently wrong bound rather than as an error."""
    proc, _ = run_resolve(INPUT_MAX="12abc")
    assert proc.returncode != 0, proc.stdout
    assert "must be a whole number" in proc.stdout, proc.stdout


def test_zero_max_items_is_refused():
    proc, _ = run_resolve(INPUT_MAX="0")
    assert proc.returncode != 0, proc.stdout


def test_absurd_delay_is_refused():
    """A delay large enough to outlive the job timeout produces a run that
    fails on time rather than on anything diagnostic."""
    proc, _ = run_resolve(INPUT_DELAY="999999")
    assert proc.returncode != 0, proc.stdout


def test_numeric_defaults_are_applied():
    proc, outputs = run_resolve(INPUT_MAX="", INPUT_DELAY="", INPUT_POSTS="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "max=800" in outputs, outputs
    assert "delay=250" in outputs, outputs
    assert "posts=true" in outputs, outputs


# --- ignore hosts -----------------------------------------------------------


def test_ignore_hosts_reject_a_comma_typo():
    """`a,org` parses as two entries that match nothing. An ignore list that
    matches nothing fails silently: the run goes on reporting the failures the
    list was added to drop, which reads as "the fix did not work"."""
    proc, _ = run_resolve(INPUT_IGNORE="a,org")
    assert proc.returncode != 0, proc.stdout
    assert "not a bare hostname" in proc.stdout, proc.stdout


def test_ignore_hosts_normalize_and_dedupe():
    proc, outputs = run_resolve(INPUT_IGNORE="A.org,,www.a.org,b.org,")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ignore_hosts=a.org,b.org" in outputs, outputs


# --- extra_hosts: folding a charity's subdomains into one repo ---------------


def test_extra_hosts_is_optional_and_defaults_to_no_mounts():
    """A single-hostname site must be completely unaffected. Asserted on the
    OUTPUT rather than the exit code: an unset input that aborted the step under
    `set -u` would also be caught by every other test here, but a silently empty
    mount list that still reported success would not."""
    proc, outputs = run_resolve()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mount_count=0" in outputs, outputs
    assert "mounts=\n" in outputs or outputs.rstrip().endswith("mounts="), outputs


def test_extra_hosts_parses_into_host_equals_mount_pairs():
    proc, outputs = run_resolve(
        INPUT_EXTRA_HOSTS="school.example.org => /school\npublications.example.org => publications"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "mounts=school.example.org=school publications.example.org=publications" in outputs, outputs
    assert "mount_count=2" in outputs, outputs


def test_extra_hosts_refuses_the_apex_domain():
    """Mounting the apex would capture the same site twice, the second time into
    a subdirectory, and the completeness gate would pass for both."""
    proc, _ = run_resolve(INPUT_EXTRA_HOSTS="example.org => /main")
    assert proc.returncode != 0, proc.stdout
    assert "apex domain" in proc.stdout + proc.stderr, proc.stdout + proc.stderr


def test_extra_hosts_refuses_an_empty_mount():
    """An empty mount silently merges a subdomain INTO the apex's routes, where
    it collides slug for slug — the expensive failure this validation exists
    for, and the one that looks like a successful run."""
    proc, _ = run_resolve(INPUT_EXTRA_HOSTS="school.example.org => /")
    assert proc.returncode != 0, proc.stdout
    assert "collide" in proc.stdout + proc.stderr, proc.stdout + proc.stderr


def test_extra_hosts_refuses_a_mount_with_a_space():
    """`resolve` flattens the parser's output with a whitespace-delimited awk,
    so a mount containing a space is TRUNCATED at that space rather than
    rejected — the charity's pages land at a URL nobody typed and every gate in
    the run still passes. Asserted end to end through the step, not just in the
    parser's own self-test, because the truncation lives in the step."""
    proc, outputs = run_resolve(INPUT_EXTRA_HOSTS="school.example.org => /school catalog")
    assert proc.returncode != 0, proc.stdout
    both = proc.stdout + proc.stderr
    assert "kebab-case" in both, both
    # The failure mode this guards against, stated as an assertion: the step
    # must not have emitted the truncated mount as if it were the real one.
    assert "school.example.org=school " not in outputs, outputs


def test_extra_hosts_refuses_overlapping_mounts():
    proc, _ = run_resolve(
        INPUT_EXTRA_HOSTS="a.example.org => /x\nb.example.org => /x/y"
    )
    assert proc.returncode != 0, proc.stdout
    assert "overlaps" in proc.stdout + proc.stderr, proc.stdout + proc.stderr


def test_extra_hosts_validation_happens_before_any_network_work():
    """`resolve` reaches no live site. Validating here is what keeps a typo from
    costing a 15-minute crawl AND a human approval before it is noticed."""
    resolve_job = load_workflow(WORKFLOW)["jobs"]["resolve"]
    assert "environment" not in resolve_job, resolve_job
    script = step_run(WORKFLOW, "resolve", "Resolve inputs")
    assert "parse-host-mounts.mjs" in script, script


def test_the_capture_step_mounts_each_extra_host():
    """Each host is captured with its own `--mount`, and gated on its OWN
    completeness report: an aggregate across hosts would let a 40% capture of a
    110-page subdomain hide behind a complete apex."""
    run = step_run(WORKFLOW, "convert", "Capture the live WordPress site")
    assert "--mount" in run, run
    assert "capture_one" in run, run
    # The apex is captured unmounted, at the root.
    assert 'capture_one "$DOMAIN" "" "apex"' in run, run
    # Every host runs the same assessment, inside the function.
    assert run.count("assess-capture-completeness.mjs") == 1, run


def test_convert_has_time_to_finish_a_polite_multi_host_crawl():
    """`timeout-minutes: 90` cut a healthy crawl in half. Measured on run
    35659542248, three hostnames at delay_ms=2000: apex 40 min (1 of 1),
    school 36 min (111 of 111), then the axe fell 14 minutes into
    publications. Nothing was wrong — the run simply ran out of clock.

    The failure mode is what makes this worth a test rather than a bigger
    number. A `timeout-minutes` expiry reports as **cancelled**, not failed,
    so it looks like a human pressed the button; it took a full re-read of
    the log to establish the crawl had been healthy throughout.

    And the slowness is deliberate. Crawling this charity's apex at the 250ms
    default knocked its two subdomains offline twice, so `delay_ms` has to
    stay high — the timeout must accommodate the politeness, not cap it.

    Bounded at both ends on purpose, and the lower bound is 240 rather than
    the ~3h projection because only two of the three hosts have been timed.
    apex (40 min) and school (36 min) are measured, so 76 minutes of the
    total is known. publications is the one that has never finished at this
    delay, and it is the largest by every axis that costs time: 246 pages
    against school's 111, ~500 MB of EdGuide PDFs to fetch, and Ghostscript
    now actually processing them at max_pdf_mb=20. Its plausible range runs
    to ~150 minutes, which puts the three-host total near 226 — so a bound
    set at the projection itself would sit *below* outcomes this run can
    legitimately produce, and would fail a good crawl exactly as 90 did.
    240 is the projection plus the margin the unmeasured term deserves;
    re-measure publications and this can tighten.

    The upper bound is GitHub's 360-minute hard cap for hosted runners: at
    or above it the value stops being a backstop against a stuck job at
    all, because the platform kills the job first either way."""
    convert = load_workflow(WORKFLOW)["jobs"]["convert"]
    timeout = convert.get("timeout-minutes")
    assert isinstance(timeout, int), convert
    assert timeout >= 240, timeout
    assert timeout < 360, timeout


def test_ghostscript_is_installed_before_the_capture_that_needs_it():
    """#1348 shipped the PDF downsampling pass assuming `gs` was on the runner.
    Run 35634361425 measured that it is not:

        [asset] ghostscript (gs) is not installed, so oversized PDFs ship as
        captured. Install it before the capture step to downsample them.
        [capture] 4 oversized PDF(s) shipped as captured because ghostscript
        was not available. This is NOT a judgement that they were already
        optimal: nothing tried.

    The pass degraded exactly as it was built to — it said so, and the
    publishable-size gate then refused the tree and named all four files. That
    is the good failure mode, and it still bought nothing: those four are
    507.7 MB of a charity's own magazines that no `git push` will take.

    Ordering is the assertion. An install placed after the capture is a
    no-op that looks like a fix, and nothing in the run's output would say so
    — the capture would go on reporting `pdfsSkippedNoEncoder` while a green
    `gs --version` scrolled past underneath it."""
    convert = load_workflow(WORKFLOW)["jobs"]["convert"]["steps"]
    names = [s.get("name", "") for s in convert]
    gs = [i for i, n in enumerate(names) if "Ghostscript" in n]
    capture = names.index("Capture the live WordPress site")
    assert gs, names
    assert gs[0] < capture, (gs, capture, names)

    run = convert[gs[0]]["run"]
    assert "apt-get install" in run and "ghostscript" in run, run
    # `apt-get update` first: the runner image ships no package lists, so a
    # bare install 404s on every mirror path.
    assert run.index("apt-get update") < run.index("apt-get install"), run
    # And prove the binary exists rather than trusting apt's exit code — a
    # renamed or dropped package then fails HERE, in seconds, instead of
    # 20 minutes later as one line in a capture log.
    assert "gs --version" in run, run


def test_each_host_starts_from_no_report_so_a_stale_one_cannot_be_assessed():
    """Every host writes the SAME report path, so it must be cleared before
    each capture — otherwise a host that dies before writing one is assessed
    against the PREVIOUS host's report, and passes.

    Measured, run 35622301582: school.newheightseducation.org became
    unreachable, its capture aborted at `REST API is not usable` before
    writing anything, and the apex's report from twelve minutes earlier was
    still on disk. The gate read that and reported `Captured 1 of 1 inventory
    entries (100.0%)` for a host that fetched nothing at all — byte-identical
    to the apex's summary, down to the apex's own asset-failure counts and its
    `www.newheightseducation.org` unlocalized host. Had the third host not
    hard-failed, the run would have delivered a site missing all 110 of that
    subdomain's pages while reporting three green hosts.

    The `[ ! -f "$report" ]` branch was written for exactly this case and
    could not fire, because the file existed. A guard that cannot observe the
    state it guards is not a weaker guard, it is an absent one.

    Ordering carries the whole assertion. An `rm -f` placed after the capture
    would delete the very report the gate is about to read, turning a silent
    false pass into a loud false failure — the opposite defect, equally wrong."""
    run = step_run(WORKFLOW, "convert", "Capture the live WordPress site")
    assert 'rm -f "$report"' in run, run
    removed = run.index('rm -f "$report"')
    captured = run.index('node scripts/capture-wordpress-api.mjs "${args[@]}" || rc=$?')
    assessed = run.index("assess-capture-completeness.mjs")
    assert removed < captured < assessed, (removed, captured, assessed)
    # ...and INSIDE capture_one, so it runs once per host rather than once for
    # the whole step. Cleared only at the top, the second host inherits the
    # first host's report exactly as before.
    body = run.split("capture_one() {", 1)[1].split("\n}", 1)[0]
    assert 'rm -f "$report"' in body, body


def test_the_per_host_report_is_labelled_by_HOST_not_by_MOUNT():
    """`label` is interpolated into a filename. A mount is a URL path and may
    legally nest (`/school/spring-2026`), so labelling by mount turns the `cp`
    destination into a path whose directory does not exist: the capture
    succeeds and the run dies copying the report it was meant to preserve —
    after the 15-minute crawl, which is the most expensive place in this
    pipeline to lose. A hostname cannot contain a `/` (isHostname)."""
    run = step_run(WORKFLOW, "convert", "Capture the live WordPress site")
    assert 'capture_one "$host" "$mount" "$host"' in run, run
    assert 'capture_one "$host" "$mount" "$mount"' not in run, run
    # And the reason the assertion above matters: the label reaches a filename.
    assert 'wp-capture-report.${label}.json' in run, run


# --- error handling: the step's declared mode must stay its mode -----------


def test_no_step_toggles_errexit_mid_script():
    """`set +e … set -e` does not restore — it ASSERTS. In a step that declares
    `set -uo pipefail` (no errexit, deliberately, so every failure reports a
    named ::error:: instead of dying silently at whichever line failed first),
    the pair turns errexit ON for the remainder of the step. Measured: off
    before the first call, ON after it.

    Two of 706's steps had it, in both cases in a loop, so the mode flipped on
    the first iteration and stayed flipped. `cmd || rc=$?` captures the same
    status and touches no option — verified identical under both modes."""
    wf = load_workflow(WORKFLOW)
    offenders = []
    for job_id, job in wf["jobs"].items():
        for step in job.get("steps", []) or []:
            run = step.get("run") or ""
            for i, line in enumerate(run.splitlines(), 1):
                if line.strip() in ("set +e", "set -e"):
                    offenders.append(f"{job_id}/{step.get('name', '?')}:{i} {line.strip()}")
    assert not offenders, offenders


def test_the_capture_captures_its_exit_code_without_disabling_errexit():
    run = step_run(WORKFLOW, "convert", "Capture the live WordPress site")
    assert 'node scripts/capture-wordpress-api.mjs "${args[@]}" || rc=$?' in run, run
    assert "local rc=0" in run, run


def test_the_per_host_report_copy_is_guarded_explicitly():
    """errexit never covered this `cp`, even while it was (accidentally) on:
    `capture_one` is always invoked as `capture_one … || exit 1`, and a tested
    context suspends errexit inside the function body too. Measured: a failing
    untested `cp` let the function return 0.

    It is not a cosmetic file. `verify-reused-capture.mjs` reads the per-host
    reports to decide which hosts a reused capture may publish and where, so a
    silently missing one turns into a refusal — or worse, a wrong publish — in
    a later run that has no way to know why."""
    run = step_run(WORKFLOW, "convert", "Capture the live WordPress site")
    assert 'wp-capture-report.${label}.json" || {' in run, run
    # And the guard must actually stop the host, not just narrate.
    # Taken line by line to the closing brace. `split("}")` is wrong here and
    # fails in the flattering direction: the guard's own message contains
    # `${label}`, so it cuts mid-string and reports the guard as empty.
    tail = run.split('wp-capture-report.${label}.json" || {', 1)[1].splitlines()
    body = []
    for line in tail:
        if line.strip() == "}":
            break
        body.append(line)
    else:
        raise AssertionError("the report-copy guard has no closing brace")
    copy_guard = "\n".join(body)
    assert "::error::" in copy_guard, copy_guard
    assert "return 1" in copy_guard, copy_guard


# --- reuse_capture_from_run: not crawling a charity twice for one result ----


def test_reuse_is_off_by_default():
    """Every existing dispatch must be completely unaffected. Asserted on the
    OUTPUT, because an input that silently defaulted to something truthy would
    skip the crawl and publish an artifact from a run nobody named."""
    proc, outputs = run_resolve()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "reuse_run=\n" in outputs or outputs.rstrip().endswith("reuse_run="), outputs


def test_reuse_run_id_must_be_numeric():
    """A run id reaches `gh run download` and an artifact name. Refused here,
    in the job that reaches no network, so a typo costs seconds rather than a
    job that has already installed a native module."""
    proc, _ = run_resolve(INPUT_REUSE_RUN="not-a-run")
    assert proc.returncode != 0, proc.stdout
    assert "numeric run id" in proc.stdout + proc.stderr, proc.stdout + proc.stderr


def test_a_numeric_reuse_run_id_is_published_for_the_convert_job():
    proc, outputs = run_resolve(INPUT_REUSE_RUN=" 35571249633 ")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "reuse_run=35571249633" in outputs, outputs


def test_reuse_max_age_defaults_and_refuses_junk():
    """The age limit is the only check standing between a reused capture and a
    site that has changed since, so it may not silently fall back to 'no limit'
    when it is mistyped."""
    proc, outputs = run_resolve()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "reuse_max_age_hours=168" in outputs, outputs
    for bad in ("0", "-5", "lots"):
        proc, _ = run_resolve(INPUT_REUSE_MAX_AGE=bad)
        assert proc.returncode != 0, (bad, proc.stdout)
        assert "reuse_max_age_hours" in proc.stdout + proc.stderr, proc.stdout + proc.stderr


def test_the_crawl_and_the_reuse_are_exact_complements():
    """The load-bearing property. If both steps could run, the crawl would
    overwrite the very capture the reuse was meant to preserve and the run
    would still report success; if neither could, the job would proceed with an
    empty capture directory. Asserted as literal, opposite conditions on the
    same expression rather than as 'both mention reuse_run'."""
    convert = load_workflow(WORKFLOW)["jobs"]["convert"]
    by_name = {s.get("name", ""): s for s in convert["steps"]}
    crawl = by_name["Capture the live WordPress site"]
    reuse = by_name["Reuse the capture from an earlier run"]
    assert crawl["if"] == "needs.resolve.outputs.reuse_run == ''", crawl["if"]
    assert reuse["if"] == "needs.resolve.outputs.reuse_run != ''", reuse["if"]


def test_convert_keeps_contents_read_when_it_gains_actions_read():
    """A job-level `permissions` block REPLACES the workflow-level one rather
    than extending it, so adding `actions: read` for the artifact download
    without restating `contents: read` breaks the checkout — which reads as a
    checkout problem, not as a permissions one."""
    convert = load_workflow(WORKFLOW)["jobs"]["convert"]
    assert convert["permissions"]["actions"] == "read", convert["permissions"]
    assert convert["permissions"]["contents"] == "read", convert["permissions"]


def test_reuse_refuses_a_run_of_a_different_workflow():
    """A run id from another workflow would otherwise fail later at 'no
    artifact named wp-capture-<id>', which reads as an expired artifact rather
    than as the wrong run. The literal path is asserted against this file's own
    name so a rename cannot leave the check pointing at nothing."""
    run = step_run(WORKFLOW, "convert", "Reuse the capture from an earlier run")
    assert f"self='.github/workflows/{WORKFLOW}'" in run, run


def test_reuse_reapplies_this_runs_completeness_threshold():
    """Inheriting the source run's gate would mean `min_capture_percent` stops
    meaning anything the moment a capture is reused: a capture that passed at
    90% would satisfy a dispatch asking for 98%."""
    run = step_run(WORKFLOW, "convert", "Reuse the capture from an earlier run")
    assert "assess-capture-completeness.mjs" in run, run
    assert '--min-percent "$MIN_PERCENT"' in run, run


def test_reuse_counts_what_it_assessed_against_what_it_expected():
    """A per-item success log is not evidence of completeness: a file with no
    trailing newline loses its last line to `read`, and every line that DID run
    prints a pass. The count is the only thing that can see the missing one."""
    run = step_run(WORKFLOW, "convert", "Reuse the capture from an earlier run")
    assert "assessed=$((assessed + 1))" in run, run
    assert "expected=$((MOUNT_COUNT + 1))" in run, run
    assert '[ "$assessed" -ne "$expected" ]' in run, run


def test_reuse_verifies_the_artifact_before_anything_reads_it():
    """Domain, host set, mounts and age are checked against THIS dispatch. Every
    later gate in this workflow asks whether the tree is a coherent site, and
    none asks whether it is the site that was asked for."""
    run = step_run(WORKFLOW, "convert", "Reuse the capture from an earlier run")
    assert "verify-reused-capture.mjs" in run, run
    assert '--domain "$DOMAIN"' in run, run
    assert '--mounts "$MOUNTS"' in run, run
    assert '--max-age-hours "$MAX_AGE"' in run, run


def test_reuse_reads_the_verifier_exit_code_without_a_pipe():
    """Ledger L50. The verifier's whole contract is its exit code; reading it
    through a pipe reports the reader's status and turns a refusal into a pass."""
    run = step_run(WORKFLOW, "convert", "Reuse the capture from an earlier run")
    assert 'rc=$?' in run, run
    assert 'verify-reused-capture.mjs' in run, run
    # The verifier's stdout goes to a FILE, never into another command.
    assert '> "$reports"' in run, run
    assert "verify-reused-capture.mjs |" not in run.replace("\n", " "), run


def test_a_reused_capture_is_re_uploaded_under_THIS_runs_id():
    """`deliver` downloads `wp-capture-<this run's id>`. The upload step is
    therefore unconditional: make it skip on a reused capture and the handoff
    breaks for exactly the dispatch capture reuse exists to serve — a retried
    `deliver` — and it breaks AFTER the approval has been spent."""
    convert = load_workflow(WORKFLOW)["jobs"]["convert"]
    upload = next(
        s for s in convert["steps"] if "Upload the neutralized capture" in s.get("name", "")
    )
    assert upload["with"]["name"] == "wp-capture-${{ github.run_id }}", upload["with"]
    # It may be conditioned on a capture EXISTING, never on how this run was
    # dispatched: skipping the upload on a reused capture breaks the handoff
    # for the retried `deliver` that capture reuse exists to serve.
    assert "reuse_run" not in (upload.get("if") or ""), upload.get("if")


def test_the_capture_is_uploaded_even_when_a_LATER_step_fails():
    """The measured cost of getting this wrong, on run 35571249633: three
    hostnames crawled for 36m32s, all three past their completeness gates, then
    `Convert the capture into real app routes` failed — and because the upload
    sat after it, the whole capture was discarded and the retry had to crawl
    again. The failure a reuse is FOR is a failure after the capture, so an
    artifact that only survives a green run cannot serve one."""
    convert = load_workflow(WORKFLOW)["jobs"]["convert"]
    upload = next(
        s for s in convert["steps"] if "Upload the neutralized capture" in s.get("name", "")
    )
    cond = upload.get("if") or ""
    assert "cancelled()" in cond, cond
    # Conditioned on a capture existing, so a run that never captured does not
    # add a second red step on top of the real failure.
    assert "steps.capture.outcome" in cond, cond
    assert "steps.reuse.outcome" in cond, cond
    # `outcome`, not `conclusion`: they differ exactly when a step failed,
    # which is the case being handled.
    assert "conclusion" not in cond, cond
    ids = {s.get("id") for s in convert["steps"]}
    assert {"capture", "reuse"} <= ids, ids


def test_reuse_does_not_require_the_SOURCE_run_to_have_succeeded():
    """The capture worth reusing usually belongs to a run that FAILED after the
    crawl. A check on the source run's conclusion would refuse exactly the runs
    this input exists for."""
    run = step_run(WORKFLOW, "convert", "Reuse the capture from an earlier run")
    # Asserted on what the step QUERIES, not on the word "success" appearing
    # anywhere: a prose comment in this step legitimately contains it, and a
    # bare substring assert failed on that comment rather than on any check.
    code = "\n".join(l for l in run.splitlines() if not l.lstrip().startswith("#"))
    assert ".conclusion" not in code, code
    assert ".status" not in code, code


def test_verify_reused_capture_is_self_tested_in_the_gate():
    gate = step_run(WORKFLOW, "resolve", "Offline self-tests (gate every later job)")
    assert "verify-reused-capture.mjs --self-test" in gate, gate


def test_the_summary_says_when_a_capture_was_reused():
    """An approval is only meaningful if the approver can see what they are
    approving, and a reused capture describes the site at an earlier moment.
    The run log that says so is fifteen steps above the gate."""
    run = step_run(WORKFLOW, "convert", "Report what would be written")
    assert "REUSE_RUN" in run, run
    assert "REUSED" in run, run


def test_parse_host_mounts_is_self_tested_in_the_gate():
    gate = step_run(WORKFLOW, "resolve", "Offline self-tests (gate every later job)")
    assert "parse-host-mounts.mjs --self-test" in gate, gate


# --- job wiring -------------------------------------------------------------


def test_self_tests_gate_every_later_job():
    """Every script this workflow runs decides either what gets fetched from a
    live site or what gets written into published markup. The gate lives in
    `resolve`, which both other jobs need, so a regression cannot reach the
    network.

    The population is DERIVED from the workflow, not listed here. An earlier
    version named four scripts in a tuple; a fifth was added to `convert` and
    the test went on passing, because a hardcoded list can only check the
    scripts someone remembered to add to it. The question worth asking is
    "is anything this workflow runs ungated?", and only the workflow knows the
    left-hand side of that.
    """
    run = step_run(WORKFLOW, "resolve", "Offline self-tests (gate every later job)")
    gated = set(re.findall(r"scripts/([\w.-]+\.mjs) --self-test", run))
    invoked = set(re.findall(r"scripts/([\w.-]+\.mjs)", (WORKFLOWS / WORKFLOW).read_text(encoding="utf-8")))

    ungated = invoked - gated
    assert not ungated, f"invoked but not self-test gated: {sorted(ungated)}"
    # A derived population can collapse to zero if the extraction breaks, and an
    # empty set satisfies the assertion above vacuously — which is the same
    # failure the hardcoded tuple had, just harder to see. Floor it.
    assert len(gated) >= 5, f"only {len(gated)} script(s) gated: {sorted(gated)}"

    wf = load_workflow(WORKFLOW)
    assert wf["jobs"]["convert"]["needs"] == "resolve", wf["jobs"]["convert"].get("needs")
    assert "resolve" in wf["jobs"]["deliver"]["needs"], wf["jobs"]["deliver"].get("needs")


def test_the_self_containment_gate_probes_the_source_site():
    """A same-origin 404 is only a clone defect if the SOURCE still serves the
    file. The first delivery of this workflow failed all 120 pages on
    `/dist/widgets.css?v=2110` and `/cdn-cgi/rum?` — a leftover WordPress.com
    reference and a Cloudflare beacon endpoint, neither of which exists on the
    origin, so no capture could ever mirror them.

    `--no-source-probe` restores the old always-fatal behaviour, so its presence
    here would silently reinstate the failure this fixes.
    """
    run = step_run(WORKFLOW, "convert", "Gate - the export must be self-contained")
    assert "verify-no-legacy.mjs" in run, run
    assert "--no-source-probe" not in run, (
        "the gate must ask the source site whether it serves a missing asset:\n" + run
    )
    # Probing is only meaningful against a local export; --dir is what selects it.
    assert "--dir" in run, run


def test_the_gate_and_the_capture_agree_on_the_localized_asset_dir():
    """`verify-no-legacy` refuses to let the source excuse a missing file under
    the clone's own directories, and it names `_ffc-assets/` as one of them.
    That name is chosen by the capture. If either side is renamed alone the
    guard silently stops matching, and a dropped localized asset goes back to
    being excused by the source's 404 on a path it never had — the failure is
    invisible because nothing errors, the gate just gets quieter."""
    capture = (REPO_ROOT / "scripts" / "capture-wordpress-api.mjs").read_text(encoding="utf-8")
    gate = (REPO_ROOT / "scripts" / "verify-no-legacy.mjs").read_text(encoding="utf-8")

    m = re.search(r"assetsDirName\s*=\s*['\"]([^'\"]+)['\"]", capture)
    assert m, "the capture no longer declares assetsDirName"
    assets_dir = m.group(1)

    m2 = re.search(r"CLONE_OWNED_PREFIXES\s*=\s*\[([^\]]*)\]", gate)
    assert m2, "the gate no longer declares CLONE_OWNED_PREFIXES"
    prefixes = re.findall(r"['\"]([^'\"]+)['\"]", m2.group(1))

    assert f"/{assets_dir}/" in prefixes, (
        f"the capture writes assets to {assets_dir!r} but the gate guards {prefixes}"
    )
    assert "/_next/" in prefixes, f"the Next.js build output is unguarded: {prefixes}"


def test_only_deliver_is_gated_and_only_deliver_writes():
    """Two independent drifts, both silent. Gating `convert` makes a read-only
    dry conversion wait on a human; ungating `deliver` lets an unapproved run
    open a PR on another repository."""
    wf = load_workflow(WORKFLOW)
    assert wf["jobs"]["resolve"].get("environment") is None
    assert wf["jobs"]["convert"].get("environment") is None
    assert wf["jobs"]["deliver"].get("environment") == "github-prod"

    convert = str(wf["jobs"]["convert"])
    assert "gh pr create" not in convert, "convert must not write to another repo"
    assert "git push" not in convert, "convert must not push"


def test_deliver_only_runs_in_deliver_mode():
    wf = load_workflow(WORKFLOW)
    cond = wf["jobs"]["deliver"]["if"]
    assert "needs.resolve.outputs.mode == 'deliver'" in cond, cond


def test_deliver_takes_the_capture_that_passed_the_gate():
    """Re-capturing would hit the charity's server twice and could disagree
    with the tree the build and the self-containment gate actually validated —
    so the evidence would no longer be about the delivered artifact."""
    wf = load_workflow(WORKFLOW)
    steps = wf["jobs"]["deliver"]["steps"]
    uses = [s.get("uses", "") for s in steps]
    assert any("download-artifact" in u for u in uses), uses
    joined = str(steps)
    assert "capture-wordpress-api.mjs" not in joined, "deliver must not re-capture"


def test_deliver_refuses_a_partial_or_form_bearing_capture():
    """The last point before a dead form or a half-transferred site becomes a
    published page. Both would otherwise review as capture problems."""
    run = step_run(WORKFLOW, "deliver", "Verify the downloaded capture is intact")
    assert "site/index.html" in run, run
    assert "<form" in run, run
    assert "exit 1" in run, run


def test_convert_keys_concurrency_on_the_normalized_domain():
    """A workflow-level group cannot read a job output, so it would key on the
    raw input and let `https://Example.org/` and `example.org` capture the same
    site in parallel."""
    wf = load_workflow(WORKFLOW)
    group = wf["jobs"]["convert"]["concurrency"]["group"]
    assert "needs.resolve.outputs.domain" in group, group
    assert "inputs.domain" not in group, group
    assert wf["jobs"]["convert"]["concurrency"]["cancel-in-progress"] is False
    assert "concurrency" not in wf, wf.get("concurrency")


def test_workflow_is_dispatch_only():
    """No schedule and no push trigger: this reaches out to a third party's
    live server and can write to another repo, so every run is deliberate."""
    wf = load_workflow(WORKFLOW)
    triggers = wf[True] if True in wf else wf["on"]
    assert set(triggers) == {"workflow_dispatch"}, triggers


def test_convert_runs_the_self_containment_gate():
    """The only check that can see a page still depending on the live origin —
    the capture report cannot, because nothing failed at capture time."""
    run = step_run(WORKFLOW, "convert", "Gate - the export must be self-contained")
    assert "verify-no-legacy.mjs" in run, run


def test_forms_are_neutralized_before_integration():
    """Ordering matters: neutralizing after integration would leave the live
    forms in the uploaded artifact, which is what `deliver` consumes."""
    wf = load_workflow(WORKFLOW)
    names = [s.get("name", "") for s in wf["jobs"]["convert"]["steps"]]
    forms = next(i for i, n in enumerate(names) if "mailto" in n)
    integrate = next(i for i, n in enumerate(names) if n.startswith("Integrate"))
    assert forms < integrate, names


def test_forms_without_a_contact_address_fail_the_run():
    """A form on a static export accepts a visitor's message and drops it. That
    is invisible to every render-time check, so it has to fail here."""
    run = step_run(WORKFLOW, "convert", "Replace forms with a mailto: block")
    assert "contact_email was not set" in run, run
    assert "exit 1" in run, run


def _run_forms_step(files: dict[str, str], email: str = "") -> subprocess.CompletedProcess:
    """EXECUTE the form-detection step against a real tree.

    The earlier version of this test asserted the branch's TEXT was present in
    the step. That is a proxy, and it passed while the branch was unreachable:
    grep exits 1 on "no match", `set -euo pipefail` turned that into an abort,
    and every form-free site failed the conversion at a step whose entire job
    was to notice there was nothing to do. Text-matching cannot see control
    flow — so this runs the thing.
    """
    script = step_run(WORKFLOW, "convert", "Replace forms with a mailto: block")
    with tempfile.TemporaryDirectory() as td:
        tdp = pathlib.Path(td)
        site = tdp / "capture" / "site"
        site.mkdir(parents=True)
        for name, body in files.items():
            f = site / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body, encoding="utf-8")
        outputs = tdp / "out.txt"
        summary = tdp / "summary.md"
        outputs.touch()
        summary.touch()
        env = child_env(
            HARNESS_DIR,
            HOME=str(tdp),
            # Forward slashes: this step body runs under `bash` below, and MSYS
            # eats a backslash as an escape, so a native `C:\...` from
            # TemporaryDirectory arrives mangled on the Windows host (#1247).
            RUNNER_TEMP=forward_slashes(str(tdp)),
            GITHUB_OUTPUT=str(outputs),
            GITHUB_STEP_SUMMARY=str(summary),
            EMAIL=email,
            DOMAIN="example.org",
        )
        proc = subprocess.run(
            ["bash", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            cwd=str(REPO_ROOT),
        )
        proc.outputs = outputs.read_text(encoding="utf-8")  # type: ignore[attr-defined]
        return proc


def test_a_form_free_site_completes_instead_of_aborting():
    """The regression Copilot caught: with no forms, grep exits 1, pipefail
    propagates it, and the step dies before the `found -eq 0` branch it exists
    to reach."""
    proc = _run_forms_step({"index.html": "<h1>no forms here</h1>"})
    assert proc.returncode == 0, f"form-free site aborted:\n{proc.stdout}\n{proc.stderr}"
    assert "pages_with_forms=0" in proc.outputs, proc.outputs  # type: ignore[attr-defined]


def test_a_form_bearing_site_without_a_contact_address_fails_closed():
    """A form on a static export accepts a visitor's message and drops it."""
    proc = _run_forms_step({"contact/index.html": '<form action="/x"><input></form>'})
    assert proc.returncode != 0, proc.stdout
    assert "contact_email was not set" in proc.stdout, proc.stdout


def test_forms_are_actually_replaced_when_an_address_is_given():
    proc = _run_forms_step(
        {"contact/index.html": '<form action="/x"><input></form>'}, email="info@example.org"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "pages_with_forms=1" in proc.outputs, proc.outputs  # type: ignore[attr-defined]


def test_a_failed_search_is_not_reported_as_no_forms():
    """grep exits >1 on a real error. Collapsing that to "no forms found" is
    how a search that never ran becomes "nothing to replace" and ships live,
    dead forms — strictly worse than the abort this replaced."""
    run = step_run(WORKFLOW, "convert", "Replace forms with a mailto: block")
    assert '[ "$rc" -gt 1 ]' in run, run
    assert "Refusing to treat a failed search as 'no forms'" in run, run


def _run_parked_routes_step(*, referencing_test: str | None, test_dirs=("__tests__", "tests"),
                            grep_stub_rc: int | None = None):
    """EXECUTE the parked-routes reporting step against a real fixture tree.

    Asserted by running it, not by grepping its source. An earlier version of
    the sibling forms test asserted that the branch TEXT was present, which
    passes just as happily when the branch is unreachable.
    """
    import os

    run = step_run(WORKFLOW, "convert", "Report tests that reference the parked routes")
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        parked = root / "_disabled_template_routes"
        (parked / "cookie-policy").mkdir(parents=True)
        (parked / "page.tsx").write_text("", encoding="utf-8")
        (parked / "cookie-policy" / "page.tsx").write_text("", encoding="utf-8")
        for d in test_dirs:
            (root / d).mkdir(exist_ok=True)
        if referencing_test:
            (root / test_dirs[0] / "a.test.ts").write_text(referencing_test, encoding="utf-8")

        script = root / "step.sh"
        script.write_text(run, encoding="utf-8")
        summary = root / "summary.md"
        summary.touch()

        env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary))
        if grep_stub_rc is not None:
            shim = root / "shim"
            shim.mkdir()
            (shim / "grep").write_text(f"#!/bin/sh\nexit {grep_stub_rc}\n", encoding="utf-8")
            (shim / "grep").chmod(0o755)
            env["PATH"] = f"{shim}{os.pathsep}{env['PATH']}"

        proc = subprocess.run(
            ["bash", "-e", "-o", "pipefail", str(script)],
            cwd=str(root), env=env, capture_output=True, text=True,
            encoding="utf-8", timeout=60,
        )
        return proc, summary.read_text(encoding="utf-8")


def test_a_parked_route_no_test_mentions_does_not_abort_the_conversion():
    """This killed delivery attempt 5 AFTER the self-containment gate passed.

    `shell: bash` runs with -e, grep exits 1 for "no match" — the normal answer
    here — and with pipefail that 1 propagates out of the pipeline and through
    the assignment, ending the step on the first route no test referenced. The
    identical bug had already been found and fixed in the forms search fifteen
    lines earlier in the same file; fixing one site and leaving its twin is the
    failure this workflow keeps repeating, so both are now executed by tests."""
    proc, summary = _run_parked_routes_step(referencing_test="import x\n")
    assert proc.returncode == 0, f"a route no test mentions aborted the step:\n{proc.stdout}{proc.stderr}"
    assert "No non-root parked route is referenced by a test." in summary, summary


def test_a_referencing_test_is_still_reported():
    """The permissive fix must not cost the finding the step exists to make."""
    proc, summary = _run_parked_routes_step(referencing_test="fetch('/cookie-policy')\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "/cookie-policy` referenced by" in summary, summary


def test_a_failed_search_still_stops_the_run():
    """grep >1 is a real error. Reporting "no references" for a search that
    never ran is the fail-open this guard exists to prevent — and the reason
    the fix is not a blanket `|| true`."""
    proc, summary = _run_parked_routes_step(referencing_test=None, grep_stub_rc=2)
    assert proc.returncode != 0, f"a failed search was treated as 'no references':\n{summary}"
    assert "Refusing to treat a failed search as 'no references'" in proc.stdout + proc.stderr


def test_a_target_repo_with_no_test_directories_says_so():
    """grep exits 2 for a missing path, which the guard above treats as a real
    error — so a repo carrying only one of the two conventional test directories
    would fail for no reason. Absence is reported as absence, not as a clean
    bill of health."""
    proc, summary = _run_parked_routes_step(referencing_test=None, test_dirs=())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "no `__tests__` or `tests` directory" in summary, summary
    # And the home-page note survives. The first version of this test asserted
    # only the message it had just added, so it passed while the `searched`
    # guard — placed before the root-route check — silently dropped the one
    # note that matters most: that the template home page was replaced. A test
    # that checks only the behaviour you added cannot see what you broke.
    # Caught in review on #1231.
    assert "the home page) was parked" in summary, (
        "the root-parked note vanished when the repo has no test dirs:\n" + summary
    )


def test_the_capture_output_path_is_the_path_every_later_step_reads():
    """`--out` IS the site root — the script writes pages directly into it, not
    into an `out/site/` subdirectory. Passing `--out .../capture` and then
    reading `.../capture/site` produces an empty clone, and integration against
    an empty clone succeeds: it parks the template routes and copies nothing,
    so the build passes and publishes a blank site. Nothing downstream fails.

    So the value is asserted to agree across every step that names it, in both
    jobs, rather than being read as correct four times."""
    wf = load_workflow(WORKFLOW)
    out_paths, clone_paths, site_vars = set(), set(), set()
    for job in ("convert", "deliver"):
        for step in wf["jobs"][job]["steps"]:
            run = step.get("run", "")
            for line in run.splitlines():
                line = line.strip()
                if "--out " in line:
                    out_paths.add(line.split("--out ", 1)[1].split()[0].strip('"'))
                if "--clone " in line:
                    clone_paths.add(line.split("--clone ", 1)[1].split()[0].strip('"'))
                if line.startswith("site="):
                    site_vars.add(line.split("=", 1)[1].strip('"'))

    assert len(out_paths) == 1, f"capture writes to more than one path: {out_paths}"
    assert clone_paths == out_paths, f"integration reads {clone_paths}, capture writes {out_paths}"
    assert site_vars == out_paths, f"form/verify steps read {site_vars}, capture writes {out_paths}"


def test_the_report_filename_matches_what_the_script_writes():
    """A workflow that reads `capture-report.json` when the script writes
    `wp-capture-report.json` fails at the step that reads it — which is
    usually the reporting step, i.e. after the expensive part, and with an
    error naming the reader rather than the mismatch."""
    script = (REPO_ROOT / "scripts" / "capture-wordpress-api.mjs").read_text(encoding="utf-8")
    assert "'wp-capture-report.json'" in script, "the script's report filename moved"
    wf_text = (REPO_ROOT / ".github" / "workflows" / WORKFLOW).read_text(encoding="utf-8")
    for line in wf_text.splitlines():
        if "capture-report.json" in line:
            assert "wp-capture-report.json" in line, line


def test_the_forms_output_is_named_for_what_it_actually_counts():
    """The step counts FILES matching a form tag (grep -rl | wc -l), not forms.
    Calling that `replaced` invites a later reader to use it as a replacement
    count — a number that is wrong by however many pages carry two forms, which
    on this first target is most of them (343 pages, 2 forms each)."""
    wf = load_workflow(WORKFLOW)
    assert wf["jobs"]["convert"]["outputs"] == {
        "pages_with_forms": "${{ steps.forms.outputs.pages_with_forms }}"
    }, wf["jobs"]["convert"]["outputs"]
    run = step_run(WORKFLOW, "convert", "Replace forms with a mailto: block")
    assert "grep -rlIE" in run and "wc -l" in run, run
    assert "replaced=" not in run, "output name no longer matches what is counted:\n" + run


def test_a_parked_root_route_is_reported_rather_than_skipped():
    """`/` cannot be grepped for the way other routes can — a search for "/"
    matches every test file. Skipping it prints "None.", which reads as
    "nothing is affected" for the one route every template has, at the exact
    moment the home page has been replaced."""
    run = step_run(WORKFLOW, "convert", "Report tests that reference the parked routes")
    # Counting occurrences of the flag is a PROXY, not the property, and it
    # passes when the assignment is deleted but the reporting branches remain.
    # Assert the two halves that actually matter, at the lines that matter.
    branch = next((l for l in run.splitlines() if '"$r" = "/"' in l), None)
    assert branch is not None, "no root-route branch at all:\n" + run
    assert "root_parked" in branch, (
        "the root route is skipped without recording it, so the summary prints "
        '"None." for the one route every template has:\n' + branch
    )
    assert 'if [ -n "$root_parked" ]' in run, "recorded but never reported:\n" + run
    assert "the home page" in run, run


# --- whitespace must be rejected, never deleted ------------------------------


def test_internal_whitespace_in_the_domain_fails_closed():
    """`tr -d '[:space:]'` read as extra safety and was a fail-OPEN: deleting
    internal whitespace turns an invalid value into a different VALID one, so
    the hostname guard then accepts it and a live capture is pointed at a host
    the operator never typed."""
    proc, outputs = run_resolve(INPUT_DOMAIN="example.org attacker.com")
    assert proc.returncode != 0, f"accepted a two-token domain:\n{outputs}"
    assert "example.orgattacker.com" not in outputs, (
        "whitespace was deleted rather than rejected, fabricating a host:\n" + outputs
    )


def test_surrounding_whitespace_in_the_domain_is_still_trimmed():
    """Rejecting internal whitespace must not start rejecting a pasted value
    with a stray leading space — that would be a different, annoying failure."""
    proc, outputs = run_resolve(INPUT_DOMAIN="  example.org\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "domain=example.org" in outputs, outputs


def test_internal_whitespace_in_the_contact_email_fails_closed():
    """`info @example.org` deleted to `info@example.org` is a published contact
    address the operator never typed — and the only channel the static site
    has."""
    proc, outputs = run_resolve(INPUT_EMAIL="info @example.org")
    assert proc.returncode != 0, f"accepted a spaced address:\n{outputs}"


def test_internal_whitespace_in_the_target_repo_fails_closed():
    proc, outputs = run_resolve(INPUT_REPO="FFC-EX-a.org evil")
    assert proc.returncode != 0, f"accepted a two-token repo:\n{outputs}"


def test_a_pasted_repo_url_with_git_or_trailing_slash_is_normalized():
    for raw in (
        "https://github.com/FreeForCharity/FFC-EX-a.org/",
        "https://github.com/FreeForCharity/FFC-EX-a.org.git",
    ):
        proc, outputs = run_resolve(INPUT_REPO=raw)
        assert proc.returncode == 0, f"{raw}: {proc.stdout}{proc.stderr}"
        assert "repo=FFC-EX-a.org" in outputs, f"{raw}: {outputs}"


def test_whitespace_separates_ignore_hosts_rather_than_vanishing():
    """`a.org b.org` must become two validated entries, never the single bogus
    host `a.orgb.org` that deleting the space would produce."""
    proc, outputs = run_resolve(INPUT_IGNORE="a.org b.org")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ignore_hosts=a.org,b.org" in outputs, outputs


def test_internal_whitespace_in_a_numeric_input_fails_closed():
    """`1 2` deleted to `12` is a bound the operator never set."""
    proc, outputs = run_resolve(INPUT_MAX="1 2")
    assert proc.returncode != 0, f"accepted a spaced number:\n{outputs}"


# --- the CNAME, where confusing the two domains is most dangerous ------------


def test_the_cname_is_never_the_source_domain():
    """integrate-clone-into-nextjs writes public/CNAME as
    (existing CNAME || --domain). Passing the SOURCE domain sets the clone's
    Pages custom domain to the host the original WordPress still serves from —
    the single most dangerous line in the conversion, and invisible in any
    output that only counts pages."""
    wf = load_workflow(WORKFLOW)
    for job in ("convert", "deliver"):
        run = step_run(WORKFLOW, job, "Integrate the capture into the Next.js app")
        assert "--domain \"$CNAME_DOMAIN\"" in run, f"{job}: {run}"
        assert "outputs.domain" not in run, (
            f"{job}: the SOURCE domain reaches --domain and becomes the Pages custom domain:\n{run}"
        )


def test_the_cname_domain_defaults_to_the_destination_not_the_source():
    """FFC-EX-vpmin.org -> vpmin.org, never viewpointministriesinternational.org."""
    proc, outputs = run_resolve(
        INPUT_DOMAIN="viewpointministriesinternational.org",
        INPUT_REPO="FFC-EX-vpmin.org",
        INPUT_PUBLISH="",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cname_domain=vpmin.org" in outputs, outputs
    assert "cname_domain=viewpointministriesinternational.org" not in outputs, outputs


def test_an_explicit_publish_domain_wins():
    proc, outputs = run_resolve(INPUT_REPO="FFC-EX-a.org", INPUT_PUBLISH="https://WWW.Custom.org/")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "cname_domain=custom.org" in outputs, outputs
    assert "publish_domain=custom.org" in outputs, outputs


def test_no_publish_domain_removes_the_cname_so_the_default_pages_url_works():
    """A CNAME file is not inert: Pages switches to the custom domain and
    REDIRECTS the default URL to it. Leaving one behind makes the converted
    site unreachable at the only address that works before DNS cutover — which
    is exactly the state this workflow is meant to deliver."""
    for job in ("convert", "deliver"):
        run = step_run(WORKFLOW, job, "Integrate the capture into the Next.js app")
        assert "rm -f ffc-ex/public/CNAME" in run, f"{job}: {run}"
        assert '[ -z "$PUBLISH_DOMAIN" ]' in run, f"{job}: {run}"


def test_a_pre_existing_cname_is_never_removed():
    """Deleting a custom domain the target repo already had would be a silent,
    destructive change to a site that is already cut over."""
    for job in ("convert", "deliver"):
        run = step_run(WORKFLOW, job, "Integrate the capture into the Next.js app")
        assert "had_cname" in run, f"{job}: {run}"
        idx_record = run.index("had_cname=yes")
        idx_rm = run.index("rm -f ffc-ex/public/CNAME")
        assert idx_record < idx_rm, f"{job}: the check must be recorded before integration"
        assert '[ "$had_cname" = "no" ]' in run, f"{job}: {run}"


def test_target_repo_outside_the_ffc_ex_fleet_is_refused():
    """`deliver` holds an org-wide PAT and the integration replaces the
    target's public/ wholesale, so a typo does not fail — it succeeds against
    a repo nobody meant to touch."""
    proc, _ = run_resolve(INPUT_REPO="FFC-Cloudflare-Automation")
    assert proc.returncode != 0, proc.stdout
    assert "not an FFC-EX repository" in proc.stdout, proc.stdout


def test_a_lowercase_owner_prefix_is_accepted():
    proc, outputs = run_resolve(INPUT_REPO="https://github.com/freeforcharity/FFC-EX-a.org")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "repo=FFC-EX-a.org" in outputs, outputs


def test_a_repo_that_is_only_the_prefix_is_refused():
    """`FFC-EX-` passes the name pattern AND the prefix gate, then derives to
    an empty destination domain that integrate rejects as a usage error —
    after the capture, the build and the self-containment gate have all run.
    Failing here names the input; failing there does not."""
    proc, _ = run_resolve(INPUT_REPO="FFC-EX-")
    assert proc.returncode != 0, proc.stdout
    assert "carries no domain" in proc.stdout, proc.stdout


def test_the_delivered_pr_body_renders_as_markdown_not_a_code_block():
    """Reported as a defect (an inline --body carrying YAML indentation into
    the argument, so 4+ leading spaces make a Markdown code block). MEASURED,
    and it does not reproduce: the continuation lines sit at the block's base
    indent, which YAML strips, so the shell receives them at column 0.

    Kept because the property is worth holding rather than the finding: the
    body IS assembled inside a YAML block scalar, and one line indented deeper
    than the rest would silently become preformatted text on the PR a human is
    asked to review. Executed, since the failure is in the rendered bytes."""
    import os

    run = step_run(WORKFLOW, "deliver", "Commit and open a draft PR on the FFC-EX repo")
    frag = run[run.index('--body "Automated') :]
    body = frag[len('--body "') :]
    body = body[: body.rindex('"')]
    indented = [l for l in body.splitlines() if l.startswith("    ") and l.strip()]
    assert not indented, "these would render as a code block:\n" + "\n".join(indented)


def test_a_derived_destination_domain_must_be_a_hostname():
    """target_repo legitimately allows `_` and needs no dot, so `FFC-EX-my_repo`
    derives to `my_repo` — non-empty, not a hostname, and bound for
    public/CNAME. Guarding only emptiness was half a fix."""
    proc, _ = run_resolve(INPUT_REPO="FFC-EX-my_repo")
    assert proc.returncode != 0, proc.stdout
    assert "is not a bare hostname" in proc.stdout, proc.stdout


# --- completeness gates delivery; a site's own dead links do not -------------


def _run_capture_gate(
    captured: int, expected: int, problems: list[str], min_pct: str = "98", write_report: bool = True
):
    """EXECUTE the real assessment script against a synthetic report.

    Runs the shipped binary, not a shell fragment lifted out of the workflow:
    the logic moved into scripts/assess-capture-completeness.mjs, and a test
    that kept exercising an inlined copy would pass while the workflow ran
    something else.
    """
    import json
    import os

    script = REPO_ROOT / "scripts" / "assess-capture-completeness.mjs"
    with tempfile.TemporaryDirectory() as td:
        report = pathlib.Path(td) / "wp-capture-report.json"
        if write_report:
            report.write_text(
                json.dumps(
                    {
                        "captured": {"total": captured},
                        "entries": [{"i": i} for i in range(expected)],
                        "verdict": {"ok": not problems, "problems": problems},
                    }
                ),
                encoding="utf-8",
            )
        summary = pathlib.Path(td) / "summary.md"
        summary.touch()
        env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary))
        return subprocess.run(
            ["node", str(script), "--report", str(report), "--min-percent", min_pct],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )


# The live site's own broken links, from the first real delivery attempt.
REAL_PROBLEMS = [
    "captured 589 of 590 inventory entries (REST collections + sitemap union)",
    "unlocalized asset hosts: myviewletstalkaboutjesus.files.wordpress.com",
    "assets: 15 failed (11×HTTP 404, 4×HTTP 410); 11 of them on viewpointministriesinternational.org",
]


def test_a_sites_own_dead_links_do_not_block_the_conversion():
    """The first live delivery failed here. The capture had reached 589 of 590
    entries with ZERO page-fetch failures, and was rejected over 15 assets that
    return 404/410 on the LIVE site too. Gating on that means FFC can never
    migrate a site with one dead image, which is most real charity sites — the
    clone reproduces those links faithfully rather than introducing them."""
    proc = _run_capture_gate(589, 590, REAL_PROBLEMS)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_losing_content_still_blocks_the_conversion():
    proc = _run_capture_gate(2, 587, ["captured 2 of 587"])
    assert proc.returncode != 0, proc.stdout
    assert "below the 98% completeness floor" in proc.stderr, proc.stderr


def test_the_completeness_floor_is_enforced_near_the_boundary():
    """95.9% must fail while 99.8% passes — otherwise the floor is decorative."""
    assert _run_capture_gate(566, 590, REAL_PROBLEMS).returncode != 0
    assert _run_capture_gate(589, 590, REAL_PROBLEMS).returncode == 0


def test_an_empty_inventory_is_a_failure_not_a_vacuous_pass():
    """0 captured of 0 expected is 0/0. As a ratio it is NaN or 1.0 depending
    on how it is written, and one of those reads as a perfect capture of a site
    with no pages."""
    proc = _run_capture_gate(0, 0, ["nothing"])
    assert proc.returncode != 0, proc.stdout
    assert "no inventory entries at all" in proc.stderr, proc.stderr


def test_an_unreadable_report_is_a_failure_not_an_assumed_pass():
    """A capture that died mid-write leaves no readable report. Treating that
    as "no problems found" converts whatever fragment it left behind."""
    proc = _run_capture_gate(0, 0, [], write_report=False)
    assert proc.returncode != 0, proc.stdout
    assert "Cannot read the capture report" in proc.stderr, proc.stderr


def test_the_floor_is_operator_controllable():
    proc = _run_capture_gate(589, 590, REAL_PROBLEMS, min_pct="100")
    assert proc.returncode != 0, proc.stdout
    assert "below the 100% completeness floor" in proc.stderr, proc.stderr


def test_the_workflow_calls_the_shipped_script_not_an_inline_copy():
    """The assessment logic lives in a file so it can be self-tested and so it
    is not a `node -e` single-quoted string whose JS template literals read to
    shellcheck as shell variables (SC2016 — this failed CI)."""
    run = step_run(WORKFLOW, "convert", "Capture the live WordPress site")
    assert "assess-capture-completeness.mjs" in run, run
    # Comments are stripped first: the comment above the call NAMES `node -e`
    # to explain why it is not used, and a bare substring match flags that as
    # the very thing it forbids. A check that cannot tell code from prose
    # reports the explanation as the violation.
    code = "\n".join(l for l in run.splitlines() if not l.strip().startswith("#"))
    assert "node -e" not in code, "the assessment was inlined again:\n" + code
    gate = step_run(WORKFLOW, "resolve", "Offline self-tests (gate every later job)")
    assert "assess-capture-completeness.mjs --self-test" in gate, gate


def test_min_capture_percent_is_validated():
    proc, _ = run_resolve(INPUT_MINPCT="abc")
    assert proc.returncode != 0, proc.stdout
    assert "must be a whole number between 1 and 100" in proc.stdout, proc.stdout




def _step_names(job_id: str) -> list[str]:
    """Step names of one job, in order."""
    wf = load_workflow(WORKFLOW)
    return [s.get("name", "") for s in wf["jobs"][job_id]["steps"]]


def _index_of(job_id: str, substring: str) -> int:
    for i, name in enumerate(_step_names(job_id)):
        if substring in name:
            return i
    raise AssertionError(f"no step matching {substring!r} in job {job_id}: {_step_names(job_id)}")


def _capture_script_text() -> str:
    return (REPO_ROOT / "scripts" / "capture-wordpress-api.mjs").read_text(encoding="utf-8")


GUARD = "the tree must be publishable"


def test_the_publishable_size_guard_runs_in_BOTH_jobs():
    """The convert-job copy is the early warning; the deliver-job copy is the
    one that actually protects the push.

    Neither is redundant. `deliver` can run against a capture REUSED from a run
    that predates the PDF pass, so a convert-side check alone proves nothing
    about the tree being pushed; and a deliver-side check alone moves the
    failure back behind the human approval, which is the cost this guard
    exists to avoid.
    """
    for job in ("convert", "deliver"):
        i = _index_of(job, GUARD)
        script = step_run(WORKFLOW, job, GUARD)
        assert "check-publishable-size.mjs ffc-ex" in script, (job, script)
        assert i >= 0


def test_the_size_guard_runs_before_the_steps_it_exists_to_save():
    """Fail in milliseconds, not after a multi-minute build or behind a push.

    The guard only reads file sizes off disk. Ordering it later would still
    catch the problem, but only once the run has spent the very time this
    check exists to save.
    """
    assert _index_of("convert", GUARD) < _index_of("convert", "Build the static export")
    assert _index_of("deliver", GUARD) < _index_of("deliver", "Commit and open a draft PR")


def test_the_size_guard_is_self_tested_before_anything_uses_it():
    """Same rule every other decision-making script in this workflow follows:
    a guard that decides whether a run may proceed does not run unverified."""
    script = step_run(WORKFLOW, "resolve", "Offline self-tests")
    assert "node scripts/check-publishable-size.mjs --self-test" in script


def test_max_pdf_mb_is_validated_before_the_network():
    """A bad budget must cost seconds, not a 40-minute crawl AND an approval."""
    script = step_run(WORKFLOW, "resolve", "Resolve inputs")
    assert "max_pdf_mb must be a whole number of MB between 1 and 100000" in script
    assert 'echo "max_pdf_mb=$max_pdf_mb"' in script


def test_max_pdf_mb_is_bounded_to_match_the_capture():
    """`resolve` and the capture must agree on the range, or the earlier check
    is decorative.

    Measured: `capture-wordpress-api.mjs` declares
    `['max-pdf-mb', ..., { min: 1, max: 100000 }]` and exits 2 with
    "expected an integer 1..100000". A resolve that only checks positivity lets
    999999 through, and the run then dies in `convert` -- after checkout and
    setup -- which is exactly the "fail before the network" promise this job
    exists to keep.
    """
    script = step_run(WORKFLOW, "resolve", "Resolve inputs")
    assert '[ "$max_pdf_mb" -gt 100000 ]' in script, script[-400:]
    assert "between 1 and 100000" in script

    # The input description must not tell an operator to do the thing the
    # bound refuses; the first draft said "set to a very large number".
    wf = load_workflow(WORKFLOW)
    triggers = wf[True] if True in wf else wf["on"]
    desc = triggers["workflow_dispatch"]["inputs"]["max_pdf_mb"]["description"]
    assert "very large number" not in desc, desc
    assert "100000" in desc, desc


def test_max_pdf_mb_reaches_the_capture():
    """The input is inert unless it is BOTH exported to the step and appended
    to the capture's argv. Asserting only one of the two passes while the
    budget silently stays at the script's own default."""
    wf = load_workflow(WORKFLOW)
    step = find_step(wf, "convert", "Capture the live WordPress site")
    assert step["env"]["MAX_PDF_MB"] == "${{ needs.resolve.outputs.max_pdf_mb }}", step["env"]
    assert 'args+=(--max-pdf-mb "$MAX_PDF_MB")' in step["run"]


def test_the_resolve_job_publishes_max_pdf_mb():
    """A job output that names a step output the step never sets resolves to
    the empty string, and an empty budget is not an error anywhere downstream
    — it simply stops being applied."""
    wf = load_workflow(WORKFLOW)
    assert wf["jobs"]["resolve"]["outputs"]["max_pdf_mb"] == (
        "${{ steps.resolve.outputs.max_pdf_mb }}"
    )


def test_the_pdf_budget_default_is_below_githubs_hard_limit():
    """90, not 100.

    Ghostscript's output size is not predictable from its input, so a budget
    set AT the limit lets a file land at 99.7 MB on one run and 100.4 MB on the
    next — and that difference only shows up at the push, after the approval.
    """
    wf = load_workflow(WORKFLOW)
    # YAML parses a bare `on:` key as the boolean True, so this cannot be
    # read as wf["on"] — the same dance test_workflow_is_dispatch_only does.
    triggers = wf[True] if True in wf else wf["on"]
    assert triggers["workflow_dispatch"]["inputs"]["max_pdf_mb"]["default"] == "90"


def test_the_pdf_ladder_excludes_the_rung_that_inflates_a_file():
    """/prepress measured 176% of its input on a scan-shaped fixture. A pass
    that reports an optimisation while making the file bigger is worse than no
    pass, so that rung is not on the ladder at all."""
    src = _capture_script_text()
    ladder = src.split("export const PDF_DOWNSAMPLE_LADDER = ")[1].split(";")[0]
    assert "/prepress" not in ladder, ladder
    assert "/printer" not in ladder, ladder
    assert "/ebook" in ladder and "/screen" in ladder, ladder


def test_a_shrunk_pdf_keeps_its_name():
    """The image pass renames (.png -> .webp) and must rewrite every reference
    to match. The PDF pass must NOT rename: a renamed PDF strands every link to
    it, including links in places the capture never parses, such as a sitemap
    or a PDF that links to another PDF."""
    src = _capture_script_text()
    call = src.split("if (optimizePdfs && shouldShrinkPdf(")[1].split("usedAssetNames.add(name)")[0]
    assert "name = " not in call, f"the PDF pass must not reassign the local name:\n{call}"
    assert "buf = shrunk.buffer;" in call


def test_ghostscript_absence_is_distinguished_from_a_bad_pdf():
    """Opposite responses — stop trying at all, versus skip this one file —
    and they are indistinguishable by whether an output file appeared. An
    earlier draft inferred it from existsSync and would have announced
    'ghostscript is not installed' on the first corrupt PDF, on a box where it
    is installed and working."""
    src = _capture_script_text()
    assert "err.code === 'ENOENT'" in src


def test_pdfs_still_over_budget_are_named_not_counted():
    """These are exactly the files a push will reject. A count cannot be acted
    on without re-running the crawl that produced it."""
    src = _capture_script_text()
    assert "pdfShrink.stillOverBudget.join(', ')" in src
    assert "will be REJECTED by a" in src


def _dedupe_script_text() -> str:
    return (REPO_ROOT / "scripts" / "dedupe-capture-assets.mjs").read_text(encoding="utf-8")


def _integrate_script_text() -> str:
    return (REPO_ROOT / "scripts" / "integrate-clone-into-nextjs.mjs").read_text(encoding="utf-8")


def test_the_templates_own_asset_directories_survive_the_public_wipe():
    """integrate wipes `public/` wholesale, and the template's components go on
    referencing /Images/... and /Svgs/... by absolute path. Measured on
    FFC-EX-newheightseducation.org: 19 such assets across 12 files under src/.
    Every one was deleted, so every one 404'd in the export.

    Run 35698011512 surfaced three of them at the self-containment gate; the
    other sixteen were equally broken and merely sat on pages that crawl did
    not reach. Directories, not filenames: a name list stops tracking a
    template that gains and renames assets, which is how this got here."""
    src = _integrate_script_text()
    assert "export const PRESERVED_PUBLIC_DIRS" in src, src[:400]
    decl = src.split("export const PRESERVED_PUBLIC_DIRS", 1)[1].split("\n", 1)[0]
    for d in ("Images", "Svgs"):
        assert f"'{d}'" in decl, (d, decl)


def test_a_preserved_directory_still_loses_to_the_captured_site():
    """A directory entry is not a licence to overwrite the charity's content.
    The restore must stay per-file and skip a destination the clone already
    wrote, or a captured site shipping its own /Images/logo.webp would have the
    FFC template's logo written over it by the migration."""
    src = _integrate_script_text()
    body = src.split("export function restorePreservedPublicFiles", 1)[1].split("\n}", 1)[0]
    assert "if (existsSync(dest)) continue;" in body, body


def test_integrate_self_tests_cover_the_preserved_directories():
    """Asserted by RUNNING them, not by reading them. The behaviour that keeps a
    charity's site whole here is the recursive walk and the collision rule, and
    a source-text assertion cannot tell a live case from a deleted one."""
    proc = subprocess.run(
        ["node", str(REPO_ROOT / "scripts" / "integrate-clone-into-nextjs.mjs"), "--self-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env(),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out[-2000:]
    for name in (
        "the template asset directories survive the public/ wipe",
        "a preserved directory is walked recursively, not just its top level",
        "the captured site still wins inside a preserved directory",
    ):
        assert f"ok   {name}" in out, (name, out[-2000:])


def test_duplicate_assets_are_collapsed_after_the_capture_and_before_integration():
    """The whole value of this pass is WHERE it sits. Placed inside the capture
    it could not act on a capture reused from an earlier run, so recovering the
    size of a five-hour crawl would mean crawling the charity's site again.
    Placed after integration it would be rewriting the FFC-EX repo rather than
    the capture, and the publishable-size gate would already have failed."""
    steps = load_workflow(WORKFLOW)["jobs"]["convert"]["steps"]
    names = [str(s.get("name", "")) for s in steps]
    def only(substring: str) -> int:
        """The index of the one step matching `substring`.

        Indexing a comprehension would raise IndexError on a renamed step, which
        names neither the step that vanished nor the steps that exist. It also
        cannot tell "no match" from "two matches" -- and a second match here
        would mean the ordering this test asserts is ambiguous."""
        hits = [i for i, n in enumerate(names) if substring.lower() in n.lower()]
        assert len(hits) == 1, f"expected exactly one step matching {substring!r}, got {hits}: {names}"
        return hits[0]

    dedupe = only("duplicate assets")
    capture = only("Capture the live WordPress site")
    reuse = only("Reuse the capture")
    integrate = only("Integrate the capture")
    gate = only("must be publishable")
    assert capture < dedupe, (capture, dedupe, names)
    assert reuse < dedupe, (reuse, dedupe, names)
    assert dedupe < integrate < gate, (dedupe, integrate, gate, names)


def test_the_dedupe_step_reads_the_same_directory_both_earlier_steps_write():
    """A capture and a reused capture converge on one path. If this step named a
    different one it would silently dedupe nothing, and the only symptom would
    be a size gate that still fails -- which looks like 'the fix did not help'
    rather than 'the fix never ran'."""
    run = step_run(WORKFLOW, "convert", "duplicate assets")
    assert '--site "$RUNNER_TEMP/capture/site"' in run, run


def test_the_dedupe_self_test_gates_every_later_job():
    """This script DELETES files from the capture. It does not get to run
    against a charity's site without its own tests having passed first."""
    run = step_run(WORKFLOW, "resolve", "Offline self-tests")
    assert "node scripts/dedupe-capture-assets.mjs --self-test" in run, run


def test_dedupe_deletes_only_after_the_rewrite_has_been_verified():
    """The safety property, and the one worth a test rather than a comment.
    Delete-then-rewrite turns a blind spot in the rewrite into 404s on a
    charity's live site; rewrite-then-verify-then-delete turns the same blind
    spot into a failed step over a tree that is still publishable, because
    every reference already points at a canonical file that exists."""
    src = _dedupe_script_text()
    rewrite = src.index("const { filesChanged, refsRewritten } = rewriteReferences(")
    verify = src.index("const stale = findStaleReferences(")
    delete = src.index("rmSync(abs, { force: true })")
    assert rewrite < verify < delete, (rewrite, verify, delete)
    # ...and the verification must ABORT rather than warn.
    between = src[verify:delete]
    assert "if (stale.length)" in between, between
    assert "return {" in between, between


def test_dedupe_collapses_a_real_duplicate_and_repoints_its_references():
    """Exercised end to end against a real tree rather than asserted about the
    source, because the failure that matters here is a file deleted while some
    reference still names it -- which no reading of the code can rule out."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        assets = root / "_ffc-assets" / "h" / "up"
        assets.mkdir(parents=True)
        body = b"VIDEO" * 2000
        (assets / "Characters.mp4").write_bytes(body)
        (assets / "Characters__1.mp4").write_bytes(body)
        (root / "index.html").write_text(
            '<video src="./_ffc-assets/h/up/Characters__1.mp4"></video>',
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                "node",
                str(REPO_ROOT / "scripts" / "dedupe-capture-assets.mjs"),
                "--site",
                forward_slashes(str(root)),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child_env(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 0, out
        assert "1 duplicate asset(s) collapsed" in out, out

        assert (assets / "Characters.mp4").exists()
        assert not (assets / "Characters__1.mp4").exists()
        assert (
            root / "index.html"
        ).read_text(encoding="utf-8") == '<video src="./_ffc-assets/h/up/Characters.mp4"></video>'


def test_dedupe_leaves_same_size_different_content_files_alone():
    """Grouping by size is an optimization, not the decision. If a same-size
    pair were collapsed without comparing bytes, this pass would quietly serve
    one charity's document in place of another."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        assets = root / "_ffc-assets" / "h"
        assets.mkdir(parents=True)
        (assets / "a.pdf").write_bytes(b"A" * 4096)
        (assets / "b.pdf").write_bytes(b"B" * 4096)
        (root / "index.html").write_text(
            '<a href="./_ffc-assets/h/a.pdf">a</a><a href="./_ffc-assets/h/b.pdf">b</a>',
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                "node",
                str(REPO_ROOT / "scripts" / "dedupe-capture-assets.mjs"),
                "--site",
                forward_slashes(str(root)),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child_env(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 0, out
        assert "no byte-identical assets found" in out, out
        assert (assets / "a.pdf").exists() and (assets / "b.pdf").exists()


def test_the_heal_step_runs_after_the_dedupe_and_before_integration():
    """Order is the whole design. The dedupe RENAMES assets, and this pass
    exists to repair references a rename left behind, so running it first would
    measure a tree the next step is about to change -- and it would report a
    clean bill of health for exactly the damage it is there to find."""
    steps = load_workflow(WORKFLOW)["jobs"]["convert"]["steps"]
    names = [str(s.get("name", "")) for s in steps]

    def only(substring: str) -> int:
        hits = [i for i, n in enumerate(names) if substring.lower() in n.lower()]
        assert len(hits) == 1, f"expected exactly one step matching {substring!r}, got {hits}: {names}"
        return hits[0]

    heal = only("assets the capture no longer has")
    dedupe = only("duplicate assets")
    integrate = only("Integrate the capture")
    assert dedupe < heal < integrate, (dedupe, heal, integrate, names)


def test_the_heal_step_reads_the_same_directory_both_earlier_steps_write():
    """A capture and a reused capture converge on one path. Naming a different
    one would repair nothing and say so in a way that reads like 'there was
    nothing to repair'."""
    run = step_run(WORKFLOW, "convert", "assets the capture no longer has")
    assert '--site "$RUNNER_TEMP/capture/site"' in run, run


def test_the_heal_self_test_gates_every_later_job():
    """This script rewrites a charity's markup. It does not get to run against
    their capture without its own tests having passed first."""
    run = step_run(WORKFLOW, "resolve", "Offline self-tests")
    assert "node scripts/heal-missing-asset-refs.mjs --self-test" in run, run


def test_heal_repoints_a_missing_reference_and_never_invents_one():
    """End to end against a real tree, because the two failures that matter --
    repointing at a file that is not there, and rewriting a reference that was
    fine -- are both invisible to a reading of the source.

    The unresolvable case is asserted in the same tree as the resolvable one on
    purpose: a pass that heals nothing would also leave `vanished.png` alone,
    so neither half is evidence without the other."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        assets = root / "_ffc-assets" / "pub.x.org" / "u"
        assets.mkdir(parents=True)
        (assets / "books.jpeg").write_bytes(b"BOOKS")
        (root / "index.html").write_text(
            '<img src="/_ffc-assets/i0.wp.com/pub.x.org/u/books__fit-1280-2C858-ssl-1.jpeg">'
            '<img src="/_ffc-assets/pub.x.org/u/vanished.png">',
            encoding="utf-8",
        )

        proc = subprocess.run(
            [
                "node",
                str(REPO_ROOT / "scripts" / "heal-missing-asset-refs.mjs"),
                "--site",
                forward_slashes(str(root)),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child_env(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 0, out

        html = (root / "index.html").read_text(encoding="utf-8")
        assert '/_ffc-assets/pub.x.org/u/books.jpeg"' in html, html
        assert "i0.wp.com" not in html, html
        assert '/_ffc-assets/pub.x.org/u/vanished.png' in html, html
        assert "pub.x.org/u/vanished.png" in out, out


def test_heal_cannot_fail_the_run_on_a_reference_it_could_not_repair():
    """Deliberate, and the decision most worth pinning. This pass scans EVERY
    text file in the capture, including pages nothing links to; the
    self-containment gate loads real pages in a real browser and so speaks only
    about references a visitor can reach. Exiting non-zero here would let an
    unreachable stray halt a charity's migration on the gate's behalf, without
    the gate ever having judged it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "_ffc-assets").mkdir()
        (root / "orphan.html").write_text(
            '<img src="/_ffc-assets/x.org/nothing-has-this.png">', encoding="utf-8"
        )

        proc = subprocess.run(
            [
                "node",
                str(REPO_ROOT / "scripts" / "heal-missing-asset-refs.mjs"),
                "--site",
                forward_slashes(str(root)),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child_env(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 0, out
        assert "could NOT be resolved" in out, out
        assert "x.org/nothing-has-this.png" in out, out


def test_heal_refuses_a_reference_that_traverses_out_of_the_assets_dir():
    """Copilot's finding on #1355. The reference character class admits `.` and
    `-`, so it admits `..` as a whole segment -- and `join(assetsRoot, ...)`
    normalises the traversal away, so a candidate could be probed, and a
    rewrite written back into a charity's markup, against a path outside the
    capture. Asserted end to end because the guard has to hold in the scanner,
    not merely in a helper someone could stop calling."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "_ffc-assets" / "x.org").mkdir(parents=True)
        (root / "_ffc-assets" / "x.org" / "a.png").write_bytes(b"A")
        (root / "outside.png").write_bytes(b"OUTSIDE")
        before = (
            '<img src="/_ffc-assets/../outside.png">'
            '<img src="/_ffc-assets/x.org/./a.png">'
            '<img src="/_ffc-assets/x.org/a__v2.png">'
        )
        (root / "index.html").write_text(before, encoding="utf-8")

        proc = subprocess.run(
            [
                "node",
                str(REPO_ROOT / "scripts" / "heal-missing-asset-refs.mjs"),
                "--site",
                forward_slashes(str(root)),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child_env(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 0, out

        html = (root / "index.html").read_text(encoding="utf-8")
        # The two traversal forms are untouched and never reported either way.
        assert "/_ffc-assets/../outside.png" in html, html
        assert "/_ffc-assets/x.org/./a.png" in html, html
        assert "outside.png" not in out, out
        # ...and the ordinary reference beside them is still healed, so this is
        # not passing merely because the whole pass did nothing.
        assert '/_ffc-assets/x.org/a.png"' in html, html
        assert "a__v2.png" not in html.replace("a__v2.png.bak", ""), html


def test_the_second_heal_runs_after_conversion_and_before_the_size_gate():
    """Two passes, and the second is the one that judges what ships. Run
    35733489308 proved the first is not enough on its own: the capture pass
    repaired 18 of 23 broken references and the gate still 404'd on one it had
    never seen, because that reference does not exist as a literal in the
    capture -- the conversion writes it. Asserted in BOTH jobs, because
    `deliver` re-runs the conversion and is what actually pushes."""
    wf = load_workflow(WORKFLOW)
    for job in ("convert", "deliver"):
        names = [str(s.get("name", "")) for s in wf["jobs"][job]["steps"]]

        def only(substring: str) -> int:
            hits = [i for i, n in enumerate(names) if substring.lower() in n.lower()]
            assert len(hits) == 1, f"{job}: expected one step matching {substring!r}, got {hits}: {names}"
            return hits[0]

        convert = only("Convert the capture into real app routes")
        second_heal = only("conversion left pointing at nothing")
        size_gate = only("must be publishable")
        assert convert < second_heal < size_gate, (job, convert, second_heal, size_gate)


def test_the_second_heal_resolves_against_public_and_scans_the_generated_routes():
    """The assets and the references live in DIFFERENT directories once the
    capture is integrated. Pointing --site at the repo root would look for
    `_ffc-assets` where there is none and report a clean tree; omitting
    `--scan ffc-ex/src` would never read the generated routes. Either mistake
    is silent and reassuring."""
    for job in ("convert", "deliver"):
        run = step_run(WORKFLOW, job, "conversion left pointing at nothing")
        assert "--site ffc-ex/public" in run, (job, run)
        assert "--scan ffc-ex/public" in run, (job, run)
        assert "--scan ffc-ex/src" in run, (job, run)


def test_heal_walks_a_next_checkout_without_touching_node_modules():
    """This pass REWRITES what it walks, and the second invocation points it at
    a Next.js checkout. Walking node_modules there would be slow and dangerous;
    walking `out/` would rewrite build output, which the next build discards --
    making the pass look effective while the source stayed broken."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "public" / "_ffc-assets" / "x.org").mkdir(parents=True)
        (root / "public" / "_ffc-assets" / "x.org" / "hero.jpg").write_bytes(b"H")
        (root / "src").mkdir()
        (root / "src" / "page.tsx").write_text(
            'const a = "/_ffc-assets/x.org/hero__fit-9.jpg";', encoding="utf-8"
        )
        # Inside src/, which IS scanned. Placed beside it they would be skipped
        # for being out of range rather than by the guard, and the assertion
        # below would hold with SKIP_DIRS deleted.
        for skipped in ("node_modules", "out", ".next"):
            (root / "src" / skipped).mkdir()
            (root / "src" / skipped / "f.js").write_text(
                'const a = "/_ffc-assets/x.org/hero__fit-9.jpg";', encoding="utf-8"
            )

        proc = subprocess.run(
            [
                "node",
                str(REPO_ROOT / "scripts" / "heal-missing-asset-refs.mjs"),
                "--site",
                forward_slashes(str(root / "public")),
                "--scan",
                forward_slashes(str(root / "public")),
                "--scan",
                forward_slashes(str(root / "src")),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=child_env(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        assert proc.returncode == 0, out

        assert '"/_ffc-assets/x.org/hero.jpg"' in (root / "src" / "page.tsx").read_text(
            encoding="utf-8"
        ), out
        for skipped in ("node_modules", "out", ".next"):
            assert "hero__fit-9.jpg" in (root / "src" / skipped / "f.js").read_text(
                encoding="utf-8"
            ), skipped


def test_heal_self_tests_cover_the_escaped_and_unresolvable_cases():
    """A source-text assertion cannot tell a live case from a deleted one, so
    this runs the self-test and requires the cases by name. The escaped
    spelling is the one that matters most: WordPress inlines JSON inside
    <script>, where every slash arrives as `\\/`, and a plain-literal matcher
    reads such a document as containing no references at all -- silently, and
    in the reassuring direction."""
    proc = subprocess.run(
        ["node", str(REPO_ROOT / "scripts" / "heal-missing-asset-refs.mjs"), "--self-test"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env(),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out[-2000:]
    for name in (
        "referencesIn finds a JSON-escaped reference",
        "the escaped spelling is repointed too",
        "an unresolvable reference is left exactly as it was",
        "a prefix-sharing neighbour is not rewritten",
        "nothing is ever deleted",
        "referencesIn drops a reference that traverses out of the assets dir",
        "a reference in a generated .tsx route is healed against public/_ffc-assets",
        "node_modules is never walked, let alone rewritten",
    ):
        assert f"PASS {name}" in out, (name, out[-2000:])


# Built HERE, at the end of the module, and not one line earlier. This is a
# snapshot of `globals()` taken where it appears, so a roster placed mid-file
# silently omits every test defined below it -- this module defined 108 and ran
# 97 that way, reporting a clean green over a suite 11 tests smaller than the
# one in the file. Nothing in the module's own output can show that; only
# run_all.py's roster guard catches it, as "defines N but reported M" (L194).
# Keep this line last.
TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    failures = []
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append((t.__name__, exc))
            print(f"  FAIL {t.__name__}: {exc}")
    if failures:
        sys.exit(1)
