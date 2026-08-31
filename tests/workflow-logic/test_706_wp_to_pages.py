"""Unit tests for 706's input-resolution step and its job wiring.

706 is the only workflow in the repo that captures a live third-party site AND
writes the result into another repository, so three properties are pinned here
rather than left to review.

1. Every input that becomes a network call, a filesystem path, or published
   markup is validated in `resolve` before any of it is used. The natural way
   to type several of these is wrong in a way that fails late and unhelpfully.

2. The offline self-tests of all four scripts gate every later job. A
   classification regression must stop the run before it touches a real
   charity's website, not after.

3. Only `deliver` is gated, and only `deliver` writes. If the gate ever drifts
   onto `convert`, a read-only dry conversion starts requiring a human; if it
   ever drifts OFF `deliver`, an unapproved run opens a PR on another repo.
   Both are silent until someone notices.
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


# --- job wiring -------------------------------------------------------------


def test_self_tests_gate_every_later_job():
    """All four scripts decide either what gets fetched from a live site or
    what gets written into published markup. The gate lives in `resolve`, which
    both other jobs need, so a regression cannot reach the network."""
    run = step_run(WORKFLOW, "resolve", "Offline self-tests (gate every later job)")
    for script in (
        "capture-wordpress-api.mjs",
        "replace-forms-with-mailto.mjs",
        "integrate-clone-into-nextjs.mjs",
        "verify-no-legacy.mjs",
    ):
        assert f"{script} --self-test" in run, f"{script} not gated:\n{run}"

    wf = load_workflow(WORKFLOW)
    assert wf["jobs"]["convert"]["needs"] == "resolve", wf["jobs"]["convert"].get("needs")
    assert "resolve" in wf["jobs"]["deliver"]["needs"], wf["jobs"]["deliver"].get("needs")


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
            RUNNER_TEMP=str(tdp),
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
