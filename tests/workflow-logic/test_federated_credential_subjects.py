"""Unit tests for scripts/check-federated-credential-subjects.py (#1081).

The checker is the shipped implementation — this module imports it rather than
re-deriving its rules, so the logic under test is the logic that runs in CI.

What the coverage half is for: the script's expected map
(`config/federated-credentials.json`) is hand-maintained, and until #1081
nothing compared it to the repo. It printed

    Federated-credential config OK: 3 apps, 9 expected env credentials.

on every run while **four** OIDC lanes sat outside its denominator —
`candid-prod-read`, `fraudlabspro-prod-read`, `github-prod`, `github-prod-read`
— and those four contained 100% of the repo's failing automation (#949, #921,
#1033, and the never-runnable 801/802). A control whose input set is
hand-maintained cannot detect an omitted input and reports clean over exactly
the gap it exists to close (L133); here the omitted set and the broken set were
the same set.

Both directions are load-bearing, and each is pinned by a mutation rather than
by the live tree, so the tests keep discriminating after the tree is fixed:

  * an unmapped OIDC lane fails `test_the_pre_fix_config_is_reported` — built by
    mutating the config back to how it shipped, all four gaps and the stale
    entry named;
  * a mapped environment nothing uses fails `test_a_mapped_but_unused_env_is_stale`.

The false-positive surface is asserted too, because it is the whole difference
between this check and a blanket environment audit: `github-pages` (721) and
`wpmudev-prod` (601) declare environments and perform no Azure OIDC exchange,
and a guard that flagged them would have to be switched off to land anything.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-federated-credential-subjects.py"
CONFIG = REPO_ROOT / "config" / "federated-credentials.json"


def _load_checker():
    """Import the hyphenated script as a module (same shape as test_502)."""
    spec = importlib.util.spec_from_file_location(
        "check_federated_credential_subjects", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()
CFG = json.loads(CONFIG.read_text(encoding="utf-8"))


def _mapped(cfg) -> set:
    return {env for app in cfg["apps"] for env in app.get("environments", [])}


def _run(args=(), cwd=REPO_ROOT):
    """Run the real script as CI does. Text captured, never a bare returncode.

    A test that asserts only on an exit code cannot tell the system under test
    from a broken harness — both exit 1 (CLAUDE.md, #722's seventh test).
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, encoding="utf-8", errors="replace", cwd=str(cwd),
        # Both ends pinned: `encoding=` decodes here, PYTHONIOENCODING makes the
        # Python child ENCODE as utf-8 rather than the console codepage. Pinning
        # only the parent leaves the child emitting cp1252 on Windows, the
        # decode raises on subprocess's reader thread, and stdout comes back
        # None — guarded by scripts/check-subprocess-encoding.py (#962).
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


def _synthetic_tree(workflows: dict, actions: dict | None = None) -> tempfile.TemporaryDirectory:
    """A temp repo root holding `.github/workflows` (+ optional local actions)."""
    tmp = tempfile.TemporaryDirectory()
    root = pathlib.Path(tmp.name)
    wf_dir = root / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    for name, body in workflows.items():
        text = body if isinstance(body, str) else yaml.safe_dump(body, allow_unicode=True)
        (wf_dir / name).write_text(text, encoding="utf-8", newline="\n")
    for rel, body in (actions or {}).items():
        path = root / rel / "action.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8", newline="\n")
    return tmp


def _oidc_job(environment: str) -> dict:
    return {
        "runs-on": "ubuntu-latest",
        "environment": environment,
        "permissions": {"id-token": "write"},
        "steps": [{"uses": "azure/login@v3", "with": {"client-id": "x"}}],
    }


# --- the live invariants ----------------------------------------------------


def test_the_mapped_set_equals_the_oidc_using_set():
    """Criterion 2: set equality, asserted as sets — not as a count.

    Two counts can agree while the members differ, which is the failure this
    guard exists to make impossible.
    """
    usage, problems = checker.oidc_environment_usage(root=str(REPO_ROOT))
    assert not problems, problems
    assert set(usage) == _mapped(CFG), (
        f"only in the tree: {sorted(set(usage) - _mapped(CFG))}; "
        f"only in the map: {sorted(_mapped(CFG) - set(usage))}"
    )


