"""Guard: the gated/ungated claims are checked against GitHub, not against us (#993).

Every "ungated" and "gated" claim in this repo used to be asserted against
itself. `test_gated_env_hygiene.py` and the #991 tests
(`test_the_environment_is_ungated`, `test_the_catalog_agrees_with_the_workflow`)
compare a workflow's `environment:` to the safety doc and the catalog — our
files to our other files. All three can agree perfectly and be wrong together,
and the tree stays green, because an environment's protection rules live in
GitHub's Settings UI and are in no artifact here.

`scripts/check-environment-protection.py` adds the third side: platform truth.
This module is what proves that guard actually discriminates.

HOW THE MUTATIONS WORK. The checker takes `--environments-json`, so the platform
side can be replaced with a synthetic payload while the declared side stays the
real tree. `_baseline_payload()` builds the payload that *agrees* with the tree
— derived, so it cannot go stale when a lane is added — and each test mutates
exactly one thing and requires a specific failure. That ordering matters: the
baseline is only a control. A guard that returns "OK" unconditionally passes the
baseline test and fails every mutation test, which is the whole point (the
lesson `test_subprocess_encoding.py` records, applied here).

WHAT THIS MODULE DELIBERATELY DOES NOT DO. It never calls the network and never
mutates a real environment. AC1 — "with the live table as it stands today, the
check passes" — is proved by the checker running for real in 722-ci.yml against
the ambient token, which is also the only place a token exists. Asserting the
live table from here would make the suite require a token it does not have, and
the fix for that is always a skip, which is the failure shape this whole issue
is about.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "scripts" / "check-environment-protection.py"

_SPEC = importlib.util.spec_from_file_location("check_environment_protection", CHECKER)
# Fail here, saying what is wrong, rather than several lines down with an
# AttributeError on None. If the checker is renamed or moved, THIS is the
# message that should appear — not a TypeError about a NoneType loader.
assert _SPEC is not None and _SPEC.loader is not None, (
    f"cannot load the checker from {CHECKER} — does that file still exist?"
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

evaluate = _MOD.evaluate
protection_map = _MOD.protection_map
workflow_lanes = _MOD.workflow_lanes
declared_gated_environments = _MOD.declared_gated_environments
REVIEWER_RULE = _MOD.REVIEWER_RULE


def _lanes():
    return workflow_lanes()


def _gated():
    return declared_gated_environments()


def _baseline_payload():
    """An API payload that agrees with the tree — derived, never hand-listed.

    Every environment the workflows reference, carrying `required_reviewers`
    exactly when the docs say it should. This is the control the mutations are
    measured against, and building it from the same sources the checker reads is
    what keeps it correct when a lane is added or renamed.
    """
    gated = _gated()
    envs = []
    for name in sorted(_lanes()):
        rules = [{"type": REVIEWER_RULE}] if name in gated else []
        envs.append({"name": name, "protection_rules": rules})
    return {"total_count": len(envs), "environments": envs}


def _run(payload):
    """Run the checker's main() against a payload; return (exit_code, output)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = pathlib.Path(tmp) / "environments.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                code = _MOD.main(["--environments-json", str(path)])
            except SystemExit as exc:  # argparse / fail-closed paths
                code = exc.code if isinstance(exc.code, int) else 1
                buf.write(str(exc))
            except Exception:  # noqa: BLE001 - a crash is a RESULT here, not an error
                # Without this the checker crashing takes the whole module down
                # mid-run: the remaining tests never execute, and the one
                # assertion that cares -- "the guard exits with a message rather
                # than a traceback" -- can never see the traceback it is looking
                # for. Recording it as output makes a crash a clean FAIL on the
                # test that provoked it, and lets the rest of the suite finish.
                import traceback

                code = 1
                buf.write(traceback.format_exc())
        return code, buf.getvalue()


def _an_ungated_lane():
    """Any environment the tree references that the docs call ungated."""
    ungated = sorted(set(_lanes()) - _gated())
    assert ungated, "no ungated environment is referenced by any workflow — nothing to mutate"
    return ungated[0]


def _a_gated_lane():
    gated = sorted(set(_lanes()) & _gated())
    assert gated, "no gated environment is referenced by any workflow — nothing to mutate"
    return gated[0]


# --------------------------------------------------------------------------
# The control.
# --------------------------------------------------------------------------


