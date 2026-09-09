"""Unit tests for the fixed-temp-path guard (#1247).

The load-bearing tests here are `test_the_scanner_flags_the_pre_fix_738_body`
and `test_two_concurrent_copies_of_a_body_writing_a_fixed_path_collide`. The
tree is clean by construction after the fix, so every other test in this module
would also pass against a scanner that returned `[]` unconditionally: one feeds
it the exact body that shipped before the fix and requires a finding, the other
demonstrates on the filesystem that the shape the guard rejects really does lose
data when two processes run it at once.

That second one is unusual for this suite -- it measures behaviour rather than
text -- and it is here because AC4 of #1247 asks for the fix to be shown failing
under the old path. A guard whose premise cannot be reproduced is a guard nobody
will believe the next time it is inconvenient.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

from wf_extract import child_env, forward_slashes, runner_temp, step_run  # noqa: E402

import importlib.util  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS_DIR = pathlib.Path(__file__).resolve().parent
GUARD = REPO_ROOT / "scripts" / "check-workflow-fixed-tmp-paths.py"

_spec = importlib.util.spec_from_file_location("check_workflow_fixed_tmp_paths", GUARD)
guard = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(guard)


def _scan(body: str) -> list[tuple[int, str]]:
    return guard.scan_body(body)


# --- the pre-fix shapes ----------------------------------------------------


def test_the_scanner_flags_the_pre_fix_738_body():
    """The exact three lines 738 shipped before #1247, verbatim.

    Without this the module asserts only that a clean tree is clean.
    """
    body = (
        '            if ! gh api "repos/${repo}/contents/${SMOKE_PATH}" \\\n'
        '                   -H "Accept: application/vnd.github.raw" \\\n'
        "                   >/tmp/smoke-body 2>/tmp/smoke-err; then\n"
        '          entries="/tmp/entries.json"\n'
        "            sha256sum /tmp/smoke-body | cut -d' ' -f1\n"
    )
    hits = [text for _, text in _scan(body)]
    assert sorted(hits) == sorted(
        [
            "/tmp/smoke-body",
            "/tmp/smoke-err",
            "/tmp/entries.json",
            "/tmp/smoke-body",
        ]
    ), hits


def test_the_scanner_flags_a_fixed_path_in_a_github_script_body():
    """743's classify step is JS, not bash, and read three fixed paths."""
    body = (
        "            const targets = JSON.parse(fs.readFileSync('/tmp/targets.json', 'utf8'));\n"
        "              const headPath = `/tmp/probes/${safe}.head`;\n"
    )
    hits = [text for _, text in _scan(body)]
    # Pinned EXACTLY, not with `startswith`. The loose form was what let the
    # template literal's own closing backtick ride along in the captured text:
    # a finding that reads ``/tmp/probes/${safe}.head` `` is not the path, and a
    # freeze entry would have had to carry the punctuation to match it.
    assert hits == ["/tmp/targets.json", "/tmp/probes/${safe}.head"], hits


def test_a_read_is_a_finding_not_only_a_write():
    """A read is how the collision surfaces, and it depends on some other body
    having written the fixed path in the first place."""
    assert _scan('          body=$(cat /tmp/drift-report.md)\n'), "read not flagged"


def test_a_finding_carries_the_line_it_is_on():
    body = "echo one\necho two\ncat /tmp/x.json\n"
    assert _scan(body) == [(3, "/tmp/x.json")], _scan(body)


# --- what is deliberately NOT a finding ------------------------------------


def test_the_bash_runner_temp_form_is_not_a_finding():
    body = (
        '          tmpd="${RUNNER_TEMP:-/tmp}"\n'
        '          : > "$tmpd/smoke-dispatched.tsv"\n'
        '          } > "${RUNNER_TEMP:-/tmp}/report.md"\n'
    )
    assert _scan(body) == [], _scan(body)


def test_the_node_runner_temp_form_is_not_a_finding():
    body = (
        '            const tmpd = process.env.RUNNER_TEMP || "/tmp";\n'
        '            fs.writeFileSync(tmpd + "/targets.json", "[]");\n'
        "              const headPath = `${tmpd}/probes/${safe}.head`;\n"
    )
    assert _scan(body) == [], _scan(body)


def test_a_comment_line_is_not_a_finding():
    """#1019: four guards in 48h flagged prose about the thing they catch, and
    this guard's own CI step has to name the paths to explain itself."""
    body = (
        "          # RUNNER_TEMP, not /tmp/entries.json, so two local runs cannot\n"
        "          # clobber each other (was /tmp/smoke-body and /tmp/smoke-err).\n"
        "            // const targets = require('/tmp/targets.json');\n"
    )
    assert _scan(body) == [], _scan(body)


