#!/usr/bin/env python3
"""Tests for `scripts/verify-conductor-clones.py` (Conductor run 231).

The subject is a verifier, so — as in `test_conductor_hook_wiring.py` — the
tests are about the two ways a verifier is worthless: it passes a workspace that
is broken (false green) or fails one that is fine (false red). Both halves are
covered, and the false-green half carries the weight, because the defect this
script exists for is **silence**: a clone parked on a feature branch serves real
files from a real checkout at a real commit, and `git pull` on it prints
`Already up to date`. Nothing errors.

The load-bearing test here is `test_a_non_main_default_branch_is_honoured`. Every
other false-green case can be satisfied by a script that simply compares HEAD
against the literal string "main" — and such a script would be wrong for any repo
whose default branch is not `main`, while passing this whole module. That test is
what makes the rest evidence rather than decoration.

Every fixture is a real git repository built under a `TemporaryDirectory`.
Nothing here mutates the repo tree: in-place-mutate-and-restore is what
CLAUDE.md/L182 records as restoring against the wrong baseline, and a module that
leaves a stray file in the working tree is #1023. Cleanup errors are ignored
because Windows will not unlink a pack file another process still holds, which
is one of the four host causes behind this suite's local-red baseline (L194) —
and a teardown `PermissionError` would make this module red for a reason that
has nothing to do with what it asserts.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

# Every Python child below is spawned with an INLINE
# `env={**os.environ, "PYTHONIOENCODING": "utf-8"}` rather than a shared module
# constant. Pinning only the parent's `encoding=` is not enough (#962): on
# Windows the child still emits cp1252, the decode raises on subprocess's reader
# thread, `proc.stdout` comes back None, and the traceback blames the caller's
# arithmetic. `scripts/check-subprocess-encoding.py` enforces it and caught this
# module twice before it was pushed -- the second time because a constant is a
# form the scanner cannot read, so the pin was correct and unprovable. Keeping
# the literal at each call site is what makes the guard able to see it.

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
SCRIPT = REPO_ROOT / "scripts" / "verify-conductor-clones.py"


def _git(cwd: pathlib.Path, *args: str) -> None:
    """Run git, raising with the real output on failure.

    Full environment inherited: CLAUDE.md records that a scrubbed `env=` dict
    breaks `bash`/`node` on this host, and that the resulting exit 1 is
    indistinguishable from the failure a test is actually looking for.
    """
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"fixture git failed: git {' '.join(args)} -> {proc.returncode}\n"
            f"{proc.stdout}\n{proc.stderr}"
        )


def _workspace(
    root: pathlib.Path, name: str = "FFC-EX-canary", branch: str = "main"
) -> pathlib.Path:
    """Build `<root>/repos/<name>`: a clone of a bare origin whose default is `branch`.

    `origin/HEAD` is set explicitly with `git remote set-head`, because a clone of
    a bare repository does not always leave it resolvable — and an unresolvable
    `origin/HEAD` is a case the script deliberately reports as UNVERIFIED, so a
    fixture that left it unset would quietly test the wrong branch of the code.
    """
    origin = root / "origin.git"
    _git(root, "init", "--bare", "--initial-branch", branch, str(origin))

    seed = root / "seed"
    seed.mkdir()
    _git(seed, "init", "--initial-branch", branch)
    _git(seed, "config", "user.email", "conductor@example.invalid")
    _git(seed, "config", "user.name", "Conductor Test")
    (seed / "AGENTS.md").write_text("source of truth\n", encoding="utf-8")
    _git(seed, "add", "AGENTS.md")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-u", "origin", branch)

    repos = root / "repos"
    repos.mkdir(exist_ok=True)
    clone = repos / name
    _git(root, "clone", str(origin), str(clone))
    _git(clone, "config", "user.email", "conductor@example.invalid")
    _git(clone, "config", "user.name", "Conductor Test")
    _git(clone, "remote", "set-head", "origin", branch)
    return clone


def _run(workspace: pathlib.Path, clone: str = "FFC-EX-canary") -> tuple[int, str, dict]:
    """Invoke the verifier for one named clone; return (rc, prose, parsed json)."""
    common = [
        sys.executable,
        str(SCRIPT),
        "--workspace",
        str(workspace),
        "--clone",
        clone,
    ]
    prose = subprocess.run(
        common,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    asjson = subprocess.run(
        [*common, "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        parsed = json.loads(asjson.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - diagnostic path
        raise AssertionError(
            f"--json did not emit JSON (rc={asjson.returncode}): "
            f"{asjson.stdout[:300]!r} / {asjson.stderr[:300]!r}"
        ) from exc
    assert prose.returncode == asjson.returncode, (
        "prose and --json must agree on the exit code, else a caller's verdict "
        f"depends on its output format: {prose.returncode} vs {asjson.returncode}"
    )
    return prose.returncode, prose.stdout + prose.stderr, parsed


def _status(parsed: dict, clone: str = "FFC-EX-canary") -> str:
    rows = [r for r in parsed["rows"] if r["clone"] == clone]
    assert rows, f"no row for {clone} in {parsed!r}"
    return rows[0]["status"]


def test_a_clone_on_its_default_branch_passes() -> None:
    """The false-red half. A clean, level clone on its default branch must exit 0."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        _workspace(root)
        rc, out, parsed = _run(root)
        assert rc == 0, f"a clean clone on `main` must pass, got rc={rc}\n{out}"
        assert _status(parsed) == "ok", parsed