def test_a_payload_that_agrees_with_the_docs_passes():
    """AC1's shape: when the platform matches the declarations, the check is green."""
    code, out = _run(_baseline_payload())
    assert code == 0, f"a payload agreeing with the docs must pass, got exit {code}:\n{out}"
    assert "Environment protection OK" in out, out


# --------------------------------------------------------------------------
# AC2 — the dangerous direction: a docs-ungated lane silently acquires a gate.
# --------------------------------------------------------------------------


def test_gating_an_ungated_lane_fails_and_names_the_workflows():
    """Adding required_reviewers to an ungated lane reproduces #834 fleet-wide.

    The load-bearing assertion is not just the non-zero exit — it is that the
    finding names the environment AND the workflows on that lane, because that
    is the whole content of the alert. A guard that fails without saying what
    broke sends the reader back to the Settings UI to guess.
    """
    env = _an_ungated_lane()
    payload = copy.deepcopy(_baseline_payload())
    for entry in payload["environments"]:
        if entry["name"] == env:
            entry["protection_rules"] = [{"type": REVIEWER_RULE}]
    code, out = _run(payload)
    assert code == 1, f"gating the ungated lane {env} must fail the check, got exit {code}:\n{out}"
    assert env in out, f"the finding must name {env}:\n{out}"
    for wf in _lanes()[env]:
        assert wf in out, f"the finding must name the affected workflow {wf}:\n{out}"


# --------------------------------------------------------------------------
# AC3 — the other direction: a gate quietly disappears.
# --------------------------------------------------------------------------


def test_ungating_a_gated_lane_fails():
    """AC2 alone is satisfiable by a check that only looks one way.

    Removing a reviewer requirement widens what a workflow may do without
    anyone approving it, so it has to be caught by the same pass.
    """
    env = _a_gated_lane()
    payload = copy.deepcopy(_baseline_payload())
    for entry in payload["environments"]:
        if entry["name"] == env:
            entry["protection_rules"] = []
    code, out = _run(payload)
    assert code == 1, f"ungating the gated lane {env} must fail the check, got exit {code}:\n{out}"
    assert env in out, f"the finding must name {env}:\n{out}"


def test_a_non_reviewer_protection_rule_does_not_count_as_a_gate():
    """`wait_timer` delays a run; it does not park it waiting for a human.

    Without this, a gated lane whose reviewers were replaced by a wait timer
    would read as still-gated — protected in the table, unprotected in fact.
    """
    env = _a_gated_lane()
    payload = copy.deepcopy(_baseline_payload())
    for entry in payload["environments"]:
        if entry["name"] == env:
            entry["protection_rules"] = [{"type": "wait_timer", "wait_timer": 30}]
    code, out = _run(payload)
    assert code == 1, (
        f"a wait_timer must not satisfy the gate requirement on {env}, got exit {code}:\n{out}"
    )


# --------------------------------------------------------------------------
# AC4 — an environment a workflow names that does not exist.
# --------------------------------------------------------------------------


def test_a_referenced_environment_missing_from_the_api_fails():
    """A typo'd `environment:` is created on first run with no protection at all.

    So "absent from the API" is not a harmless bookkeeping difference: it is a
    lane that will come into existence ungated whatever the docs say about it.
    """
    env = _a_gated_lane()
    payload = copy.deepcopy(_baseline_payload())
    payload["environments"] = [e for e in payload["environments"] if e["name"] != env]
    payload["total_count"] = len(payload["environments"])
    code, out = _run(payload)
    assert code == 1, f"a referenced-but-absent environment ({env}) must fail, got {code}:\n{out}"
    assert env in out, f"the finding must name {env}:\n{out}"


def test_an_absent_lane_is_fatal_even_when_the_docs_call_it_ungated():
    """Why `absent -> fatal` cannot be split by declared gatedness.

    Reviewed and nearly changed on #1005: make an absent environment fatal only
    when the docs call it GATED, and merely reported when they call it ungated,
    so a knowingly-unprovisioned ungated lane could pass. That is appealing —
    an auto-created rule-less environment does match an "ungated" declaration —
    and it fails against the threat the rule exists to stop.

    A typo'd name can never appear in the gated set, so the declared side
    classifies it as UNGATED **by construction**. Under the split, a workflow
    that typos its way off a gated lane would emit a warning and merge, losing
    the gate silently. The declared side is untrustworthy in exactly the case
    that matters, which is what makes the unconditional rule load-bearing.

    Pinned as a test so the reasoning is not re-litigated from the wording of
    the error message alone.
    """
    typo = _a_gated_lane() + "-raed"
    assert typo not in _gated(), "premise: a typo cannot be in the declared gated set"
    errors, warnings = evaluate({typo: ["999-does-a-write.yml"]}, _gated(), {})
    assert any(typo in e for e in errors), (
        "an absent environment must be fatal even though the declared side reads it "
        f"as ungated — that is the whole point. errors={errors} warnings={warnings}"
    )


