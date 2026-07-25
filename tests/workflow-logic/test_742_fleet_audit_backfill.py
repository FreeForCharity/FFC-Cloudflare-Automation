"""Unit tests for the 742 fleet security-audit backfill decision logic.

Workflow 742 edits `package.json` in 34 charity repos unattended, so the tests
are weighted toward the two ways that can go wrong quietly:

  * a `partial` repo — one half of the detection pair written without the other,
    which looks wired and reports nothing (#840's review); and
  * a mangled `package.json` — a surgical text insertion at a mislocated offset
    can still produce valid JSON, so parsing the result is not evidence.

Both are asserted against the *shipped* modules
(scripts/fleet-audit-backfill-lib.js behind scripts/fleet-audit-backfill.js),
including end-to-end runs of the `apply` subcommand against real temp clones,
because the workflow calls that CLI and not the library directly.

Where the logic overlaps 741's, agreement between the two libraries is asserted
rather than assumed: a repo this backfill reports as covered must be one 741's
sweep also reads as covered, or the next sweep re-lists work that was done.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "fleet-audit-backfill-lib.js"
CLI = REPO_ROOT / "scripts" / "fleet-audit-backfill.js"
COVERAGE_LIB = REPO_ROOT / "scripts" / "fleet-audit-coverage-lib.js"
WF_FILE = "742-fleet-security-audit-backfill.yml"

AUDIT_CMD = "npm audit --omit=dev --audit-level=high"

# Verbatim copy of the canonical file this workflow rolls out
# (FFC-IN-FFC_Single_Page_Template/.github/workflows/security-audit.yml).
#
# A fixture rather than a read of the real file, because the canonical copy
# lives in ANOTHER repo and is not present in this repo's CI checkout. That
# makes the fixture a snapshot, so it cannot be the guard against the canonical
# file drifting — the workflow re-fetches and re-validates the real file at run
# time (twice: before the gate and again after it), and that is the authority.
# What the fixture is for is proving the renderer and the validator behave
# correctly on the real shape.
CANONICAL = """name: Security Audit

# Runs `npm audit` against production dependencies on a daily cadence and on
# pull requests that touch package files.

on:
  schedule:
    - cron: '17 6 * * *' # daily, 06:17 UTC
  pull_request:
    branches: ['main']
    paths:
      - 'package.json'
      - 'package-lock.json'
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  audit:
    name: npm audit (production)
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v6

      - name: Setup Node.js
        uses: actions/setup-node@v6
        with:
          node-version: '24'
          cache: 'npm'

      - name: Install (no scripts)
        run: npm ci --ignore-scripts

      - name: Audit high-severity production deps
        run: npm run audit:high
