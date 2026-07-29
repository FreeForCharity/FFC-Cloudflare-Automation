"""Unit tests for the 741 fleet security-audit coverage decision logic.

The workflow's github-script step `require`s scripts/fleet-audit-coverage-lib.js,
so exercising that module directly tests the shipped logic: which repos count as
a coverage gap (a Node app missing either half of the detection pair) versus the
benign buckets (`notNode` repos with no dependency tree, `unreadable` fetch
failures) that must NOT raise the rolling issue.

The parsing helpers are tested against the *real* canonical files rather than
hand-written fixtures where possible, because the failure this guards against is
a fleet repo silently reading as covered. A shape test guards the workflow
wiring itself.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "fleet-audit-coverage-lib.js"
WF_FILE = "741-fleet-security-audit-coverage.yml"


def _node(expr_body: str, *argv: str) -> object:
    code = f"const l=require({json.dumps(str(LIB))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def analyze(entries: list) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify(l.analyze(JSON.parse(process.argv[1]))));",
        json.dumps(entries),
    )


def render(analysis: dict, ts: str) -> str:
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.renderBody(JSON.parse(process.argv[1]), process.argv[2])));",
        json.dumps(analysis),
        ts,
    )


def extract_cron(raw: str):
    return _node(
        "process.stdout.write(JSON.stringify(l.extractCron(process.argv[1])));", raw
    )


def has_audit_script(raw: str) -> bool:
    return _node(
        "process.stdout.write(JSON.stringify(l.hasAuditScript(process.argv[1])));", raw
    )


def _covered(repo: str, cron: str = "17 6 * * *") -> dict:
    return {"repo": repo, "hasPackageJson": True, "hasWorkflow": True,
            "hasScript": True, "cron": cron}


# --- classification --------------------------------------------------------

def test_fully_covered_fleet_is_no_gap():
    r = analyze([_covered("FreeForCharity/FFC-EX-one.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-two.org", "23 6 * * *")])
    assert r["hasGap"] is False, r
    assert [c["repo"] for c in r["covered"]] == [
        "FreeForCharity/FFC-EX-one.org",
        "FreeForCharity/FFC-EX-two.org",
    ], r
    assert r["nodeRepos"] == 2, r


def test_neither_half_is_uncovered():
    # The #838 population: a Node app with no detection of any kind.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-silent.org", "hasPackageJson": True,
                  "hasWorkflow": False, "hasScript": False}])
    assert r["hasGap"] is True, r
    assert r["uncovered"] == ["FreeForCharity/FFC-EX-silent.org"], r
    assert r["partial"] == [] and r["covered"] == [], r


def test_workflow_without_script_is_partial_not_covered():
    # Fails on every run with "Missing script: audit:high" — looks wired, reports
    # nothing useful. Must not be counted as covered.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-half.org", "hasPackageJson": True,
                  "hasWorkflow": True, "hasScript": False}])
    assert r["hasGap"] is True, r
    assert r["partial"] == [{"repo": "FreeForCharity/FFC-EX-half.org",
                             "hasWorkflow": True, "hasScript": False}], r
    assert r["covered"] == [], r


def test_script_without_workflow_is_partial_not_covered():
    # Never runs at all — the more dangerous half, since the script's presence is
    # what a hand sweep tends to grep for.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-half2.org", "hasPackageJson": True,
                  "hasWorkflow": False, "hasScript": True}])
    assert r["hasGap"] is True, r
    assert r["partial"] == [{"repo": "FreeForCharity/FFC-EX-half2.org",
                             "hasWorkflow": False, "hasScript": True}], r


def test_no_package_json_is_out_of_scope_not_a_gap():
    # The 18 static/placeholder FFC-EX repos have no dependency tree; auditing
    # them would only manufacture red (#838).
    r = analyze([{"repo": "FreeForCharity/FFC-EX-static.org", "hasPackageJson": False},
                 {"repo": "FreeForCharity/FFC-EX-static2.org"}])  # key absent entirely
    assert r["hasGap"] is False, r
    assert sorted(r["notNode"]) == [
        "FreeForCharity/FFC-EX-static.org",
        "FreeForCharity/FFC-EX-static2.org",
    ], r
    assert r["nodeRepos"] == 0, r


def test_fetch_error_is_unreadable_not_a_gap():
    # Coverage cannot be asserted either way, so guessing "uncovered" would put a
    # repo on a remediation list it may not belong on.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403 rate limited"}])
    assert r["hasGap"] is False, r
    assert r["unreadable"] == [
        {"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403 rate limited"}
    ], r
    assert r["nodeRepos"] == 0, r


def test_error_wins_over_partial_flags():
    # A record carrying both an error and stale flags must be reported as
    # unreadable — the flags are meaningless if the fetch failed.
    r = analyze([{"repo": "FreeForCharity/FFC-EX-x.org", "hasPackageJson": True,
                  "hasWorkflow": True, "hasScript": False, "error": "boom"}])
    assert r["hasGap"] is False, r
    assert len(r["unreadable"]) == 1 and r["partial"] == [], r


def test_mixed_fleet_counts_every_bucket():
    r = analyze([
        _covered("FreeForCharity/FFC-EX-a.org"),
        {"repo": "FreeForCharity/FFC-EX-b.org", "hasPackageJson": True,
         "hasWorkflow": True, "hasScript": False},
        {"repo": "FreeForCharity/FFC-EX-c.org", "hasPackageJson": True,
         "hasWorkflow": False, "hasScript": False},
        {"repo": "FreeForCharity/FFC-EX-d.org", "hasPackageJson": False},
        {"repo": "FreeForCharity/FFC-EX-e.org", "error": "nope"},
    ])
    assert r["hasGap"] is True, r
    assert len(r["covered"]) == 1 and len(r["partial"]) == 1
    assert len(r["uncovered"]) == 1 and len(r["notNode"]) == 1
    assert len(r["unreadable"]) == 1
    assert r["nodeRepos"] == 3, r  # covered + partial + uncovered only


def test_empty_fleet_is_no_gap():
    r = analyze([])
    assert r["hasGap"] is False, r
    assert r["nodeRepos"] == 0 and r["covered"] == [], r


# --- cron distribution -----------------------------------------------------

def test_single_cron_across_fleet_reads_as_not_staggered():
    # The state #838 warns about: 34 more repos pointed at `17 6 * * *`.
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-b.org", "17 6 * * *")])
    assert r["crons"]["staggered"] is False, r["crons"]
    assert r["crons"]["distinct"] == 1, r["crons"]
    assert r["crons"]["groups"][0]["count"] == 2, r["crons"]


def test_distinct_crons_read_as_staggered():
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-b.org", "42 6 * * *")])
    assert r["crons"]["staggered"] is True, r["crons"]
    assert r["crons"]["distinct"] == 2, r["crons"]


def test_a_single_covered_repo_is_not_reported_as_unstaggered():
    # One repo cannot collide with itself; flagging it would be noise.
    r = analyze([_covered("FreeForCharity/FFC-EX-only.org", "17 6 * * *")])
    assert r["crons"]["staggered"] is True, r["crons"]


def test_cron_groups_sorted_largest_pile_up_first():
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-b.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-c.org", "42 6 * * *")])
    groups = r["crons"]["groups"]
    assert groups[0]["cron"] == "17 6 * * *" and groups[0]["count"] == 2, groups
    assert groups[1]["cron"] == "42 6 * * *", groups


def test_cron_distribution_never_sets_has_gap():
    # Scheduling hygiene and missing detection are different problems; folding
    # them together would blur what a red rolling issue means, and "no two repos
    # share a minute" is a rule 44 repos could never satisfy.
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-b.org", "17 6 * * *")])
    assert r["hasGap"] is False, r


def test_dispatch_only_audit_copy_has_unknown_cron():
    r = analyze([{"repo": "FreeForCharity/FFC-EX-a.org", "hasPackageJson": True,
                  "hasWorkflow": True, "hasScript": True, "cron": None}])
    assert r["hasGap"] is False, r
    assert r["crons"]["groups"][0]["cron"] == "unknown", r["crons"]
    assert r["crons"]["distinct"] == 0, r["crons"]


def _unknown_cron(repo: str) -> dict:
    return {"repo": repo, "hasPackageJson": True, "hasWorkflow": True,
            "hasScript": True, "cron": None}


def test_all_unknown_crons_do_not_read_as_unstaggered():
    # Regression (Copilot review on #840): every repo landing in the `unknown`
    # group is "nothing is known about the schedule", NOT "everything shares one
    # schedule". Testing the whole covered set would make distinct==0 satisfy
    # `distinct <= 1` and fire the warning. Not-measured must never render as
    # measured-and-bad.
    r = analyze([_unknown_cron("FreeForCharity/FFC-EX-a.org"),
                 _unknown_cron("FreeForCharity/FFC-EX-b.org")])
    assert r["crons"]["staggered"] is True, r["crons"]
    assert r["crons"]["knownCount"] == 0, r["crons"]


def test_one_known_cron_alongside_unknowns_is_not_unstaggered():
    # A single known schedule cannot collide with anything, and the unknowns
    # carry no evidence either way.
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _unknown_cron("FreeForCharity/FFC-EX-b.org")])
    assert r["crons"]["staggered"] is True, r["crons"]
    assert r["crons"]["knownCount"] == 1, r["crons"]


def test_two_known_identical_crons_still_flag_despite_unknowns():
    # The warning must survive unknowns in the same fleet — otherwise a single
    # dispatch-only repo would mask a real pile-up.
    r = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                 _covered("FreeForCharity/FFC-EX-b.org", "17 6 * * *"),
                 _unknown_cron("FreeForCharity/FFC-EX-c.org")])
    assert r["crons"]["staggered"] is False, r["crons"]
    assert r["crons"]["knownCount"] == 2, r["crons"]


def test_render_omits_stagger_warning_when_every_cron_is_unknown():
    analysis = analyze([_unknown_cron("FreeForCharity/FFC-EX-a.org"),
                        _unknown_cron("FreeForCharity/FFC-EX-b.org")])
    body = render(analysis, "t")
    assert "never staggered" not in body, body


# --- parsing helpers -------------------------------------------------------

def test_extract_cron_from_the_canonical_workflow_shape():
    raw = "on:\n  schedule:\n    - cron: '17 6 * * *' # daily, 06:17 UTC\n"
    assert extract_cron(raw) == "17 6 * * *"


def test_extract_cron_handles_double_quotes_and_no_quotes():
    assert extract_cron('    - cron: "5 4 * * 0"\n') == "5 4 * * 0"
    assert extract_cron("    - cron: 5 4 * * 0\n") == "5 4 * * 0"


def test_extract_cron_returns_none_without_a_schedule():
    assert extract_cron("on:\n  workflow_dispatch:\n") is None
    assert extract_cron("") is None


def test_has_audit_script_requires_a_real_script_entry():
    assert has_audit_script(
        '{"scripts": {"audit:high": "npm audit --omit=dev --audit-level=high"}}'
    ) is True
    assert has_audit_script('{"scripts": {"build": "next build"}}') is False
    assert has_audit_script('{}') is False


def test_has_audit_script_does_not_match_a_bare_mention():
    # A grep would call this covered; it is not. This is the false-negative that
    # would leave a repo unmonitored while reading as done.
    assert has_audit_script(
        '{"description": "run audit:high before release", "scripts": {"build": "x"}}'
    ) is False


def test_has_audit_script_survives_malformed_json():
    assert has_audit_script("{not json") is False
    assert has_audit_script("") is False


def test_has_audit_script_rejects_a_non_string_value():
    assert has_audit_script('{"scripts": {"audit:high": null}}') is False


# --- report rendering ------------------------------------------------------

def test_render_contains_marker_and_actionable_checklist():
    analysis = analyze([
        _covered("FreeForCharity/FFC-EX-good.org"),
        {"repo": "FreeForCharity/FFC-EX-silent.org", "hasPackageJson": True,
         "hasWorkflow": False, "hasScript": False},
    ])
    body = render(analysis, "2026-07-25T00:00:00Z")
    assert "<!-- fleet-security-audit-coverage -->" in body, body
    assert "- [ ] FreeForCharity/FFC-EX-silent.org" in body, body
    assert "Uncovered — no detection at all (1)" in body, body
    assert "Covered (1)" in body, body
    assert "Refs #838" in body, body


def test_render_partial_table_explains_the_effect():
    analysis = analyze([
        {"repo": "FreeForCharity/FFC-EX-wf.org", "hasPackageJson": True,
         "hasWorkflow": True, "hasScript": False},
        {"repo": "FreeForCharity/FFC-EX-sc.org", "hasPackageJson": True,
         "hasWorkflow": False, "hasScript": True},
    ])
    body = render(analysis, "t")
    assert "workflow fails every run (missing script)" in body, body
    assert "script never runs (no workflow)" in body, body


def test_render_flags_an_unstaggered_fleet():
    analysis = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                        _covered("FreeForCharity/FFC-EX-b.org", "17 6 * * *")])
    body = render(analysis, "t")
    assert "never staggered" in body, body
    assert "Schedule distribution" in body, body


def test_render_omits_the_stagger_warning_when_distributed():
    analysis = analyze([_covered("FreeForCharity/FFC-EX-a.org", "17 6 * * *"),
                        _covered("FreeForCharity/FFC-EX-b.org", "42 6 * * *")])
    body = render(analysis, "t")
    assert "never staggered" not in body, body


def test_render_out_of_scope_and_unreadable_sections():
    analysis = analyze([
        {"repo": "FreeForCharity/FFC-EX-static.org", "hasPackageJson": False},
        {"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403"},
    ])
    body = render(analysis, "t")
    assert "No package.json (1)" in body, body
    assert "Unreadable (1)" in body, body
    assert "HTTP 403" in body, body


# --- workflow wiring shape -------------------------------------------------

def test_workflow_requires_lib_and_is_read_only():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "scripts/fleet-audit-coverage-lib.js" in raw, "workflow must require the shipped lib"
    wf = load_workflow(WF_FILE)
    perms = wf["permissions"]
    assert perms.get("contents") == "read", perms
    assert perms.get("issues") == "write", perms
    # No environment gate on any job — an alerter must never be blocked by a gate.
    for name, job in wf["jobs"].items():
        assert "environment" not in job, f"{name} must not use an environment gate"


def test_workflow_has_schedule_and_dispatch():
    wf = load_workflow(WF_FILE)
    on = wf.get(True, wf.get("on"))
    assert "schedule" in on, on
    assert "workflow_dispatch" in on, on


def test_workflow_does_not_collide_with_738_fleet_sweep():
    # Both read every fleet repo from the same shared REST budget; running them
    # at the same minute is the one avoidable contention.
    def cron_of(f):
        wf = load_workflow(f)
        on = wf.get(True, wf.get("on"))
        return [s["cron"] for s in on["schedule"]]

    mine = cron_of(WF_FILE)
    theirs = cron_of("738-fleet-smoke-engine-drift-audit.yml")
    assert set(mine).isdisjoint(set(theirs)), (mine, theirs)


def test_741_header_never_wraps_mid_hyphenated_word():
    """The catalog generator joins header comment lines with a space, so a word
    split across the wrap ships to the PUBLIC catalog (ffcadmin.org/automation/)
    as `dependency- vulnerability`. Caught on #840 by review, and the mechanical
    cause — a header line ending in a hyphen — is what this asserts.

    Scoped to 741 rather than repo-wide: 727's description carries the same
    pattern (`squash- or`) and predates this PR, so a repo-wide guard would fail
    on unrelated work. Widening it is a worthwhile follow-up.
    """
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    header = [ln for ln in raw.splitlines() if ln.startswith("#")]
    offenders = [ln for ln in header if ln.rstrip().endswith("-")]
    assert not offenders, offenders

    catalog = json.loads((REPO_ROOT / "docs" / "workflow-catalog.json").read_text(encoding="utf-8"))
    entries = catalog["workflows"] if isinstance(catalog, dict) else catalog
    mine = next(w for w in entries if w.get("number") == 741)
    import re as _re
    assert not _re.search(r"\w+- \w+", mine["description"]), mine["description"]


def test_workflow_header_does_not_claim_zero_external_apis():
    """The header text ships verbatim to the PUBLIC catalog (ffcadmin.org/automation/).

    `npm ci --dry-run` reads the npm registry, so the old "No external API" line
    became false the moment the lockfile stage landed — a safety claim drifting
    out of sync with what the job does (Copilot, #910).
    """
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    header = raw.split("on:", 1)[0]
    assert "No external API" not in header, header
    assert "npm registry" in header, header


def test_workflow_never_writes_to_a_fleet_repo():
    # Remediation belongs to #822; this workflow only measures. The single write
    # is the rolling issue in this repo.
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    for forbidden in ["create_or_update_file", "git push", "peter-evans/create-pull-request"]:
        assert forbidden not in raw, f"741 must not write to fleet repos ({forbidden})"


# --- lockfile resolution (#889, ledger L06) --------------------------------
#
# The strings below are VERBATIM `npm ci --dry-run --ignore-scripts` output,
# captured from npm 10.9.7 on 2026-07-29 against purpose-built fixtures — not
# transcribed from npm's documentation. That matters: this classifier's whole
# job is to read npm's mind, and a fixture written from memory would test the
# classifier against the same guess it encodes.

NPM_OUT_OF_SYNC_MISSING = """npm error code EUSAGE
npm error
npm error `npm ci` can only install packages when your package.json and package-lock.json or npm-shrinkwrap.json are in sync. Please update your lock file with `npm install` before continuing.
npm error
npm error Missing: is-odd@3.0.1 from lock file
npm error Missing: is-number@6.0.0 from lock file
npm error
npm error A complete log of this run can be found in: /root/.npm/_logs/x-debug-0.log
"""

NPM_OUT_OF_SYNC_INVALID = """npm error code EUSAGE
npm error
npm error `npm ci` can only install packages when your package.json and package-lock.json or npm-shrinkwrap.json are in sync. Please update your lock file with `npm install` before continuing.
npm error
npm error Invalid: lock file's left-pad@1.3.0 does not satisfy left-pad@1.1.3
npm error
"""

NPM_NO_LOCKFILE = """npm error code EUSAGE
npm error
npm error The `npm ci` command can only install with an existing package-lock.json or
npm error npm-shrinkwrap.json with lockfileVersion >= 1. Run an install with npm@5 or
npm error later to generate a package-lock.json file, then try again.
"""

NPM_ETARGET = """npm error code ETARGET
npm error notarget No matching version found for left-pad@^1.9.9.
npm error notarget In most cases you or one of your dependencies are requesting
npm error notarget a package version that doesn't exist.
"""

NPM_REGISTRY_502 = """npm error code E502
npm error 502 Bad Gateway - GET https://registry.example/left-pad
"""

NPM_OFFLINE = """npm error code ENOTCACHED
npm error request to https://registry.example/is-odd failed: cache mode is 'only-if-cached' but no cached response is available.
"""

# A real in-sync pair, so the end-to-end test below drives actual npm rather
# than a fixture. Generated by `npm install --package-lock-only`; left-pad is
# used because it is tiny, and --dry-run never downloads it anyway.
FIXTURE_PACKAGE_JSON = json.dumps(
    {"name": "insync", "version": "1.0.0", "dependencies": {"left-pad": "1.3.0"}}
)
FIXTURE_LOCKFILE = json.dumps(
    {
        "name": "insync",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "requires": True,
        "packages": {
            "": {"name": "insync", "version": "1.0.0",
                 "dependencies": {"left-pad": "1.3.0"}},
            "node_modules/left-pad": {
                "version": "1.3.0",
                "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                "integrity": "sha512-XI5MPzVNApjAyhQzphX8BkmKsKUxD4LdyK24iZeQGinBN9yTQT3bFlCBy/aVx2HrNcqQGsdot8ghrjyrvMCoEA==",
            },
        },
    }
)


def classify(exit_code: int, output: str) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.classifyLockResolution(Number(process.argv[1]), process.argv[2])));",
        str(exit_code),
        output,
    )


def has_dependencies(raw: str) -> bool:
    return _node(
        "process.stdout.write(JSON.stringify(l.hasDependencies(process.argv[1])));", raw
    )


def _node_repo(repo: str, lock: dict | None, covered: bool = True) -> dict:
    e = {"repo": repo, "hasPackageJson": True, "hasWorkflow": covered,
         "hasScript": covered, "cron": "17 6 * * *" if covered else None}
    if lock is not None:
        e["lockfile"] = lock
    return e


def test_exit_zero_is_the_only_way_to_read_as_resolving():
    assert classify(0, "added 1 package in 300ms")["state"] == "resolves"


def test_real_npm_sync_failure_reads_as_out_of_sync_and_names_the_package():
    r = classify(1, NPM_OUT_OF_SYNC_MISSING)
    assert r["state"] == "out-of-sync", r
    # The detail is what makes the report actionable without opening the repo.
    assert "Missing: is-odd@3.0.1 from lock file" in r["detail"], r


def test_real_npm_invalid_version_reads_as_out_of_sync():
    r = classify(1, NPM_OUT_OF_SYNC_INVALID)
    assert r["state"] == "out-of-sync", r
    assert "does not satisfy left-pad@1.1.3" in r["detail"], r


def test_absent_lockfile_message_is_distinguished_from_drift():
    # Same EUSAGE code, different remedy — #889 asks for these to be separate
    # buckets, and the two messages differ only in wording.
    r = classify(1, NPM_NO_LOCKFILE)
    assert r["state"] == "missing", r


def test_unsatisfiable_range_is_unresolvable_not_out_of_sync():
    # `npm install` does NOT fix this one, so folding it into out-of-sync would
    # print the wrong remediation next to it.
    r = classify(1, NPM_ETARGET)
    assert r["state"] == "unresolvable", r
    assert "left-pad@^1.9.9" in r["detail"], r


def test_registry_failure_is_unverified_never_broken():
    # The finding this whole audit exists to prevent, one level up: a transient
    # 502 must not put a charity repo on a remediation list.
    r = classify(1, NPM_REGISTRY_502)
    assert r["state"] == "unverified", r
    assert "E502" in r["detail"], r


def test_offline_enotcached_is_unverified():
    # npm consults the registry precisely WHEN the lockfile does not satisfy
    # package.json, so a network-less runner turns every genuinely out-of-sync
    # repo into ENOTCACHED. Right answer, wrong reason, if read as out-of-sync.
    r = classify(1, NPM_OFFLINE)
    assert r["state"] == "unverified", r
    # Asserting the REASON, not just the state: an unrecognized failure also
    # lands on `unverified`, so a state-only assertion would pass even if the
    # classifier had stopped recognizing offline runs at all.
    assert r["detail"] == "registry/network failure (ENOTCACHED)", r


def test_timeout_is_unverified():
    assert classify(124, "")["state"] == "unverified"
    assert "timed out" in classify(124, "")["detail"]


def test_unrecognized_failure_is_unverified_and_carries_the_message():
    r = classify(1, "npm error code EACCES\nnpm error permission denied\n")
    assert r["state"] == "unverified", r
    assert "EACCES" in r["detail"] or "permission denied" in r["detail"], r


def test_npm9_err_prefix_still_parses():
    # npm 9 wrote `npm ERR!`; npm 10+ writes `npm error`. A runner image change
    # must not silently turn every verdict into "unverified".
    old = NPM_OUT_OF_SYNC_MISSING.replace("npm error", "npm ERR!")
    r = classify(1, old)
    assert r["state"] == "out-of-sync", r
    assert "is-odd@3.0.1" in r["detail"], r


def absent_lock(probes: list) -> dict:
    return _node(
        "process.stdout.write(JSON.stringify("
        "l.classifyAbsentLockfile(JSON.parse(process.argv[1]))));",
        json.dumps(probes),
    )


def test_no_lockfile_anywhere_is_missing():
    r = absent_lock([{"path": "pnpm-lock.yaml", "status": "ABSENT"},
                     {"path": "yarn.lock", "status": "ABSENT"}])
    assert r == {"state": "missing", "detail": "no package-lock.json in the repo"}, r


def test_a_found_alt_lockfile_is_not_a_finding():
    r = absent_lock([{"path": "pnpm-lock.yaml", "status": "OK"}])
    assert r == {"state": "other-lockfile", "detail": "pnpm-lock.yaml"}, r


def test_a_failed_alt_probe_is_unverified_not_missing():
    # Copilot's finding on #910. A rate-limited probe is not evidence that the
    # file is absent, and `missing` is a remediation finding — so believing a
    # failed probe would put a pnpm repo on a to-fix list on evidence that was
    # never collected. Same substitution this whole audit exists to catch.
    r = absent_lock([{"path": "pnpm-lock.yaml", "status": "ERROR:HTTP 403 rate limited"},
                     {"path": "yarn.lock", "status": "ABSENT"}])
    assert r["state"] == "unverified", r
    assert "pnpm-lock.yaml" in r["detail"], r


def test_a_confirmed_alt_lockfile_beats_a_failed_probe():
    # Knowing the answer makes the failed probe irrelevant; reporting
    # `unverified` here would discard evidence actually in hand.
    r = absent_lock([{"path": "pnpm-lock.yaml", "status": "ERROR:boom"},
                     {"path": "yarn.lock", "status": "OK"}])
    assert r == {"state": "other-lockfile", "detail": "yarn.lock"}, r


def test_absent_lockfile_classifier_handles_no_probes():
    assert absent_lock([])["state"] == "missing"


def test_workflow_routes_alt_probe_failures_through_the_library():
    # The bash must not decide this itself — the branch that would regress is
    # exactly the one that reads a failed probe as an absent file.
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "lib.classifyAbsentLockfile" in raw, raw[:0]


def test_has_dependencies_distinguishes_an_empty_tree():
    assert has_dependencies(FIXTURE_PACKAGE_JSON) is True
    assert has_dependencies('{"scripts": {"build": "next build"}}') is False
    assert has_dependencies('{"dependencies": {}}') is False
    assert has_dependencies('{"devDependencies": {"a": "1"}}') is True
    assert has_dependencies("{not json") is False


# --- lockfile buckets ------------------------------------------------------

def test_broken_lockfile_is_a_gap_even_when_the_repo_is_covered():
    # The finding #889 is about: coverage means the workflow is wired, not that
    # it can run. Both halves present + `npm ci` failing = a repo that reports
    # clean while scanning nothing.
    r = analyze([_node_repo("FreeForCharity/FFC-EX-a.org",
                            {"state": "out-of-sync", "detail": "Missing: x"})])
    assert r["hasGap"] is True, r
    assert len(r["covered"]) == 1, r
    assert r["lockfiles"]["broken"] == [
        {"repo": "FreeForCharity/FFC-EX-a.org", "state": "out-of-sync", "detail": "Missing: x"}
    ], r


def test_missing_lockfile_is_a_gap_and_a_separate_state():
    r = analyze([_node_repo("FreeForCharity/FFC-EX-a.org", {"state": "missing"})])
    assert r["hasGap"] is True, r
    assert r["lockfiles"]["broken"] == [
        {"repo": "FreeForCharity/FFC-EX-a.org", "state": "missing", "detail": ""}
    ], r


def test_resolving_lockfile_is_not_a_gap():
    r = analyze([_node_repo("FreeForCharity/FFC-EX-a.org", {"state": "resolves"})])
    assert r["hasGap"] is False, r
    assert r["lockfiles"]["resolves"] == ["FreeForCharity/FFC-EX-a.org"], r


def test_unverified_lockfile_never_sets_has_gap():
    r = analyze([_node_repo("FreeForCharity/FFC-EX-a.org",
                            {"state": "unverified", "detail": "registry/network failure (E502)"})])
    assert r["hasGap"] is False, r
    assert r["lockfiles"]["broken"] == [], r
    assert r["lockfiles"]["unverified"] == [
        {"repo": "FreeForCharity/FFC-EX-a.org", "state": "unverified",
         "detail": "registry/network failure (E502)"}
    ], r


def test_pnpm_repo_is_not_reported_as_a_missing_lockfile():
    # A pnpm repo is correctly without package-lock.json (#771); flagging it
    # would be a finding nobody can act on.
    r = analyze([_node_repo("FreeForCharity/FFC-EX-a.org",
                            {"state": "other-lockfile", "detail": "pnpm-lock.yaml"})])
    assert r["hasGap"] is False, r
    # Compared as a whole list rather than by index: a mutation that empties the
    # bucket must FAIL this test, not crash it with an IndexError. A crashed
    # module prints no FAIL line, which is ledger L07 — the shape where a broken
    # guard reads as a passing one.
    assert r["lockfiles"]["skipped"] == [
        {"repo": "FreeForCharity/FFC-EX-a.org", "state": "other-lockfile",
         "detail": "pnpm-lock.yaml"}
    ], r


def test_dependency_free_repo_is_not_a_broken_lockfile():
    r = analyze([_node_repo("FreeForCharity/FFC-EX-a.org", {"state": "no-dependencies"})])
    assert r["hasGap"] is False, r
    assert r["lockfiles"]["skipped"] == [
        {"repo": "FreeForCharity/FFC-EX-a.org", "state": "no-dependencies", "detail": ""}
    ], r


def test_a_repo_with_no_lockfile_record_reads_as_unverified_not_ok():
    # An unmeasured repo must never land in `resolves`. Substituting
    # not-measured for measured-and-fine is ledger L06 itself.
    r = analyze([_node_repo("FreeForCharity/FFC-EX-a.org", None)])
    assert r["lockfiles"]["resolves"] == [], r
    assert r["lockfiles"]["unverified"] == [
        {"repo": "FreeForCharity/FFC-EX-a.org", "state": "not-measured", "detail": "not measured"}
    ], r
    assert r["hasGap"] is False, r


def test_unknown_future_state_fails_benign():
    r = analyze([_node_repo("FreeForCharity/FFC-EX-a.org", {"state": "quantum"})])
    assert r["hasGap"] is False, r
    assert r["lockfiles"]["unverified"] == [
        {"repo": "FreeForCharity/FFC-EX-a.org", "state": "unverified",
         "detail": "unrecognized state 'quantum'"}
    ], r


def test_non_node_and_unreadable_repos_get_no_lockfile_record():
    r = analyze([{"repo": "FreeForCharity/FFC-EX-static.org", "hasPackageJson": False},
                 {"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403"}])
    locks = r["lockfiles"]
    assert locks["resolves"] == [] and locks["broken"] == [], locks
    assert locks["unverified"] == [] and locks["skipped"] == [], locks


def test_render_lists_broken_lockfiles_with_their_fix():
    analysis = analyze([
        _node_repo("FreeForCharity/FFC-EX-drift.org",
                   {"state": "out-of-sync", "detail": "Missing: is-odd@3.0.1 from lock file"}),
        _node_repo("FreeForCharity/FFC-EX-nolock.org", {"state": "missing"}),
    ])
    body = render(analysis, "t")
    assert "Lockfile does not resolve — the audit scans nothing (2)" in body, body
    assert "Missing: is-odd@3.0.1 from lock file" in body, body
    assert "`npm install` and commit the lockfile" in body, body
    assert "Refs #838, #889" in body, body


def test_render_reports_unverified_lockfiles_separately_from_broken_ones():
    analysis = analyze([
        _node_repo("FreeForCharity/FFC-EX-a.org", {"state": "unverified", "detail": "E502"}),
        _node_repo("FreeForCharity/FFC-EX-b.org",
                   {"state": "other-lockfile", "detail": "pnpm-lock.yaml"}),
    ])
    body = render(analysis, "t")
    assert "Lockfile unverified (1)" in body, body
    assert "Lockfile n/a (1)" in body, body
    assert "NOT asserted" in body, body


def test_render_states_the_real_auto_close_condition():
    # `hasGap` ignores unverified lockfiles, so the report must not promise that
    # a close means every lockfile resolved (Copilot, #910). A close condition
    # stated more strongly than the code implements is a documentation defect
    # with the same shape as the bug this workflow hunts.
    body = render(analyze([_node_repo("FreeForCharity/FFC-EX-a.org", {"state": "resolves"})]), "t")
    assert "no lockfile is broken" in body, body
    assert "Unverified lockfiles do not hold it open" in body, body
    assert "every lockfile resolves." not in body, body


# --- end-to-end against real npm -------------------------------------------

def test_real_npm_agrees_with_the_classifier_on_a_valid_pair():
    """Drive actual `npm ci --dry-run` rather than a fixture.

    A classifier tested only against captured strings proves it can read a
    transcript, not that npm still speaks that way. This case needs no registry
    (npm builds the tree from a lockfile that already satisfies package.json),
    so it is safe on an offline runner.
    """
    import shutil
    import tempfile

    if not shutil.which("npm"):
        return  # no npm on this host; the fixture tests still ran
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d)
        (work / "package.json").write_text(FIXTURE_PACKAGE_JSON, encoding="utf-8")
        (work / "package-lock.json").write_text(FIXTURE_LOCKFILE, encoding="utf-8")
        proc = subprocess.run(
            ["npm", "ci", "--dry-run", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=work, capture_output=True, text=True, timeout=180,
        )
        r = classify(proc.returncode, proc.stdout + proc.stderr)
    assert r["state"] == "resolves", (r, proc.returncode, proc.stdout, proc.stderr)


def test_real_npm_drift_never_reads_as_resolving():
    """The mutation of the case above: add a dependency the lockfile lacks.

    Asserted as "never `resolves`" rather than "always `out-of-sync`" because
    npm reaches the registry to answer this one — offline it says ENOTCACHED,
    which is correctly `unverified`. Both are right; `resolves` is the answer
    that would be a lie, and it is the one the test forbids.
    """
    import shutil
    import tempfile

    if not shutil.which("npm"):
        return
    drifted = json.dumps(
        {"name": "insync", "version": "1.0.0",
         "dependencies": {"left-pad": "1.3.0", "is-odd": "3.0.1"}}
    )
    with tempfile.TemporaryDirectory() as d:
        work = pathlib.Path(d)
        (work / "package.json").write_text(drifted, encoding="utf-8")
        (work / "package-lock.json").write_text(FIXTURE_LOCKFILE, encoding="utf-8")
        proc = subprocess.run(
            ["npm", "ci", "--dry-run", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=work, capture_output=True, text=True, timeout=180,
        )
        r = classify(proc.returncode, proc.stdout + proc.stderr)
    assert proc.returncode != 0, (proc.stdout, proc.stderr)
    assert r["state"] in ("out-of-sync", "unverified"), (r, proc.stdout, proc.stderr)
    assert r["state"] != "resolves", r


# --- workflow wiring for the lockfile stage --------------------------------

def test_workflow_runs_npm_safely_and_bounded():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(encoding="utf-8")
    assert "npm ci --dry-run" in raw, "the verdict must come from real npm"
    # --dry-run installs nothing and --ignore-scripts runs nothing, so a fleet
    # lockfile can never execute code inside this job.
    assert "--ignore-scripts" in raw, "must not run lifecycle scripts from fleet repos"
    assert "timeout 180 npm ci" in raw, "an npm hang must not hang the weekly audit"
    assert "lib.classifyLockResolution" in raw, "classification must come from the shipped lib"
    assert "lib.hasDependencies" in raw


def test_workflow_pins_the_npm_that_judges():
    # The classifier keys on npm's wording; which npm answers is a decision.
    wf = load_workflow(WF_FILE)
    steps = wf["jobs"]["audit"]["steps"]
    node_step = [s for s in steps if str(s.get("uses", "")).startswith("actions/setup-node")]
    assert node_step, steps
    assert str(node_step[0]["with"]["node-version"]) == "24", node_step


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
