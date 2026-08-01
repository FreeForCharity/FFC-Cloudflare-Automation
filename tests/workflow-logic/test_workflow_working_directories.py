"""Guard: every `working-directory:` names a directory that will exist (#972).

Why this module exists. #858 shipped `working-directory: target-repo` into the
dry-run branch of `702-ffc-ex-clone-deploy.yml`. No such directory is ever made
in that job — the target repo is checked out with `path: ffc-ex`, and `target`
is the *step id* that produces the repo name. Every dry run would have died
before the shell started. The YAML was valid, `actionlint` was silent, the
catalog regenerated and every existing guard passed; it was caught by hand.

Same shape as #945 and #929: the defect cannot turn CI red, because the only
code path that reaches it is gated and had never run. So it is enforced
statically against the source by `scripts/check-workflow-working-directories.py`.

`test_the_scanner_flags_the_858_defect_in_the_real_702` is the load-bearing
test — it puts the real defect back into the real workflow and requires the
finding. Without it this module only asserts a happy path: the tree is clean by
construction after the fix, so a scanner returning `[]` unconditionally would
pass every other assertion here. That is the lesson #943's guard recorded and
AGENTS.md restates as "reintroduce the defect it claims to catch".
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_SPEC = importlib.util.spec_from_file_location(
    "check_workflow_working_directories",
    REPO_ROOT / "scripts" / "check-workflow-working-directories.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)

find_findings = _MOD.find_findings
scan_repo = _MOD.scan_repo
BAD_DIR = _MOD.BAD_DIR
UNRESOLVABLE = _MOD.UNRESOLVABLE
UNPARSEABLE = _MOD.UNPARSEABLE

# A fixed directory set, so the unit cases do not depend on the repo tree.
FAKE_DIRS = {"scripts", "docs", "automation", "automation/lib"}

WF_702 = REPO_ROOT / ".github" / "workflows" / "702-ffc-ex-clone-deploy.yml"
DRY_RUN_STEP = "Report what the write would do (dry run)"


def _wf(steps: str, job_extra: str = "") -> str:
    return "name: t\non:\n  workflow_dispatch:\njobs:\n  build:\n" + job_extra + (
        "    runs-on: ubuntu-latest\n    steps:\n" + steps
    )


# --------------------------------------------------------------------------
# The load-bearing one: put the real defect back and require the finding.
# --------------------------------------------------------------------------


def test_the_scanner_flags_the_858_defect_in_the_real_702():
    """`working-directory: target-repo` on 702's dry-run step must be reported."""
    lines = WF_702.read_text(encoding="utf-8").splitlines()

    anchor = next(i for i, line in enumerate(lines) if DRY_RUN_STEP in line)
    target = next(
        i
        for i in range(anchor, anchor + 8)
        if lines[i].strip().startswith("working-directory:")
    )
    assert lines[target].strip() == "working-directory: ffc-ex", (
        f"702's dry-run step no longer reads `working-directory: ffc-ex` "
        f"({lines[target].strip()!r}) — this test's mutation is stale"
    )
    lines[target] = lines[target].replace("ffc-ex", "target-repo")

    found = find_findings("\n".join(lines), WF_702.name, FAKE_DIRS)
    assert len(found) == 1, f"expected exactly the #858 defect, got: {[str(f) for f in found]}"
    only = found[0]
    assert only.kind == BAD_DIR, only.kind
    assert only.job == "clone-deploy", only.job
    assert only.step == DRY_RUN_STEP, only.step
    assert only.value == "target-repo", only.value
    assert only.line == target + 1, f"pointed at line {only.line}, defect is on {target + 1}"


def test_the_tree_is_clean():
    """Every working-directory: in every workflow resolves today."""
    found = scan_repo(REPO_ROOT)
    assert found == [], "working-directory: findings:\n" + "\n".join(f"  {f}" for f in found)


# --------------------------------------------------------------------------
# What must NOT be a finding — the false-positive boundary.
# --------------------------------------------------------------------------


def test_a_step_with_no_working_directory_is_not_a_finding():
    found = find_findings(_wf("      - run: echo hi\n"), "t.yml", FAKE_DIRS)
    assert found == [], [str(f) for f in found]


def test_the_workspace_root_is_not_a_finding():
    for root in (".", "./", "${{ github.workspace }}"):
        steps = f"      - run: echo hi\n        working-directory: {root!r}\n"
        found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
        assert found == [], f"{root} is the workspace root: {[str(f) for f in found]}"


def test_a_repository_directory_is_not_a_finding():
    steps = "      - run: ls\n        working-directory: scripts\n"
    assert find_findings(_wf(steps), "t.yml", FAKE_DIRS) == []


def test_a_checkout_path_from_an_earlier_step_is_not_a_finding():
    steps = (
        "      - uses: actions/checkout@v7.0.1\n        with:\n          path: ffc-ex\n"
        "      - run: npm ci\n        working-directory: ffc-ex\n"
    )
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert found == [], [str(f) for f in found]