"""

# A realistically-shaped FFC template package.json (2-space indent, scripts
# block with several siblings).
PKG = """{
  "name": "ffc-charity-site",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev --turbopack",
    "build": "next build",
    "lint": "eslint .",
    "test": "vitest run"
  },
  "dependencies": {
    "next": "16.2.4",
    "react": "19.0.0"
  }
}
"""

# The 34 uncovered repos as measured by 741's first live sweep (#841). Used to
# exercise the stagger against the real names it will actually see rather than
# synthetic ones.
UNCOVERED_34 = [
    "FFC-EX-3dmc2.org",
    "FFC-EX-PAGboosters.org",
    "FFC-EX-SRRN.net",
    "FFC-EX-aariasblueelephant.org",
    "FFC-EX-amargraves.org",
    "FFC-EX-americanlegionpost64.org",
    "FFC-EX-aprilhansen.com",
    "FFC-EX-armstrongacesbaseball.org",
    "FFC-EX-bintobetter.org",
    "FFC-EX-browncanyonranch.org",
    "FFC-EX-bucktownbullsbaseball.org",
    "FFC-EX-bulldogztowing.com",
    "FFC-EX-canadatokeywestcoastalrun.org",
    "FFC-EX-clarasbridge.org",
    "FFC-EX-coronadonationalforestheritagesociety.org",
    "FFC-EX-falloutshelterecovillage.org",
    "FFC-EX-foxtrotenterprises.com",
    "FFC-EX-freedomrisingusa.org",
    "FFC-EX-friendsof362.org",
    "FFC-EX-instituteofforgiveness.org",
    "FFC-EX-jwvpost619.org",
    "FFC-EX-legioninthewoods.org",
    "FFC-EX-mitchellnchistory.org",
    "FFC-EX-nittanypost245.org",
    "FFC-EX-nj4israel.org",
    "FFC-EX-rebirthsdc.org",
    "FFC-EX-savewatersaveplanet.org",
    "FFC-EX-slopestohope.org",
    "FFC-EX-soulcap.org",
    "FFC-EX-southamptonfriends.org",
    "FFC-EX-sporting2Impact.org",
    "FFC-EX-technologymonastery.org",
    "FFC-EX-technomonasteries.org",
    "FFC-EX-thewayofyeshuaministries.org",
]


def _node(lib: pathlib.Path, expr_body: str, *argv: str) -> object:
    code = f"const l=require({json.dumps(str(lib))});{expr_body}"
    proc = subprocess.run(
        ["node", "-e", code, *argv], capture_output=True, text=True, timeout=60
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed: {proc.stderr}")
    return json.loads(proc.stdout)


def cron_for(repo: str) -> str:
    return _node(
        LIB, "process.stdout.write(JSON.stringify(l.cronFor(process.argv[1])));", repo
    )


def stable_minute(repo: str) -> int:
    return _node(
        LIB,
        "process.stdout.write(JSON.stringify(l.stableMinute(process.argv[1])));",
        repo,
    )


def validate_template(raw: str) -> list:
    return _node(
        LIB,
        "process.stdout.write(JSON.stringify(l.validateTemplate(process.argv[1])));",
        raw,
    )


def render_workflow(raw: str, cron: str) -> dict:
    return _node(
        LIB,
        "process.stdout.write(JSON.stringify("
        "l.renderAuditWorkflow(process.argv[1], process.argv[2])));",
        raw,
        cron,
    )


def add_script(raw: str) -> dict:
    return _node(
        LIB,
        "process.stdout.write(JSON.stringify(l.addAuditScript(process.argv[1])));",
        raw,
    )


def verify_only_added(before: str, after: str, cmd: str = AUDIT_CMD) -> dict:
    return _node(
        LIB,
        "process.stdout.write(JSON.stringify("
        "l.verifyOnlyScriptAdded(process.argv[1], process.argv[2], process.argv[3])));",
        before,
        after,
        cmd,
    )


def plan(candidates: list) -> dict:
    return _node(
        LIB,
        "process.stdout.write(JSON.stringify(l.planTargets(JSON.parse(process.argv[1]))));",
        json.dumps(candidates),
    )


def coverage_says_covered(pkg_raw: str) -> bool:
    return _node(
        COVERAGE_LIB,
        "process.stdout.write(JSON.stringify(l.hasAuditScript(process.argv[1])));",
        pkg_raw,
    )


def coverage_cron(wf_raw: str):
    return _node(
        COVERAGE_LIB,
        "process.stdout.write(JSON.stringify(l.extractCron(process.argv[1])));",
        wf_raw,
    )


def cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["node", str(CLI), *args], capture_output=True, text=True, timeout=60
    )


def _fake_repo(tmp: str, pkg: str = PKG, with_workflow: str | None = None) -> str:
    root = pathlib.Path(tmp) / "repo"
    root.mkdir(parents=True)
    (root / "package.json").write_text(pkg)
    if with_workflow is not None:
        wfdir = root / ".github" / "workflows"
        wfdir.mkdir(parents=True)
        (wfdir / "security-audit.yml").write_text(with_workflow)
    return str(root)


def _template_file(tmp: str, raw: str = CANONICAL) -> str:
    p = pathlib.Path(tmp) / "canonical.yml"
    p.write_text(raw)
    return str(p)


# --- the stagger ------------------------------------------------------------


def test_cron_is_deterministic_and_owner_prefix_insensitive():
    """Determinism is what makes a re-dispatch idempotent. A cron that shifted
    between runs would produce a fresh diff on every already-done repo."""
    a = cron_for("FFC-EX-soulcap.org")
    b = cron_for("FFC-EX-soulcap.org")
    c = cron_for("FreeForCharity/FFC-EX-soulcap.org")
    assert a == b == c, (a, b, c)


def test_stagger_never_lands_on_the_crowded_minute():
    """All 10 covered repos sit on `17 6 * * *`. A backfill that added 34 more
    to that minute would deepen the exact pile-up #838 asked it to avoid."""
    offenders = [r for r in UNCOVERED_34 if stable_minute(r) == 17]
    assert offenders == [], offenders
    # Also over a wide synthetic sweep, not only the current fleet.
    synthetic = [f"FFC-EX-r{i}.org" for i in range(500)]
    assert [r for r in synthetic if stable_minute(r) == 17] == []