def test_a_trailing_comment_on_a_line_of_code_is_still_scanned():
    """Stated so the over-report is a decision and not a surprise: the exclusion
    is by LEADING token, because deciding where a comment starts on a line of
    shell means deciding what is inside a string."""
    assert _scan("          cat /tmp/x  # the old path\n"), "trailing-comment line skipped"


def test_a_bare_tmp_with_no_segment_is_not_a_finding():
    """`TMPDIR=/tmp` names the directory, not a shared file, and the accepted
    RUNNER_TEMP fallbacks end exactly there."""
    assert _scan('          export TMPDIR=/tmp\n') == [], _scan('          export TMPDIR=/tmp\n')


# --- the population, and the freeze ----------------------------------------


def test_the_tree_is_clean():
    findings, errors, scanned = guard.scan_all()
    assert not errors, errors
    assert not findings, [str(f) for f in findings]
    assert scanned > 100, f"only {scanned} files scanned — the walk found almost nothing"


def test_the_scan_reads_real_bodies_and_not_an_empty_set():
    """A scanner that collected no bodies would report the tree clean forever.

    Pinned against a body this repo actually ships rather than a count.
    """
    doc = guard.yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "738-fleet-smoke-engine-drift-audit.yml")
        .read_text(encoding="utf-8")
    )
    bodies = guard.executable_bodies(doc)
    assert bodies, "no executable bodies extracted from 738"
    assert any("fetch_hash()" in b for _, _, b in bodies), (
        "738's gather body was not among the extracted bodies"
    )


def test_the_freeze_is_empty():
    """An entry here is an explicit exception. If one is ever added, this test
    is where the reader is told the normal state changed."""
    assert guard.KNOWN_FIXED_TMP_PATHS == {}, guard.KNOWN_FIXED_TMP_PATHS


def test_a_composite_action_is_frozen_by_path_not_by_action_yml():
    """All six composite actions are named `action.yml`, so a basename key
    would name every one of them at once — one entry silently excusing the
    other five, and an error line that cannot say which action it meant.
    Raised by Copilot on #1256."""
    key = guard.freeze_key(".github/actions/whmcs-secrets-from-kv/action.yml")
    assert key == ".github/actions/whmcs-secrets-from-kv/action.yml", key
    other = guard.freeze_key(".github/actions/cloudflare-tokens-from-kv/action.yml")
    assert key != other, "two different composite actions collapsed to one freeze key"


def test_a_workflow_is_still_frozen_by_basename():
    """The sibling guards' convention, unchanged."""
    assert guard.freeze_key(".github/workflows/738-fleet-smoke-engine-drift-audit.yml") == (
        "738-fleet-smoke-engine-drift-audit.yml"
    )


def test_a_windows_style_source_is_normalized_before_keying():
    """`Path.relative_to` yields backslashes on Windows; the key must not
    depend on which host ran the scan."""
    assert guard.freeze_key(r".github\actions\whmcs-secrets-from-kv\action.yml") == (
        ".github/actions/whmcs-secrets-from-kv/action.yml"
    )


def test_a_composite_action_freeze_entry_resolves_against_the_tree():
    """A path key still has its existence checked — the freeze must fail when
    it stops describing the tree, whichever population the entry names."""
    real = ".github/actions/whmcs-secrets-from-kv/action.yml"
    assert (REPO_ROOT / real).is_file(), "fixture path is stale, not the guard"
    assert guard.compare({real: ["/tmp/x"]}, {real: ("/tmp/x",)}) == []
    errors = guard.compare({}, {".github/actions/nope/action.yml": ("/tmp/x",)})
    assert errors and "no such file" in errors[0], errors


def test_a_freeze_entry_naming_a_missing_file_is_an_error():
    errors = guard.compare({}, {"999-not-a-workflow.yml": ("/tmp/x",)})
    assert errors and "no such file" in errors[0], errors


def test_a_stale_freeze_entry_is_an_error():
    """The entry names a real file but a path that is no longer in it."""
    errors = guard.compare(
        {"738-fleet-smoke-engine-drift-audit.yml": []},
        {"738-fleet-smoke-engine-drift-audit.yml": ("/tmp/entries.json",)},
    )
    assert errors and "stale" in errors[0].lower(), errors


def test_a_frozen_path_is_not_reported_but_an_unfrozen_one_in_the_same_file_is():
    errors = guard.compare(
        {"738-fleet-smoke-engine-drift-audit.yml": ["/tmp/entries.json", "/tmp/new.json"]},
        {"738-fleet-smoke-engine-drift-audit.yml": ("/tmp/entries.json",)},
    )
    assert len(errors) == 1, errors
    assert "/tmp/new.json" in errors[0], errors
    assert "/tmp/entries.json" not in errors[0], errors


# --- fail closed -----------------------------------------------------------