def test_the_shipped_script_exits_zero_on_the_tree():
    result = _run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "coverage OK" in result.stdout, result.stdout


def test_the_four_repaired_lanes_are_mapped():
    """The gaps #1081 measured, pinned by name so a revert is loud."""
    mapped = _mapped(CFG)
    for env in ("candid-prod-read", "fraudlabspro-prod-read", "github-prod", "github-prod-read"):
        assert env in mapped, f"{env} dropped out of the expected map"
    assert "cloudflare-prod" not in mapped, "the stale entry came back"


# --- the guard discriminates (mutations) ------------------------------------


def test_the_pre_fix_config_is_reported():
    """Criterion 1: the map as it shipped fails, naming all five findings."""
    pre_fix = copy.deepcopy(CFG)
    for app in pre_fix["apps"]:
        if app["displayName"] == "ffc-admin-kv-reader":
            app["environments"] = ["cloudflare-prod-read", "google-prod-read", "whmcs-prod-read"]
        elif app["displayName"] == "ffc-admin-kv-writer":
            app["environments"] = [
                "cloudflare-prod-write", "cloudflare-prod", "google-prod-write",
                "zeffy-prod", "whmcs-prod",
            ]
    # Assert the mutation landed before believing its result: a plant that did
    # not apply produces a green run that reads as "the guard has a hole".
    assert _mapped(pre_fix) != _mapped(CFG) and len(_mapped(pre_fix)) == 9

    usage, problems = checker.oidc_environment_usage(root=str(REPO_ROOT))
    findings = _coverage_findings(pre_fix, usage, problems)
    joined = "\n".join(findings)
    for env in ("candid-prod-read", "fraudlabspro-prod-read", "github-prod", "github-prod-read"):
        assert any(f"'{env}'" in f and "NOT in the expected map" in f for f in findings), joined
    assert any("'cloudflare-prod'" in f and "stale" in f for f in findings), joined
    assert len(findings) == 5, joined


def test_a_new_oidc_lane_is_caught():
    """Criterion 3: the actual job — catching lanes nobody has added yet."""
    with _synthetic_tree({"999-new.yml": {"jobs": {"go": _oidc_job("newthing-prod")}}}) as root:
        usage, problems = checker.oidc_environment_usage(root=root)
        assert usage == {"newthing-prod": ["999-new.yml"]}, usage
        findings = _coverage_findings(CFG, usage, problems)
        assert any("'newthing-prod'" in f for f in findings), findings


def test_a_mapped_but_unused_env_is_stale():
    """Criterion 5: a stale entry is one finding, and only that one."""
    with_stale = copy.deepcopy(CFG)
    for app in with_stale["apps"]:
        if app["displayName"] == "ffc-admin-kv-writer":
            app["environments"] = sorted(app["environments"] + ["cloudflare-prod"])
    assert "cloudflare-prod" in _mapped(with_stale)

    usage, problems = checker.oidc_environment_usage(root=str(REPO_ROOT))
    findings = _coverage_findings(with_stale, usage, problems)
    assert len(findings) == 1, findings
    assert "'cloudflare-prod'" in findings[0] and "stale" in findings[0], findings


# --- the false-positive surface ---------------------------------------------


def test_an_environment_without_an_azure_exchange_is_not_a_finding():
    """Criterion 4: this is not a blanket environment audit.

    `github-pages` (721) and `wpmudev-prod` (601) are real environments on real
    jobs that never touch Azure. Flagging them would make the check unlandable.
    """
    usage, _ = checker.oidc_environment_usage(root=str(REPO_ROOT))
    for env in ("github-pages", "wpmudev-prod"):
        assert env not in usage, f"{env} was read as performing an Azure OIDC exchange"

    plain = {
        "runs-on": "ubuntu-latest",
        "environment": "somewhere-prod",
        "steps": [{"uses": "actions/checkout@v5"}, {"run": "echo ${{ secrets.GITHUB_TOKEN }}"}],
    }
    assert checker.job_performs_azure_oidc(plain, root=str(REPO_ROOT)) is False


def test_a_job_with_no_environment_is_ignored():
    assert checker.job_environment({}) is None
    with _synthetic_tree({"998-x.yml": {"jobs": {"go": {"steps": [{"uses": "azure/login@v3"}]}}}}) as root:
        usage, problems = checker.oidc_environment_usage(root=root)
        assert usage == {} and not problems, (usage, problems)