def test_stagger_actually_spreads_the_real_uncovered_fleet():
    """The point of the exercise: not uniqueness (44 repos over 59 minutes must
    collide sometimes) but genuine spread."""
    minutes = [stable_minute(r) for r in UNCOVERED_34]
    distinct = len(set(minutes))
    assert distinct >= 25, f"only {distinct} distinct minutes for 34 repos: {sorted(minutes)}"
    # No single minute may take a meaningful share of the batch.
    worst = max(minutes.count(m) for m in set(minutes))
    assert worst <= 3, f"{worst} repos share one minute: {sorted(minutes)}"


def test_every_cron_is_a_valid_daily_six_oclock_expression():
    for r in UNCOVERED_34:
        c = cron_for(r)
        assert re.fullmatch(r"([0-9]|[1-5][0-9]) 6 \* \* \*", c), (r, c)


def test_741_can_parse_every_cron_this_workflow_would_write():
    """Cross-library agreement: a schedule 742 writes must be one 741's coverage
    sweep can read back, or the audit and the backfill disagree about the fleet."""
    for r in UNCOVERED_34[:8]:
        rendered = render_workflow(CANONICAL, cron_for(r))
        assert "error" not in rendered, rendered
        assert coverage_cron(rendered["content"]) == cron_for(r), r


# --- the canonical template -------------------------------------------------


def test_canonical_fixture_is_accepted():
    assert validate_template(CANONICAL) == []


def test_template_without_a_schedule_is_rejected():
    """A dispatch-only copy would never report on its own — rolled out to 34
    repos it would read as covered while running never."""
    raw = CANONICAL.replace("    - cron: '17 6 * * *' # daily, 06:17 UTC\n", "")
    problems = validate_template(raw)
    assert any("cron" in p for p in problems), problems
    assert "error" in render_workflow(raw, "5 6 * * *")


def test_template_with_two_schedules_is_rejected():
    raw = CANONICAL.replace(
        "    - cron: '17 6 * * *' # daily, 06:17 UTC",
        "    - cron: '17 6 * * *'\n    - cron: '18 6 * * *'",
    )
    problems = validate_template(raw)
    assert any("cannot tell which one" in p for p in problems), problems
    assert "error" in render_workflow(raw, "5 6 * * *")


def test_template_that_stopped_running_audit_high_is_rejected():
    """The pnpm-drift case (#771). If the canonical file moves off npm, a blind
    copy leaves the fleet with an audit that fails on every run — which looks
    identical to the vulnerability it exists to report."""
    raw = CANONICAL.replace("npm run audit:high", "pnpm run audit:high").replace(
        "npm ci --ignore-scripts", "pnpm install --frozen-lockfile"
    )
    problems = validate_template(raw)
    assert len(problems) == 2, problems
    assert "error" in render_workflow(raw, "5 6 * * *")


def test_template_escalating_permissions_is_rejected():
    raw = CANONICAL.replace("permissions:\n  contents: read", "permissions:\n  contents: write")
    problems = validate_template(raw)
    assert any("permissions" in p for p in problems), problems


def test_template_that_GAINED_a_scope_is_rejected():
    """`contents: read` being present is not enough — the block must be exactly
    that. A template that acquired `issues: write` upstream would otherwise be
    rolled out to 34 charity repos with a scope an audit has no use for.

    Caught in review on #842: the original regex only checked that `contents:
    read` appeared somewhere under `permissions:`, while its own error message
    claimed it was refusing to grant more than that.
    """
    for extra in ["issues: write", "contents: write", "pull-requests: write", "id-token: write"]:
        raw = CANONICAL.replace(
            "permissions:\n  contents: read", f"permissions:\n  contents: read\n  {extra}"
        )
        problems = validate_template(raw)
        assert any("exactly" in p for p in problems), (extra, problems)
        assert "error" in render_workflow(raw, "5 6 * * *"), extra


def test_template_with_no_permissions_block_is_rejected():
    """No block means the default token scope, which on many repos is write."""
    raw = CANONICAL.replace("permissions:\n  contents: read\n\n", "")
    problems = validate_template(raw)
    assert any("permissions" in p for p in problems), problems


def test_permissions_check_tolerates_formatting_variation():
    """Must reject on scope, not on whitespace or quoting style."""
    for variant in ["permissions:\n  contents: read", "permissions:\n    contents:   read"]:
        raw = CANONICAL.replace("permissions:\n  contents: read", variant)
        assert validate_template(raw) == [], (variant, validate_template(raw))


def test_empty_template_is_rejected():
    assert validate_template("") != []
    assert validate_template("   \n") != []