def test_a_passing_run_still_prints_a_verdict_line() -> None:
    """Silence on success leaves "clean" and "the check never ran" identical.

    Same reason `verify-conductor-hooks.py` prints `HOOKS:` in both directions:
    the line goes in the run's START comment, and a verifier that only speaks up
    when it fails cannot be distinguished from one that was never invoked.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        _workspace(root)
        rc, out, _ = _run(root)
        assert rc == 0, out
        assert "CLONES:" in out, f"a successful run must emit a CLONES: line, got {out!r}"


def test_a_clone_parked_on_a_feature_branch_fails() -> None:
    """The run-231 defect itself: parked on a feature branch, read as the source of truth."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        clone = _workspace(root)
        _git(clone, "checkout", "-b", "fix/rule6-segment-scope")
        rc, out, parsed = _run(root)
        assert rc != 0, (
            "a clone parked on a feature branch must fail -- this is the defect "
            f"the script exists for\n{out}"
        )
        assert _status(parsed) == "parked", parsed
        assert "fix/rule6-segment-scope" in out, (
            f"the verdict must name the branch actually checked out, got {out!r}"
        )


def test_a_detached_head_fails() -> None:
    """Detached at the right commit is still wrong: a branch cut here is cut from a commit."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        clone = _workspace(root)
        _git(clone, "checkout", "--detach", "HEAD")
        rc, out, parsed = _run(root)
        assert rc != 0, f"a detached HEAD must not read as on-branch\n{out}"
        assert _status(parsed) == "parked", parsed
        # Assert the *detail*, not just the status. Without this the test is
        # satisfied by the generic `head != branch` arm -- which also fails, and
        # fails for the wrong reason, so deleting the dedicated detached-HEAD
        # branch would leave this test green. Measured: the mutation harness for
        # this module reported exactly that MISS before this line existed.
        assert "detached" in out.lower(), (
            f"the verdict must say the checkout is detached, got {out!r}"
        )


def test_a_clone_behind_origin_fails() -> None:
    """A stale checkout reads old docs exactly as confidently as a parked one."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        clone = _workspace(root)
        seed = root / "seed"
        (seed / "AGENTS.md").write_text("source of truth, revised\n", encoding="utf-8")
        _git(seed, "commit", "-am", "advance origin")
        _git(seed, "push", "origin", "main")
        _git(clone, "fetch", "origin")  # remote-tracking ref moves; HEAD does not
        rc, out, parsed = _run(root)
        assert rc != 0, f"a clone 1 behind origin/main must fail\n{out}"
        assert _status(parsed) == "parked", parsed
        assert "behind" in out, f"the verdict must say it is behind, got {out!r}"