def test_unparseable_yaml_is_reported_not_skipped():
    with tempfile.TemporaryDirectory() as td:
        bad = pathlib.Path(td) / "bad.yml"
        bad.write_text("jobs: [\n  unclosed\n", encoding="utf-8")
        findings, errors = guard.scan_file(bad)
        assert not findings
        assert errors and "could not be parsed" in errors[0], errors


def test_a_non_mapping_document_is_reported():
    with tempfile.TemporaryDirectory() as td:
        scalar = pathlib.Path(td) / "scalar.yml"
        scalar.write_text("just a string\n", encoding="utf-8")
        _findings, errors = guard.scan_file(scalar)
        assert errors and "did not parse to a mapping" in errors[0], errors


def test_a_non_string_run_body_is_still_examined():
    """`run:` coerced to a mapping by a templating mistake must not read as
    clean. It is stringified and scanned rather than skipped."""
    doc = {"jobs": {"j": {"steps": [{"name": "s", "run": {"nested": "/tmp/x.json"}}]}}}
    bodies = guard.executable_bodies(doc)
    assert bodies, "a non-string run: was dropped"
    assert _scan(bodies[0][2]), "a non-string run: was not scanned"


# --- the premise, reproduced ------------------------------------------------


def _concurrent_rc(script: str, envs: list[dict]) -> list[int]:
    """Run one script once per env, all at the same time; return the exit codes.

    The envs must DIFFER in MARKER. Two writers of the same bytes cannot
    interfere with each other, and a fixture that passed both processes the same
    marker would report the race as absent — which is how the first draft of
    this test read a working collision as `[0, 0]`.
    """
    procs = [
        subprocess.Popen(
            ["bash", "-c", script],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for env in envs
    ]
    return [p.wait(timeout=120) for p in procs]


# A body of the shape the guard rejects: write a marker, settle, require it back.
# The settle is what makes the race deterministic rather than merely likely.
_COLLIDING = """
set -eu
f={path}
printf '%s' "$MARKER" > "$f"
sleep 0.5
test "$(cat "$f")" = "$MARKER"
"""


def test_two_concurrent_copies_of_a_body_writing_a_fixed_path_collide():
    """The premise. Two processes, one hardcoded path, and at least one loses.

    Runs under the module's own RUNNER_TEMP so the "fixed" path is fixed
    relative to this test rather than to the machine — the point is the SHARED
    name, and putting a real `/tmp/` file in a repo's test suite is the thing
    being argued against.
    """
    shared = f"{runner_temp()}/collide-fixture.txt"
    script = _COLLIDING.format(path=f'"{shared}"')
    codes = _concurrent_rc(script, [child_env(MARKER="A"), child_env(MARKER="B")])
    assert any(rc != 0 for rc in codes), (
        f"two concurrent writers of one fixed path both succeeded ({codes}) — the "
        f"fixture is not reproducing the race this guard exists for"
    )


def test_the_same_body_under_runner_temp_survives_concurrency():
    """The fix, on the same fixture: distinct RUNNER_TEMP, no interference.

    The overrides go through `forward_slashes` for the same reason
    `runner_temp()` does: `TemporaryDirectory()` yields a platform-native path,
    and a `C:\\...` value handed to the bash body below has its backslashes
    eaten as escapes by MSYS. A test that hands bash a path bash cannot read
    measures the fixture, not the fix. Raised by Copilot on #1256 — the first
    round normalized the harness and left its own override un-normalized.
    """
    script = _COLLIDING.format(path='"${RUNNER_TEMP:-/tmp}/collide-fixture.txt"')
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        codes = _concurrent_rc(
            script,
            [
                child_env(RUNNER_TEMP=forward_slashes(a), MARKER="A"),
                child_env(RUNNER_TEMP=forward_slashes(b), MARKER="B"),
            ],
        )
    assert codes == [0, 0], f"isolated writers still interfered: {codes}"


# --- the harness half of the fix -------------------------------------------


def test_child_env_hands_the_child_a_runner_temp():
    env = child_env()
    assert env.get("RUNNER_TEMP") == runner_temp(), env.get("RUNNER_TEMP")
    assert pathlib.Path(env["RUNNER_TEMP"]).is_dir(), env["RUNNER_TEMP"]


def test_runner_temp_is_spelled_with_forward_slashes():
    """The value is handed to `bash` step bodies as `"$tmpd/file"`. On Windows
    `mkdtemp` returns `C:\\Users\\...`, MSYS bash eats the backslashes as
    escapes, and the redirect writes to a mangled path nobody reads — the
    failure CLAUDE.md records for any Windows path handed to git-bash. No-op on
    POSIX. Raised by Copilot on #1256."""
    # The rule, on a synthetic Windows path — this is the half that
    # discriminates from a Linux host, where `mkdtemp` never emits a backslash
    # and an inspection of the live value would pass with no normalization.
    assert forward_slashes(r"C:\Users\x\Temp\wf-logic-runner-temp-1") == (
        "C:/Users/x/Temp/wf-logic-runner-temp-1"
    )
    # ...and the live value, which must also still be a real directory.
    value = runner_temp()
    assert "\\" not in value, value
    assert pathlib.Path(value).is_dir(), f"normalized path is not openable: {value}"


def _runs_a_bash_body(tree: ast.Module) -> bool:
    """True if the module launches `bash` — i.e. its RUNNER_TEMP reaches a shell
    that treats a backslash as an escape.

    Deliberately a literal-argv check and nothing cleverer: a module that builds
    its interpreter name dynamically is not something a source scan can resolve,
    and guessing would put the rule back on the correct-code side of the line.
    Such a module is simply not covered, which this docstring states rather than
    letting the reader infer coverage the scan does not have.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", "") not in {"run", "Popen"}:
            continue
        for arg in node.args[:1]:
            if isinstance(arg, ast.List) and arg.elts:
                first = arg.elts[0]
                if isinstance(first, ast.Constant) and first.value == "bash":
                    return True
    return False


def test_no_module_hands_child_env_an_unnormalized_runner_temp():
    """Every `child_env(RUNNER_TEMP=...)` override must be normalized.

    Checked statically, against the source, because the defect is invisible
    from the platform CI runs on: `TemporaryDirectory()` yields forward slashes
    on Linux, so a module handing bash a raw `C:\\...` path passes here and
    fails only on the Windows git-bash host. That asymmetry is #943's whole
    lesson, and `test_child_env_inheritance` already enforces its sibling rule
    the same way.

    The first round of this PR normalized `runner_temp()` and left this very
    module's own override raw — which is why the rule is enforced over the
    directory rather than pinned to the one call site that was wrong.

    SCOPED TO MODULES THAT RUN A `bash` BODY, and that qualifier is the rule,
    not a convenience. `test_101_domain_status_wiring` hands `RUNNER_TEMP` to a
    **PowerShell** step, where a native `C:\\...` path is exactly right and
    forward slashes would be the odd spelling. A blanket rule flags it, and a
    guard that reports correct code as a violation is one somebody switches off
    (#1019). The consumer decides, so the scan asks who the consumer is.
    """
    allowed = {"forward_slashes", "runner_temp"}
    offenders = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        if not _runs_a_bash_body(tree):
            continue
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "child_env"):
                continue
            for kw in node.keywords:
                if kw.arg != "RUNNER_TEMP":
                    continue
                value = kw.value
                if isinstance(value, ast.Constant):
                    continue  # a literal is written by hand and visible in review
                if isinstance(value, ast.Call) and getattr(value.func, "id", "") in allowed:
                    continue
                offenders.append(f"{path.name}:{value.lineno} {ast.unparse(value)}")
    assert not offenders, (
        "these child_env(RUNNER_TEMP=...) overrides are handed to bash step bodies "
        f"un-normalized, so MSYS eats the backslashes on Windows: {offenders}"
    )


def test_child_env_runner_temp_can_still_be_overridden():
    """A module that wants its own scratch directory must be able to say so."""
    assert child_env(RUNNER_TEMP="/somewhere/else")["RUNNER_TEMP"] == "/somewhere/else"


def test_the_720_bodies_that_were_fixed_read_runner_temp():
    """Spot-check the two bodies whose modules execute them, so a revert to a
    fixed path fails here as well as in the tree-wide scan."""
    gather = step_run("738-fleet-smoke-engine-drift-audit.yml", "audit", "Gather smoke-engine hashes")
    assert "RUNNER_TEMP" in gather, "738's gather step no longer reads RUNNER_TEMP"
    dispatch = step_run(
        "120-bulk-cutover-to-github-pages.yml", "post-cutover-smoke", "Dispatch Post-Deploy Smoke"
    )
    assert "RUNNER_TEMP" in dispatch, "120's dispatch step no longer reads RUNNER_TEMP"


# --- wiring -----------------------------------------------------------------


def test_the_guard_is_wired_into_ci():
    """A checker nobody runs is the failure mode this whole class is about."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-workflow-fixed-tmp-paths.py" in ci, (
        "check-workflow-fixed-tmp-paths.py must run in 722-ci.yml, or the rule is "
        "documentation"
    )


def test_the_guard_installs_its_own_yaml_dependency_in_ci():
    """The runner image only happens to preinstall PyYAML; without the install
    an image that drops it turns this guard into an ImportError, which is a red
    build that says nothing about temp paths."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    block = ci.split("Validate no workflow body names a fixed temp path", 1)[1]
    block = block.split("scripts/check-workflow-fixed-tmp-paths.py", 1)[0]
    assert "pip install --quiet pyyaml" in block, (
        "the fixed-temp-path step must install PyYAML defensively, like its siblings"
    )


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