def test_render_rewrites_only_the_schedule_line():
    """Everything but the schedule is copied byte-for-byte, so a future change
    to the canonical file needs no change here and byte-identity drift audits
    (738's pattern) stay meaningful."""
    out = render_workflow(CANONICAL, "42 6 * * *")
    assert "error" not in out, out
    before = CANONICAL.splitlines()
    after = out["content"].splitlines()
    assert len(before) == len(after)
    differing = [i for i, (b, a) in enumerate(zip(before, after)) if b != a]
    assert len(differing) == 1, [(before[i], after[i]) for i in differing]
    assert "cron" in after[differing[0]]
    assert after[differing[0]].strip() == "- cron: '42 6 * * *' # daily, 06:42 UTC"


def test_render_refuses_a_malformed_cron():
    for bad in ["", "not a cron", "17 6 * *", "* * * * *", "17 6 * * 1"]:
        out = render_workflow(CANONICAL, bad)
        assert "error" in out, (bad, out)


def test_render_stamps_a_readable_time_comment():
    out = render_workflow(CANONICAL, "5 6 * * *")
    assert "# daily, 06:05 UTC" in out["content"], out["content"]


# --- editing package.json ---------------------------------------------------


def test_adds_the_script_preserving_the_file_s_formatting():
    out = add_script(PKG)
    assert out["changed"] is True, out
    content = out["content"]
    assert f'    "{"audit:high"}": "{AUDIT_CMD}",' in content
    # Untouched siblings, indentation and trailing newline.
    assert '    "dev": "next dev --turbopack",' in content
    assert content.endswith("}\n")
    assert len(content.splitlines()) == len(PKG.splitlines()) + 1


def test_edit_changes_nothing_but_the_script():
    """The guard that makes an unattended text insertion safe. A mislocated
    insertion can still produce valid JSON — dropping a sibling, nesting one
    level too deep, clobbering a value — so the whole document is compared."""
    out = add_script(PKG)
    assert verify_only_added(PKG, out["content"])["ok"] is True


def test_741_reads_the_edited_package_as_covered():
    out = add_script(PKG)
    assert coverage_says_covered(PKG) is False
    assert coverage_says_covered(out["content"]) is True


def test_already_present_is_not_a_change():
    once = add_script(PKG)["content"]
    twice = add_script(once)
    assert twice["changed"] is False
    assert twice["reason"] == "already-present", twice


def test_a_mention_of_the_script_name_elsewhere_is_not_coverage():
    """`hasAuditScript` parses rather than greps; this asserts the editor agrees
    — a repo whose README-ish field mentions the name still needs the script."""
    raw = PKG.replace(
        '  "private": true,', '  "private": true,\n  "description": "run audit:high nightly",'
    )
    assert coverage_says_covered(raw) is False
    assert add_script(raw)["changed"] is True


def test_empty_scripts_block_is_supported():
    raw = '{\n  "name": "x",\n  "scripts": {}\n}\n'
    out = add_script(raw)
    assert out["changed"] is True, out
    assert json.loads(out["content"])["scripts"]["audit:high"] == AUDIT_CMD
    assert verify_only_added(raw, out["content"])["ok"] is True


def test_tab_indentation_is_preserved():
    raw = '{\n\t"name": "x",\n\t"scripts": {\n\t\t"build": "next build"\n\t}\n}\n'
    out = add_script(raw)
    assert out["changed"] is True, out
    assert f'\t\t"audit:high": "{AUDIT_CMD}",' in out["content"], out["content"]


def test_missing_scripts_block_is_refused_not_invented():
    out = add_script('{\n  "name": "x"\n}\n')
    assert out["changed"] is False
    assert "scripts" in out["reason"], out


def test_inline_scripts_block_is_refused():
    """Valid JSON would result, but a line this change does not own gets
    re-flowed — which can fail the target repo's own format check and make a
    working backfill look like it broke the repo."""
    out = add_script('{\n  "name": "x",\n  "scripts": { "build": "next build" }\n}\n')
    assert out["changed"] is False
    assert "shares the opening brace" in out["reason"], out


def test_invalid_json_is_refused():
    assert add_script("{ not json")["changed"] is False
    assert add_script("[1,2]")["changed"] is False
    assert add_script("")["changed"] is False


def test_verify_rejects_a_dropped_sibling():
    """Simulates the realistic bad-offset outcome: still-valid JSON, still has
    audit:high, but a script was lost."""
    good = add_script(PKG)["content"]
    mangled = good.replace('    "lint": "eslint .",\n', "")
    assert verify_only_added(PKG, mangled)["ok"] is False