def test_the_environment_object_form_is_understood():
    """`environment: {name: …, url: …}` is the same declaration."""
    assert checker.job_environment({"environment": {"name": "m365-prod", "url": "https://x"}}) == "m365-prod"
    assert checker.job_environment({"environment": "whmcs-prod"}) == "whmcs-prod"


# --- the other two OIDC signals ---------------------------------------------


def test_a_composite_action_is_resolved_by_reading_it():
    """A `*-from-kv` action counts because it logs in, not because of its name."""
    job = {
        "environment": "kvlane-prod",
        "steps": [{"uses": "./.github/actions/thing-from-kv"}],
    }
    workflows = {"997-kv.yml": {"jobs": {"go": job}}}
    doing = {".github/actions/thing-from-kv": {"runs": {"using": "composite", "steps": [{"uses": "azure/login@v3"}]}}}
    with _synthetic_tree(workflows, doing) as root:
        usage, problems = checker.oidc_environment_usage(root=root)
        assert usage == {"kvlane-prod": ["997-kv.yml"]} and not problems, (usage, problems)

    # …and an action that does NOT log in leaves the lane uncounted, so the
    # signal is the action's content and not the `-from-kv` in its name.
    inert = {".github/actions/thing-from-kv": {"runs": {"using": "composite", "steps": [{"run": "echo hi"}]}}}
    with _synthetic_tree(workflows, inert) as root:
        usage, problems = checker.oidc_environment_usage(root=root)
        assert usage == {} and not problems, (usage, problems)


def test_an_identifier_reference_counts_as_an_exchange():
    """A job hand-rolling the exchange presents as an identifier reference."""
    job = {
        "environment": "handrolled-prod",
        "steps": [{"run": "echo x", "env": {"AZ": "${{ vars.READ_ALL_FFC_AZURE_KV_CLIENT_ID }}"}}],
    }
    with _synthetic_tree({"996-h.yml": {"jobs": {"go": job}}}) as root:
        usage, _ = checker.oidc_environment_usage(root=root)
        assert usage == {"handrolled-prod": ["996-h.yml"]}, usage

    # The bracket form is exactly equivalent at run time; matching only the dot
    # form would leave a one-character bypass.
    job["steps"][0]["env"]["AZ"] = "${{ vars['READ_ALL_FFC_AZURE_TENANT_ID'] }}"
    with _synthetic_tree({"996-h.yml": {"jobs": {"go": job}}}) as root:
        usage, _ = checker.oidc_environment_usage(root=root)
        assert usage == {"handrolled-prod": ["996-h.yml"]}, usage


# --- fail closed -------------------------------------------------------------


def test_an_unparseable_workflow_is_a_finding_not_a_skip():
    """Criterion 6. A guard that drops the file it cannot read reports clean
    over the one place nobody has looked."""
    with _synthetic_tree({"995-broken.yml": "jobs: [: :\n  - not: yaml: at all\n"}) as root:
        usage, problems = checker.oidc_environment_usage(root=root)
        assert problems, "an unparseable workflow was skipped silently"
        assert "995-broken.yml" in problems[0], problems
        assert _coverage_findings(CFG, usage, problems), "the parse error did not fail the check"


def test_a_workflow_that_parses_but_is_not_a_workflow_is_a_finding():
    """`jobs:` valid YAML, wrong shape — report, never raise (#1120 review).

    Measured before fixing: `jobs: [a, b]` and `jobs: nope` each raised
    AttributeError out of the coverage check. A traceback is not a finding — CI
    fails with an opaque stack instead of naming the file — so the shapes are
    pinned here rather than left to review.
    """
    for body in ("jobs:\n  - a\n  - b\n", "jobs: nope\n"):
        with _synthetic_tree({"993-shape.yml": body}) as root:
            usage, problems = checker.oidc_environment_usage(root=root)
            assert problems and "993-shape.yml" in problems[0], (body, problems)
            assert "not a mapping" in problems[0], problems
            assert usage == {}, usage

    # `jobs: []` too. The old `or {}` swallowed it because an empty list is
    # falsy, which is an accident of truthiness rather than a decision: Actions
    # requires `jobs:` to be a MAP, so an empty list is just as invalid as a
    # populated one and there is no reason for the two to be judged differently.
    # One rule, no special case.
    with _synthetic_tree({"993-shape.yml": "jobs: []\n"}) as root:
        usage, problems = checker.oidc_environment_usage(root=root)
        assert problems and "not a mapping" in problems[0], problems
        assert usage == {}, usage

    # A file with no `jobs:` key at all is left alone — that is absence, not a
    # malformed value, and the check is not a workflow-schema validator.
    with _synthetic_tree({"993-shape.yml": "name: no jobs here\n"}) as root:
        usage, problems = checker.oidc_environment_usage(root=root)
        assert not problems and usage == {}, (usage, problems)