def test_a_subdirectory_of_a_checkout_is_not_a_finding():
    """The contents of a foreign checkout are not in this tree; accept the prefix."""
    steps = (
        "      - uses: actions/checkout@v7.0.1\n        with:\n          path: ffc-ex\n"
        "      - run: npm ci\n        working-directory: ffc-ex/apps/web\n"
    )
    assert find_findings(_wf(steps), "t.yml", FAKE_DIRS) == []


def test_a_mkdir_in_an_earlier_step_is_not_a_finding():
    steps = (
        "      - run: mkdir -p build/out\n"
        "      - run: ls\n        working-directory: build/out\n"
    )
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert found == [], [str(f) for f in found]


def test_a_quoted_mkdir_operand_with_a_space_stays_one_directory():
    """`.split()` would make `"build dir"` into `build` + `dir` — two wrong
    answers at once: it invents directories nobody created, and loses the one
    that was, so a correct working-directory becomes a false finding."""
    steps = (
        '      - run: mkdir -p "build dir"\n'
        '      - run: ls\n        working-directory: "build dir"\n'
    )
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert found == [], [str(f) for f in found]


def test_an_unbalanced_quote_in_a_mkdir_does_not_crash_the_scan():
    """shlex raises on unbalanced quotes; the scan must degrade, not die."""
    steps = '      - run: mkdir -p "build\n      - run: ls\n'
    find_findings(_wf(steps), "t.yml", FAKE_DIRS)  # must not raise


def test_new_item_directory_counts_as_creating_it():
    steps = (
        "      - shell: pwsh\n        run: New-Item -ItemType Directory -Path artifacts\n"
        "      - run: ls\n        working-directory: artifacts\n"
    )
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert found == [], [str(f) for f in found]


# --------------------------------------------------------------------------
# What must BE a finding — including the ordering rule.
# --------------------------------------------------------------------------


def test_a_directory_nothing_creates_is_a_finding():
    steps = "      - run: ls\n        working-directory: target-repo\n"
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert len(found) == 1 and found[0].kind == BAD_DIR, [str(f) for f in found]
    assert found[0].value == "target-repo"


def test_a_mkdir_in_a_LATER_step_is_still_a_finding():
    """The directory does not exist yet when the step runs — order is the rule."""
    steps = (
        "      - run: ls\n        working-directory: build\n"
        "      - run: mkdir build\n"
    )
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert len(found) == 1, f"a later mkdir must not excuse an earlier step: {found}"


def test_a_checkout_LATER_in_the_job_is_still_a_finding():
    steps = (
        "      - run: ls\n        working-directory: ffc-ex\n"
        "      - uses: actions/checkout@v7.0.1\n        with:\n          path: ffc-ex\n"
    )
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert len(found) == 1, f"a later checkout must not excuse an earlier step: {found}"


def test_job_level_defaults_are_checked_too():
    job_extra = "    defaults:\n      run:\n        working-directory: nowhere\n"
    found = find_findings(_wf("      - run: ls\n", job_extra), "t.yml", FAKE_DIRS)
    assert len(found) == 1 and "defaults.run" in found[0].step, [str(f) for f in found]


def test_a_default_is_not_proven_by_a_mkdir_that_runs_under_it():
    """The circular case: that `mkdir` step itself starts in `build`.

    `defaults.run` applies to the mkdir step too, so it fails to start and
    never creates anything. Treating it as proof is fail-open — the guard
    accepting a default precisely because of a step that can never execute.
    """
    job_extra = "    defaults:\n      run:\n        working-directory: build\n"
    found = find_findings(_wf("      - run: mkdir build\n", job_extra), "t.yml", FAKE_DIRS)
    assert len(found) == 1, f"a mkdir running under the default cannot prove it: {found}"
    assert found[0].value == "build"


def test_a_default_IS_proven_by_a_checkout_before_the_first_run_step():
    """`defaults.run` does not apply to `uses:` steps, so a checkout can create it."""
    job_extra = "    defaults:\n      run:\n        working-directory: ffc-ex\n"
    steps = (
        "      - uses: actions/checkout@v7.0.1\n        with:\n          path: ffc-ex\n"
        "      - run: npm ci\n"
    )
    found = find_findings(_wf(steps, job_extra), "t.yml", FAKE_DIRS)
    assert found == [], [str(f) for f in found]


def test_a_default_proven_only_by_a_LATER_checkout_is_a_finding():
    """Order holds for defaults as well: the first run step gets there first."""
    job_extra = "    defaults:\n      run:\n        working-directory: ffc-ex\n"
    steps = (
        "      - run: npm ci\n"
        "      - uses: actions/checkout@v7.0.1\n        with:\n          path: ffc-ex\n"
    )
    found = find_findings(_wf(steps, job_extra), "t.yml", FAKE_DIRS)
    assert len(found) == 1, f"the checkout lands after the run step: {found}"


def test_a_bad_default_is_reported_once_per_job_not_once_per_step():
    job_extra = "    defaults:\n      run:\n        working-directory: nowhere\n"
    steps = "      - run: a\n      - run: b\n      - run: c\n"
    found = find_findings(_wf(steps, job_extra), "t.yml", FAKE_DIRS)
    assert len(found) == 1, f"one default, one finding: {[str(f) for f in found]}"