def test_verify_rejects_a_changed_value():
    good = add_script(PKG)["content"]
    mangled = good.replace('"next": "16.2.4"', '"next": "16.2.11"')
    v = verify_only_added(PKG, mangled)
    assert v["ok"] is False, v


def test_verify_rejects_a_missing_audit_script():
    assert verify_only_added(PKG, PKG)["ok"] is False


def test_verify_rejects_the_entry_nested_at_the_wrong_level():
    """A top-level `audit:high` would satisfy a grep and satisfy JSON.parse, but
    `npm run audit:high` would not find it."""
    mangled = PKG.replace(
        '  "private": true,', f'  "private": true,\n  "audit:high": "{AUDIT_CMD}",'
    )
    assert verify_only_added(PKG, mangled)["ok"] is False


# --- planning ---------------------------------------------------------------


def _cand(repo, **kw):
    base = {
        "repo": repo,
        "hasPackageJson": True,
        "hasWorkflow": False,
        "hasScript": False,
        "hasLockfile": True,
        "defaultBranch": "main",
    }
    base.update(kw)
    return base


def test_uncovered_repo_needs_both_halves():
    p = plan([_cand("FreeForCharity/FFC-EX-a.org")])
    assert len(p["targets"]) == 1
    assert p["targets"][0]["needs"] == ["workflow", "script"], p


def test_partial_repo_gets_only_the_missing_half():
    p = plan(
        [
            _cand("FreeForCharity/FFC-EX-wf.org", hasWorkflow=True),
            _cand("FreeForCharity/FFC-EX-sc.org", hasScript=True),
        ]
    )
    needs = {t["repo"]: t["needs"] for t in p["targets"]}
    assert needs["FreeForCharity/FFC-EX-wf.org"] == ["script"], needs
    assert needs["FreeForCharity/FFC-EX-sc.org"] == ["workflow"], needs


def test_repo_without_a_lockfile_is_blocked_not_wired():
    """The canonical workflow runs `npm ci`, which exits nonzero with no
    lockfile. Wiring such a repo yields a permanently red audit that reports
    nothing about vulnerabilities while looking exactly like a repo that has
    them — worse than uncovered, and the same looks-monitored-but-isn't failure
    #838 was opened about."""
    p = plan([_cand("FreeForCharity/FFC-EX-pnpm.org", hasLockfile=False)])
    assert p["targets"] == []
    assert len(p["blocked"]) == 1
    assert "npm ci" in p["blocked"][0]["reason"], p


def test_covered_repo_is_skipped_not_blocked():
    p = plan([_cand("FreeForCharity/FFC-EX-ok.org", hasWorkflow=True, hasScript=True)])
    assert p["targets"] == [] and p["blocked"] == []
    assert p["skipped"][0]["reason"] == "already covered", p


def test_non_node_repo_is_skipped_not_blocked():
    """The 18 static FFC-EX repos have no dependency tree; auditing them would
    only manufacture red (#838). A skip, not a gap."""
    p = plan([{"repo": "FreeForCharity/FFC-EX-static.org", "hasPackageJson": False}])
    assert p["targets"] == [] and p["blocked"] == []
    assert "no package.json" in p["skipped"][0]["reason"], p


def test_unreadable_repo_is_blocked_not_guessed():
    p = plan([{"repo": "FreeForCharity/FFC-EX-priv.org", "error": "HTTP 403 rate limited"}])
    assert p["targets"] == []
    assert "unreadable" in p["blocked"][0]["reason"], p


def test_non_main_default_branch_warns_but_does_not_block():
    """The daily cron still fires; only the canonical file's
    `pull_request: branches: ['main']` trigger goes dormant. Losing PR-time
    audits is not a reason to leave the repo with no audit at all."""
    p = plan([_cand("FreeForCharity/FFC-EX-master.org", defaultBranch="master")])
    assert len(p["targets"]) == 1, p
    assert p["targets"][0]["warnings"], p
    assert "master" in p["targets"][0]["warnings"][0]


def test_main_default_branch_produces_no_noise():
    p = plan([_cand("FreeForCharity/FFC-EX-a.org", defaultBranch="main")])
    assert p["targets"][0]["warnings"] == []


def test_plan_is_sorted_so_batches_are_reproducible():
    """max_repos slices this list, so an unstable order would make two runs pick
    overlapping-but-different batches."""
    p = plan([_cand(f"FreeForCharity/FFC-EX-{n}.org") for n in ["c", "a", "b"]])
    assert [t["repo"] for t in p["targets"]] == [
        "FreeForCharity/FFC-EX-a.org",
        "FreeForCharity/FFC-EX-b.org",
        "FreeForCharity/FFC-EX-c.org",
    ]