def test_a_malformed_composite_action_fails_closed():
    """An action.yml that parses but is not an action (#1120 review)."""
    job = {"environment": "shape-prod", "steps": [{"uses": "./.github/actions/bad"}]}
    workflows = {"992-bad.yml": {"jobs": {"go": job}}}
    for action_body, expected in (
        ({"name": "x", "runs": "nope"}, "`runs:`"),
        (["a", "b"], "not a mapping"),
    ):
        with _synthetic_tree(workflows) as root:
            path = pathlib.Path(root) / ".github" / "actions" / "bad" / "action.yml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(action_body), encoding="utf-8", newline="\n")
            usage, problems = checker.oidc_environment_usage(root=root)
            assert problems and expected in problems[0], (action_body, problems)
            # Fail closed: undecidable counts as an exchange, so the lane stays
            # visible instead of being silently dropped.
            assert usage == {"shape-prod": ["992-bad.yml"]}, usage


def test_an_unreadable_local_action_fails_closed():
    job = {"environment": "mystery-prod", "steps": [{"uses": "./.github/actions/ghost"}]}
    with _synthetic_tree({"994-ghost.yml": {"jobs": {"go": job}}}) as root:
        usage, problems = checker.oidc_environment_usage(root=root)
        assert problems and "ghost" in problems[0], problems
        # Fail closed: undecidable is counted as an exchange, so the lane is
        # visible rather than quietly dropped.
        assert usage == {"mystery-prod": ["994-ghost.yml"]}, usage


# --- the pre-existing audit is untouched ------------------------------------


def test_the_live_audit_behaviour_is_unchanged():
    """Criterion 7: this adds a check; it does not alter the audit."""
    reader = next(a for a in CFG["apps"] if a["displayName"] == "ffc-admin-kv-reader")
    good = [
        {
            "subject": checker.expected_subject(CFG, env),
            "issuer": CFG["issuer"],
            "audiences": CFG["audiences"],
        }
        for env in reader["environments"]
    ]
    one_app = copy.deepcopy(CFG)
    one_app["apps"] = [reader]
    with _captured():
        assert checker.audit(one_app, lambda _oid: good) == 0

    # The #625 class: present, but with a typo'd subject.
    typo = copy.deepcopy(good)
    typo[0]["subject"] = typo[0]["subject"].replace("FFC-Cloudflare-Automation", "FFC-Cloudflare-Automation-")
    with _captured() as out:
        assert checker.audit(one_app, lambda _oid: typo) == 1
    assert "is MISSING" in out.getvalue(), out.getvalue()


@contextlib.contextmanager
def _captured():
    """Collect what the checker prints instead of letting it into the roster.

    run_all.py reads this module's stdout for `  PASS`/`  FAIL` lines; a
    checker printing its own findings there is noise at best.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        yield buffer


def _coverage_findings(cfg, usage, problems):
    """coverage_check's findings, captured rather than printed.

    The checker indents each finding (`  - <text>`), so this strips before
    matching and is indentation-independent by construction. Stated because the
    first draft was not: it matched `- ` against the RAW line, returned [], and
    four assertions of the form "the guard reports X" passed over an empty list.
    A predicate over captured output needs a positive control — which is what
    the callers' non-empty and exact-count assertions now are.
    """
    with _captured() as out:
        checker.coverage_check(cfg, usage, problems)
    findings = [
        line.strip()[2:].strip()
        for line in out.getvalue().splitlines()
        if line.strip().startswith("- ")
    ]
    return findings


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