def test_a_step_overriding_a_bad_default_still_runs_and_can_create_a_directory():
    """The override is what makes the mkdir reachable — so it counts."""
    job_extra = "    defaults:\n      run:\n        working-directory: scripts\n"
    steps = (
        "      - run: mkdir build\n        working-directory: docs\n"
        "      - run: ls\n        working-directory: build\n"
    )
    found = find_findings(_wf(steps, job_extra), "t.yml", FAKE_DIRS)
    assert found == [], [str(f) for f in found]


def test_a_mkdir_in_a_step_that_cannot_start_does_not_count():
    """A step whose own working-directory is unproven never executes its mkdir."""
    steps = (
        "      - run: mkdir build\n        working-directory: nowhere\n"
        "      - run: ls\n        working-directory: build\n"
    )
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert len(found) == 2, f"both the dead step and the unmade dir: {[str(f) for f in found]}"


# --------------------------------------------------------------------------
# Fail closed: unreadable input is reported, never skipped.
# --------------------------------------------------------------------------


def test_unparseable_yaml_is_reported_not_skipped():
    found = find_findings("name: t\njobs:\n  build:\n   - [unclosed\n", "bad.yml", FAKE_DIRS)
    assert len(found) == 1 and found[0].kind == UNPARSEABLE, [str(f) for f in found]


def test_a_non_mapping_document_is_reported():
    found = find_findings("- just\n- a list\n", "bad.yml", FAKE_DIRS)
    assert len(found) == 1 and found[0].kind == UNPARSEABLE, [str(f) for f in found]


def test_an_unresolvable_expression_is_reported():
    steps = "      - run: ls\n        working-directory: ${{ inputs.dir }}\n"
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert len(found) == 1 and found[0].kind == UNRESOLVABLE, [str(f) for f in found]


def test_a_non_literal_checkout_path_is_reported():
    """An unreadable checkout path makes the whole job's directory set unprovable."""
    steps = (
        "      - uses: actions/checkout@v7.0.1\n"
        "        with:\n          path: ${{ steps.target.outputs.name }}\n"
        "      - run: ls\n        working-directory: scripts\n"
    )
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert len(found) == 1 and found[0].kind == UNRESOLVABLE, [str(f) for f in found]


def test_a_non_string_checkout_path_is_reported_not_coerced():
    """`str(path)` would turn an unreadable value into an apparent literal.

    A coerced non-string does not match the expression pattern, so it would be
    accepted as a proven directory — the fail-open shape this guard exists to
    avoid, inside the guard itself.
    """
    steps = (
        "      - uses: actions/checkout@v7.0.1\n        with:\n          path: 42\n"
        "      - run: ls\n        working-directory: scripts\n"
    )
    found = find_findings(_wf(steps), "t.yml", FAKE_DIRS)
    assert len(found) == 1 and found[0].kind == UNRESOLVABLE, [str(f) for f in found]


def test_the_guard_installs_its_own_yaml_dependency_in_ci():
    """The guard is the first PyYAML consumer in the job; the runner image only
    happens to preinstall it, so the step must not depend on that."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    step = ci.split("python3 scripts/check-workflow-working-directories.py")[0]
    tail = step[-600:]
    assert "pip install --quiet pyyaml" in tail, (
        "the working-directory guard imports yaml and runs before the "
        "workflow-logic step that installs it — it must install it itself"
    )


def test_the_repo_walk_prunes_ignored_directories_instead_of_filtering_them():
    """Filtering after the fact returns the right set while still descending.

    On a full clone `.git` holds thousands of loose-object directories, stat-ed
    on every CI run for a result thrown away. Asserting the returned set cannot
    see the difference — only watching the walk can — so this spies on it.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / ".git" / "objects" / "ab").mkdir(parents=True)
        (root / "node_modules" / "pkg").mkdir(parents=True)
        (root / "scripts" / "lib").mkdir(parents=True)

        visited: list[str] = []
        real_walk = _MOD.os.walk

        def spy(top, *a, **kw):
            for current, subdirs, files in real_walk(top, *a, **kw):
                visited.append(pathlib.Path(current).name)
                yield current, subdirs, files

        _MOD.os.walk = spy
        try:
            dirs = _MOD._repo_dirs(root)
        finally:
            _MOD.os.walk = real_walk

    assert dirs == {"scripts", "scripts/lib"}, dirs
    assert ".git" not in visited, f"descended into .git — pruning is not happening: {visited}"
    assert "node_modules" not in visited, f"descended into node_modules: {visited}"


def test_the_guard_is_wired_into_ci():
    """A checker nobody runs is the failure mode this whole class is about."""
    ci = (REPO_ROOT / ".github" / "workflows" / "722-ci.yml").read_text(encoding="utf-8")
    assert "scripts/check-workflow-working-directories.py" in ci, (
        "check-workflow-working-directories.py must run in 722-ci.yml, "
        "or the rule is documentation"
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