def test_an_empty_environment_list_fails_rather_than_reading_as_no_gates():
    """The failure mode workflow 730 states in its own error path.

    "Cannot read the environments" and "this repo has no environments" are
    opposite findings; collapsing them lets an unreadable audit render as a
    clean bill of health.
    """
    code, out = _run({"total_count": 0, "environments": []})
    assert code == 1, f"an empty environment list must fail the check, got exit {code}:\n{out}"


def test_an_unrecognised_payload_shape_fails():
    """Refuse to interpret a response we do not understand as 'no gates'."""
    code, out = _run({"message": "Not Found"})
    assert code == 1, f"a payload with no 'environments' key must fail, got exit {code}:\n{out}"
    assert "no 'environments' key" in out, f"the message must say what was wrong:\n{out}"


def test_a_malformed_environments_value_fails_closed_with_a_message():
    """Every not-a-list shape is rejected, not coerced.

    The coercing form (`payload.get("environments") or []`) turns a dict into a
    list of its KEYS, so the rows become strings and the first `.get()`
    downstream raises AttributeError — a traceback about our own bookkeeping
    instead of about the malformed response. Exit 1 either way, but only one of
    them tells the reader what happened, and this guard exists to be readable
    when it fires.
    """
    for value in (None, {"github-prod": "gated"}, "environments", 7):
        code, out = _run({"total_count": 0, "environments": value})
        assert code == 1, f"environments={value!r} must fail closed, got exit {code}:\n{out}"
        assert "not a list" in out, (
            f"environments={value!r} must be rejected by name, not crash: {out}"
        )
        assert "Traceback" not in out, f"environments={value!r} crashed instead of exiting:\n{out}"


def test_a_non_object_environment_entry_fails_closed_with_a_message():
    """A list is not enough — its entries have to be objects we can read."""
    for entry in ("github-prod", None, 42, ["github-prod"]):
        code, out = _run({"total_count": 1, "environments": [entry]})
        assert code == 1, f"entry {entry!r} must fail closed, got exit {code}:\n{out}"
        assert "non-object entry" in out, f"entry {entry!r} must be rejected by name:\n{out}"
        assert "Traceback" not in out, f"entry {entry!r} crashed instead of exiting:\n{out}"