def test_plan_assigns_each_target_its_staggered_cron():
    p = plan([_cand(f"FreeForCharity/{r}") for r in UNCOVERED_34[:6]])
    for t in p["targets"]:
        assert t["cron"] == cron_for(t["repo"]), t


# --- the apply CLI, end to end ---------------------------------------------


def test_apply_writes_both_halves_and_agrees_with_741():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp)
        proc = cli("apply", repo, "FreeForCharity/FFC-EX-a.org", "42 6 * * *", _template_file(tmp))
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result["wroteWorkflow"] is True and result["wroteScript"] is True, result
        assert result["cron"] == "42 6 * * *", result

        wf = pathlib.Path(repo) / ".github" / "workflows" / "security-audit.yml"
        pkg = (pathlib.Path(repo) / "package.json").read_text()
        assert wf.exists()
        assert coverage_says_covered(pkg) is True
        assert coverage_cron(wf.read_text()) == "42 6 * * *"
        assert verify_only_added(PKG, pkg)["ok"] is True


def test_apply_writes_NOTHING_when_the_package_json_is_unusable():
    """The `partial` guard, end to end and at the level that matters: a repo
    whose package.json cannot be edited must not be left holding a workflow file
    that fails on every run. Nothing is written to disk until both halves are
    known to be producible."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp, pkg='{\n  "name": "x"\n}\n')  # no scripts block
        proc = cli("apply", repo, "FreeForCharity/FFC-EX-a.org", "42 6 * * *", _template_file(tmp))
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert "scripts" in proc.stderr
        assert not (pathlib.Path(repo) / ".github" / "workflows" / "security-audit.yml").exists()
        assert (pathlib.Path(repo) / "package.json").read_text() == '{\n  "name": "x"\n}\n'


def test_apply_writes_nothing_when_the_template_is_unusable():
    with tempfile.TemporaryDirectory() as tmp:
        bad = _template_file(tmp, CANONICAL.replace("npm run audit:high", "echo skip"))
        repo = _fake_repo(tmp)
        proc = cli("apply", repo, "FreeForCharity/FFC-EX-a.org", "42 6 * * *", bad)
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        assert not (pathlib.Path(repo) / ".github" / "workflows" / "security-audit.yml").exists()
        assert (pathlib.Path(repo) / "package.json").read_text() == PKG


def test_apply_is_idempotent():
    """A re-dispatch over an already-done repo must produce no diff, otherwise
    the workflow churns PRs on repos it already fixed."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp)
        tpl = _template_file(tmp)
        assert cli("apply", repo, "R", "42 6 * * *", tpl).returncode == 0
        first_pkg = (pathlib.Path(repo) / "package.json").read_text()
        first_wf = (pathlib.Path(repo) / ".github/workflows/security-audit.yml").read_text()

        proc = cli("apply", repo, "R", "42 6 * * *", tpl)
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result["wroteWorkflow"] is False and result["wroteScript"] is False, result
        assert (pathlib.Path(repo) / "package.json").read_text() == first_pkg
        assert (
            pathlib.Path(repo) / ".github/workflows/security-audit.yml"
        ).read_text() == first_wf


def test_apply_does_not_overwrite_an_existing_workflow():
    """A `partial` repo that already has the workflow (perhaps hand-tuned, or on
    a deliberately different schedule) needs only the script. Rewriting its
    schedule would be an unrequested change."""
    existing = CANONICAL.replace("17 6 * * *", "33 4 * * *")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _fake_repo(tmp, with_workflow=existing)
        proc = cli("apply", repo, "R", "42 6 * * *", _template_file(tmp))
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result["wroteWorkflow"] is False and result["wroteScript"] is True, result
        assert (
            pathlib.Path(repo) / ".github/workflows/security-audit.yml"
        ).read_text() == existing


def test_apply_reports_blocked_for_a_missing_package_json():
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp) / "repo"
        root.mkdir()
        proc = cli("apply", str(root), "R", "42 6 * * *", _template_file(tmp))
        assert proc.returncode == 2, (proc.returncode, proc.stderr)


def test_validate_template_cli_exit_codes():
    with tempfile.TemporaryDirectory() as tmp:
        assert cli("validate-template", _template_file(tmp)).returncode == 0
        bad = _template_file(tmp, "name: x\n")
        proc = cli("validate-template", bad)
        assert proc.returncode == 1
        assert "unusable" in proc.stderr, proc.stderr