def test_a_dirty_clone_fails() -> None:
    """Uncommitted edits are how a previous run's scratch work is read as repo state."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        clone = _workspace(root)
        (clone / "AGENTS.md").write_text("locally edited\n", encoding="utf-8")
        rc, out, parsed = _run(root)
        assert rc != 0, f"a dirty clone must fail\n{out}"
        assert _status(parsed) == "parked", parsed


def test_an_unresolvable_origin_head_is_unverified_not_ok() -> None:
    """The script's own false green, closed off.

    With `origin/HEAD` deleted there is no way to know the default branch. A
    verifier that fell back to the literal "main" would answer `ok` here for a
    clone whose branch nobody has established -- reporting a verification that
    did not happen. It must say UNVERIFIED and exit non-zero, which is the same
    instruction to the run as `parked`: stop and fix the workspace.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        clone = _workspace(root)
        # `symbolic-ref -d`, NOT `update-ref -d`. Measured on this host: the
        # latter DEREFERENCES, so it deletes `refs/remotes/origin/main` and
        # leaves `origin/HEAD` still resolving to it -- `symbolic-ref --short`
        # exits 0 naming a branch that no longer exists. The first draft of this
        # test used it and so exercised the "cannot compare HEAD with
        # origin/<branch>" arm instead of the one its name claims; the mutation
        # harness caught it by missing the `return "main"` fallback entirely.
        _git(clone, "symbolic-ref", "-d", "refs/remotes/origin/HEAD")
        rc, out, parsed = _run(root)
        assert rc != 0, f"an unresolvable origin/HEAD must not pass\n{out}"
        assert _status(parsed) == "unverified", (
            f"must be UNVERIFIED, not ok and not parked: {parsed!r}"
        )
        assert "origin/HEAD" in out, (
            f"the verdict must name origin/HEAD as the thing it could not read, got {out!r}"
        )


def test_a_dangling_origin_head_is_unverified_not_ok() -> None:
    """The sibling case the fixture bug above exposed, now covered deliberately.

    `origin/HEAD` can resolve to a branch that does not exist -- which is the
    state `git update-ref -d refs/remotes/origin/HEAD` actually produces. The
    default branch is then *named* but not present, so neither `ok` nor `parked`
    is honest: nothing was verified.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        clone = _workspace(root)
        _git(clone, "update-ref", "-d", "refs/remotes/origin/HEAD")
        rc, out, parsed = _run(root)
        assert rc != 0, f"a dangling origin/HEAD must not pass\n{out}"
        assert _status(parsed) == "unverified", (
            f"must be UNVERIFIED, not ok and not parked: {parsed!r}"
        )


def test_a_missing_clone_is_unverified_not_ok() -> None:
    """A clone that is not there has not been checked -- the audit-refusal rule.

    Same contract as `audit-agentic-os-board.py`, which refuses to run
    unauthenticated rather than reporting an empty board as a clean one.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        _workspace(root)
        rc, out, parsed = _run(root, clone="FFC-EX-not-cloned")
        assert rc != 0, f"a missing clone must not pass\n{out}"
        assert _status(parsed, "FFC-EX-not-cloned") == "unverified", parsed


def test_a_non_main_default_branch_is_honoured() -> None:
    """The discriminating test: the check reads origin/HEAD, it does not assume `main`.

    Every other false-green case in this module is also satisfied by a script
    that compares HEAD against the literal string "main". This one is not: the
    clone sits on `trunk`, which IS its origin default, so a "main"-comparing
    script fails it and the correct script passes it. Without this case the rest
    of the module is decoration.
    """
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = pathlib.Path(td)
        _workspace(root, branch="trunk")
        rc, out, parsed = _run(root)
        assert rc == 0, (
            "a clone on its own non-`main` default branch must pass -- failing it "
            f"means the script hardcodes `main`\n{out}"
        )
        assert _status(parsed) == "ok", parsed
        assert "trunk" in out, f"the verdict should name the real default, got {out!r}"


def test_the_workspace_must_be_stated() -> None:
    """#1237, applied to this script: the cwd is the thing under suspicion.

    `verify-conductor-hooks.py` shipped a cwd fallback and it produced a measured
    false green -- run from inside a clone it certified a session whose project
    root was elsewhere. A verifier whose subject is "which tree is this run
    reading" must not infer that tree from the cwd. Here the flag is `required`,
    so argparse refuses; the test exists so that a later convenience default
    cannot be added without turning this red.
    """
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert proc.returncode != 0, (
        "running with no --workspace must be refused, not defaulted to the cwd: "
        f"rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    )
    combined = proc.stdout + proc.stderr
    assert "workspace" in combined.lower(), (
        f"the refusal must name the missing argument, got {combined[:300]!r}"
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