def test_both_entry_points_share_one_definition_of_a_valid_payload():
    """The paged fetch and protection_map must not drift apart on shape.

    They had two partial copies of the check; the fetch path accepted a dict for
    `environments` that protection_map would then crash on. One validator, used
    by both, is what makes the tests above cover the network path too.
    """
    import ast

    tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert list(funcs).count("environment_rows") == 1, "exactly one shape validator, please"
    for caller in ("fetch_environments_payload", "protection_map"):
        assert caller in funcs, f"{caller} is gone — this test is no longer checking anything"
        called = {
            n.func.id
            for n in ast.walk(funcs[caller])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "environment_rows" in called, (
            f"{caller}() must route through environment_rows(), or the definition of "
            "a valid payload has drifted into two copies again — which is how the "
            "fetch path came to accept a dict that protection_map then crashed on"
        )


# --------------------------------------------------------------------------
# AC5 — a missing token fails, it does not skip.
# --------------------------------------------------------------------------


def test_a_missing_token_fails_rather_than_skipping():
    """A check that silently no-ops is exactly what this issue is about.

    Driven through `resolve_token()` with both variables cleared, which is the
    state a developer (or a CI step that forgot the `env:` block) is in.
    """
    import os

    saved = {v: os.environ.pop(v, None) for v in ("GH_TOKEN", "GITHUB_TOKEN")}
    try:
        raised = None
        try:
            _MOD.resolve_token()
        except SystemExit as exc:
            raised = exc
        assert raised is not None, (
            "resolve_token() must raise on a missing token, never return a sentinel "
            "that a caller can read as 'skip'"
        )
        message = str(raised.code if raised.code is not None else raised)
        assert "GH_TOKEN" in message and "FAILS rather than skipping" in message, (
            f"the error must say which variable to set and that this is not a skip: {message}"
        )
    finally:
        for var, val in saved.items():
            if val is not None:
                os.environ[var] = val


def test_an_empty_token_is_treated_as_missing():
    """`GH_TOKEN: ${{ secrets.MISSING }}` renders as an empty string, not an unset var."""
    import os

    saved = {v: os.environ.get(v) for v in ("GH_TOKEN", "GITHUB_TOKEN")}
    os.environ["GH_TOKEN"] = "   "
    os.environ.pop("GITHUB_TOKEN", None)
    try:
        raised = None
        try:
            _MOD.resolve_token()
        except SystemExit as exc:
            raised = exc
        assert raised is not None, "an empty/whitespace token must fail like a missing one"
    finally:
        for var, val in saved.items():
            os.environ.pop(var, None)
            if val is not None:
                os.environ[var] = val


# --------------------------------------------------------------------------
# AC6 — both sides derived, and the guard is actually wired in.
# --------------------------------------------------------------------------


def test_the_checker_hand_lists_no_environment_names():
    """The point of the check is the pair nobody thought to list (ledger L16/L62).

    Also the reason the naming convention cannot be used as a proxy: `zeffy-prod`
    is ungated despite the `-prod` suffix, so anything parsing names instead of
    reading the API would classify it wrong.
    """
    src = CHECKER.read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]  # skip the module docstring, which cites names as prose
    for env in sorted(set(_lanes()) | _gated()):
        assert f'"{env}"' not in body and f"'{env}'" not in body, (
            f"{CHECKER.name} hard-codes the environment name {env!r}. Both sides must be "
            "derived from the tree, the docs and the API, or the check cannot catch a "
            "lane nobody remembered to list."
        )


def test_the_gated_set_comes_from_the_repos_single_declaration():
    """Not a fourth artifact that agrees with itself.

    `GATED_ENVS` in scripts/check-workflow-doc-consistency.py is the one
    machine-readable statement of the safety doc's approval-gates layer, and
    test_gated_env_hygiene.py already pins it to that doc.
    """
    checker_src = CHECKER.read_text(encoding="utf-8")
    assert "check-workflow-doc-consistency.py" in checker_src, (
        "the gated set must be read from the existing declaration, not re-typed"
    )
    assert _gated(), "declared_gated_environments() returned nothing"


def test_the_lanes_are_derived_from_the_workflow_tree():
    """Every lane maps to the real files declaring it — that is what the alert names."""
    lanes = _lanes()
    assert lanes, "no environments found in .github/workflows — the tree read failed"
    for env, files in lanes.items():
        assert files, f"{env} has no declaring workflow"
        for name in files:
            assert (REPO_ROOT / ".github" / "workflows" / name).is_file(), (
                f"{env} maps to {name}, which is not a workflow file"
            )


def test_the_guard_is_wired_into_ci():
    """A checker nobody runs is documentation. It also needs the token and the scope."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-environment-protection.py" in ci, (
        "check-environment-protection.py must run in 722-ci.yml, or the rule is documentation"
    )
    assert "actions: read" in ci, (
        "the validate job needs `actions: read` to read its own environments — "
        "workflow 730 reads them with exactly that scope"
    )


def test_the_evaluate_function_is_pure_and_reports_dead_lanes():
    """An environment nobody references is reported, not fatal.

    Fatal would be wrong twice over: `cloudflare-prod`, `copilot` and
    `github-pages` are legitimately in that state, and a check that cannot pass
    on a correct tree gets switched off.
    """
    lanes = {"a-lane": ["001-x.yml"]}
    actual = {"a-lane": [], "an-orphan": [REVIEWER_RULE]}
    errors, warnings = evaluate(lanes, set(), actual)
    assert not errors, f"an agreeing pair must produce no errors: {errors}"
    assert any("an-orphan" in w for w in warnings), (
        f"an unreferenced environment must be reported: {warnings}"
    )


def test_protection_map_reads_rule_types():
    payload = {
        "total_count": 2,
        "environments": [
            {"name": "one", "protection_rules": [{"type": REVIEWER_RULE}, {"type": "wait_timer"}]},
            {"name": "two", "protection_rules": []},
        ],
    }
    assert protection_map(payload) == {"one": [REVIEWER_RULE, "wait_timer"], "two": []}


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