def test_probe_cli_matches_741():
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "package.json"
        p.write_text(PKG)
        assert cli("probe", str(p)).stdout == "false"
        p.write_text(add_script(PKG)["content"])
        assert cli("probe", str(p)).stdout == "true"


def test_summary_flags_a_truncated_batch():
    p = plan([_cand(f"FreeForCharity/FFC-EX-{n}.org") for n in "abcde"])
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "plan.json"
        f.write_text(json.dumps(p))
        out = cli("summary", str(f), "true", "2").stdout
        assert "Batch limit" in out, out
        assert "re-dispatch for the rest" in out.lower(), out
        # And says nothing about a limit when the batch fits.
        assert "Batch limit" not in cli("summary", str(f), "true", "50").stdout


def test_pr_body_states_that_red_is_the_expected_outcome():
    """A charity maintainer seeing their first red audit must not read it as this
    PR breaking their site, and must be pointed at #822 for the fix."""
    with tempfile.TemporaryDirectory() as tmp:
        f = pathlib.Path(tmp) / "t.json"
        f.write_text(json.dumps({"repo": "FreeForCharity/FFC-EX-a.org", "cron": "42 6 * * *"}))
        body = cli("pr-body", str(f)).stdout
        assert "822" in body, body
        assert "red" in body.lower(), body
        assert "42 6 * * *" in body, body
        assert "838" in body, body


# --- workflow shape ---------------------------------------------------------


def test_plan_job_is_ungated_so_a_preview_needs_nobody():
    wf = load_workflow(WF_FILE)
    assert "environment" not in wf["jobs"]["plan"], wf["jobs"]["plan"]


def test_apply_job_is_gated_and_only_runs_for_a_live_dispatch():
    wf = load_workflow(WF_FILE)
    apply_job = wf["jobs"]["apply"]
    assert apply_job["environment"] == "github-prod", apply_job
    cond = apply_job["if"]
    assert "dry_run == false" in cond, cond
    assert "count" in cond, cond


def test_dry_run_defaults_to_preview():
    wf = load_workflow(WF_FILE)
    on = wf.get(True, wf.get("on"))
    assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True


def test_workflow_is_dispatch_only():
    """A schedule would mean unattended cross-repo writes waiting at a gate —
    the exact shape #834 measured as failing (8 of 21 scheduled gated runs)."""
    on = load_workflow(WF_FILE).get(True, load_workflow(WF_FILE).get("on"))
    assert list(on.keys()) == ["workflow_dispatch"], on


def test_concurrency_queues_rather_than_cancels():
    wf = load_workflow(WF_FILE)
    assert wf["concurrency"]["cancel-in-progress"] is False, wf["concurrency"]


def test_target_list_is_derived_live_never_hardcoded():
    """The failure #838 is named after: a hand-carried count (13, then 38, then
    44). If any fleet repo name appears literally in the shipped files, the
    35th repo provisioned next month is invisible to this workflow."""
    shipped = [
        (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text(),
        LIB.read_text(),
        CLI.read_text(),
    ]
    for text in shipped:
        offenders = [r for r in UNCOVERED_34 if r in text]
        assert offenders == [], offenders


def test_workflow_never_pushes_a_target_default_branch():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    assert "git push" in raw
    for bad in ["git push origin main", 'git push -u origin "$DEFBRANCH"', "git push origin HEAD"]:
        assert bad not in raw, bad
    # Every push goes to the dedicated backfill branch, never a target's default.
    pushes = re.findall(r"git push[^\n;]*", raw)
    assert pushes, raw
    for p in pushes:
        assert '"$BRANCH"' in p, p
        assert "--force-with-lease" in p, p


def test_apply_step_guards_the_set_of_paths_it_may_change():
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    assert "git diff --cached --name-only" in raw
    assert "unexpected paths staged" in raw


def test_canonical_template_is_fetched_not_vendored():
    """#838: 'do not write a new one'. A checked-in copy would drift from the
    source of truth, which is precisely what 738 exists to detect elsewhere."""
    assert not (REPO_ROOT / ".github" / "workflows" / "security-audit.yml").exists()
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    assert "CANONICAL_REPO" in raw
    # Validated on BOTH sides of the gate — the file can change while waiting.
    assert raw.count("validate-template") == 2, raw.count("validate-template")


def test_no_dependency_manifest_sections_are_touched():
    """Remediation is #822's. If this workflow ever bumped a version it would be
    doing an unreviewed dependency upgrade across 34 charity repos.

    Asserted on the JSON KEY names, not the English word: the prose 'dependency'
    appears throughout these files' comments, so a substring test against it is
    vacuous. (An earlier version of this test was worse than vacuous — it ended
    in `or "audit" in text`, which is true for every one of these files, so the
    assertion could never fail. Caught in review on #842.)
    """
    for path in [REPO_ROOT / ".github" / "workflows" / WF_FILE, LIB, CLI]:
        text = path.read_text()
        for forbidden in [
            '"dependencies"',
            '"devDependencies"',
            "npm install",
            "npm update",
            "npm upgrade",
        ]:
            assert forbidden not in text, f"{path.name} references {forbidden}"


def test_the_editor_cannot_alter_a_dependency_version():
    """The runtime half of the guarantee above: even if something tried, the
    whole-document comparison rejects it."""
    tampered = add_script(PKG)["content"].replace('"next": "16.2.4"', '"next": "16.2.11"')
    assert verify_only_added(PKG, tampered)["ok"] is False


def test_apply_step_distinguishes_blocked_from_an_internal_error():
    """Exit 2 (BLOCKED) is an expected, reported outcome; exit 1 is a bug. A
    single `if !` collapsing both would let the job report success while
    internal errors occurred — the looks-fine-but-lied failure this workflow
    exists to avoid, in the workflow itself. Caught in review on #842."""
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    assert '[ "$rc" -eq 2 ]' in raw, raw
    assert '[ "$rc" -ne 0 ]' in raw, raw
    # The error branch must count toward the job's failure tally; blocked must not.
    error_branch = raw.split('elif [ "$rc" -ne 0 ]; then')[1].split("continue")[0]
    assert "failures=$((failures + 1))" in error_branch, error_branch
    blocked_branch = raw.split('if [ "$rc" -eq 2 ]; then')[1].split("elif")[0]
    assert "failures=" not in blocked_branch, blocked_branch


def test_max_repos_is_normalized_to_base_ten():
    """`max_repos` is validated with `^[0-9]+$`, which accepts `08`, so it is
    normalized with `10#` before any arithmetic or jq use.

    Raised in review on #842 as "jq --argjson will fail on 08". Worth recording
    precisely, because the stated mechanism does NOT reproduce on the jq the
    runners ship: jq 1.7 parses `08` as 8 and slices correctly (asserted below,
    so this note cannot rot into a false claim). The normalization is kept as
    version-independence rather than as a bug fix — jq's number parsing was
    relaxed in 1.7 and a stricter build would reject it.

    The leading zero IS a live hazard one step away, though: bash `$((08))` is a
    hard error ("value too great for base"), so the `10#` form is the correct
    way to write this regardless of jq.
    """
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    assert 'limit="$((10#$MAX_REPOS))"' in raw, raw
    assert '--argjson n "$limit"' in raw, raw
    assert '--argjson n "$MAX_REPOS"' not in raw, raw
    # No bare $(( )) on the raw input anywhere — that is the form that breaks.
    assert "$((MAX_REPOS))" not in raw and "$(($MAX_REPOS))" not in raw, raw

    # Record the actual behaviour of the jq in use, whichever it is.
    probe = subprocess.run(
        ["jq", "-n", "--argjson", "n", "08", "$n"], capture_output=True, text=True
    )
    if probe.returncode == 0:
        assert probe.stdout.strip() == "8", probe.stdout
    # And the bash hazard the 10# form exists for.
    bare = subprocess.run(["bash", "-c", 'x=08; echo $((x))'], capture_output=True, text=True)
    assert bare.returncode != 0, "bash accepted $((08)) — check the 10# rationale"
    fixed = subprocess.run(["bash", "-c", 'x=08; echo $((10#$x))'], capture_output=True, text=True)
    assert fixed.stdout.strip() == "8", fixed


def test_header_never_wraps_mid_hyphenated_word():
    """The catalog generator joins header comment lines with a space, so a word
    split across the wrap ships to the public catalog as `dependency- vulner…`
    (caught on #840 by review)."""
    raw = (REPO_ROOT / ".github" / "workflows" / WF_FILE).read_text()
    header = [ln for ln in raw.splitlines() if ln.startswith("#")]
    offenders = [ln for ln in header if ln.rstrip().endswith("-")]
    assert not offenders, offenders


def test_does_not_collide_with_741_or_738_sweeps():
    """742 is dispatch-only so it has no cron of its own, but it must not be
    given one later that lands on the fleet readers' minutes."""
    wf = load_workflow(WF_FILE)
    on = wf.get(True, wf.get("on"))
    assert "schedule" not in on, on


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
