"""Unit tests for the 722 large-blob guard (scripts/check-large-blobs.sh).

The guard exists because `main`'s merge queue is configured
`merge_method: MERGE` (ruleset 16768928), which keeps every commit on a PR
branch permanently reachable from `main`. A blob added in one commit and
deleted in a later one is therefore still in history forever, while every
tip-tree view -- `git diff base..head`, the "Files changed" tab, a reviewer's
eye -- reports the PR as clean. PR #910 was the live case: a 6.07 MB
`actionlint` binary and a 3.8 MB shellcheck wheel, added by `git add -A` and
removed in a follow-up commit.

The central test is therefore `test_add_then_delete_is_still_caught`: the exact
shape a files-changed check cannot see. Fixtures are built as real throwaway git
repositories rather than mocks, because the property under test is a property of
git's reachability rules, and a mock would just re-assert our own reading of
them. A shape test guards the workflow wiring, including the `fetch-depth: 0`
the scan depends on -- a shallow checkout makes the guard exit 2 rather than
silently pass, but the wiring is what keeps it from ever getting there.
"""

from __future__ import annotations

import inspect
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import load_workflow

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GUARD = REPO_ROOT / "scripts" / "check-large-blobs.sh"
ALLOWLIST = REPO_ROOT / ".github" / "large-blob-allowlist.txt"
WF_FILE = "722-ci.yml"

# Comfortably over the 1 MiB default, small enough to stay fast.
BIG = 1_200_000


def _bash() -> str:
    """Resolve a bash that understands the Windows-style paths we pass it.

    On the Conductor's Windows host a bare `bash` resolves to the WSL shim,
    which strips drive letters out of `C:\\...` arguments and dies with
    `No such file or directory`. Git for Windows ships a bash that handles them,
    so prefer it locally. On CI (`ubuntu-latest`) `bash` is already correct.
    """
    if sys.platform == "win32":
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if pathlib.Path(candidate).exists():
                return candidate
    return "bash"


def _git(repo: pathlib.Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
    return proc.stdout


def _init_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "fixture"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "conductor@freeforcharity.org")
    _git(repo, "config", "user.name", "Conductor Test")
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "baseline")
    _git(repo, "branch", "base")
    return repo


def _commit(repo: pathlib.Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)


def _run_guard(
    repo: pathlib.Path,
    base: str = "base",
    head: str = "HEAD",
    allowlist: pathlib.Path | None = None,
) -> subprocess.CompletedProcess:
    # Inherit the real environment. A scrubbed env breaks `bash` on Windows
    # hosts (it resolves to the WSL shim, which needs SYSTEMROOT et al.) and
    # the failure surfaces as exit 1 -- indistinguishable from a genuine
    # "oversized blob found", which silently turns the negative tests green.
    env = dict(os.environ)
    env.pop("BLOB_ALLOWLIST", None)
    if allowlist is not None:
        env["BLOB_ALLOWLIST"] = str(allowlist)
    else:
        # Default to a path that cannot exist so the fixture repos are scanned
        # with no exemptions regardless of the caller's working directory.
        env["BLOB_ALLOWLIST"] = str(repo / "no-allowlist-here.txt")
    return subprocess.run(
        [_bash(), str(GUARD), base, head],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=env,
    )


def test_clean_branch_passes(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "small.txt").write_text("x" * 1000, encoding="utf-8")
    _commit(repo, "add a small file")

    result = _run_guard(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK:" in result.stdout


def test_large_blob_in_tip_is_caught(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "actionlint").write_bytes(b"\0" * BIG)
    _commit(repo, "commit a tool binary")

    result = _run_guard(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "actionlint" in result.stdout


def test_add_then_delete_is_still_caught(tmp_path):
    """The regression that motivated the guard (PR #910).

    The tip tree is clean and `git diff base..HEAD` is empty of the blob, so a
    files-changed check passes. Under merge_method=MERGE the blob still lands
    in main's history, so the guard must fail.
    """
    repo = _init_repo(tmp_path)
    (repo / "actionlint").write_bytes(b"\0" * BIG)
    (repo / "feature.txt").write_text("real work\n", encoding="utf-8")
    _commit(repo, "feat: real work, plus a binary swept in by git add -A")

    (repo / "actionlint").unlink()
    _commit(repo, "chore: drop the binary")

    # Precondition: the tip tree really is clean, which is why this shape slips
    # past every diff-based review.
    tip_files = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").split()
    assert "actionlint" not in tip_files

    result = _run_guard(repo)
    assert result.returncode == 1, (
        "add-then-delete must fail: merge_method=MERGE keeps the blob reachable\n"
        + result.stdout
        + result.stderr
    )
    assert "actionlint" in result.stdout


def test_blob_already_on_base_is_not_attributed_to_the_pr(tmp_path):
    """Pre-existing large assets must not fail every unrelated PR."""
    repo = _init_repo(tmp_path)
    (repo / "vendor.bin").write_bytes(b"\0" * BIG)
    _commit(repo, "baseline large asset")
    _git(repo, "branch", "-f", "base", "HEAD")

    (repo / "note.txt").write_text("unrelated change\n", encoding="utf-8")
    _commit(repo, "docs: unrelated")

    result = _run_guard(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_allowlisted_path_passes_and_is_reported(tmp_path):
    repo = _init_repo(tmp_path)
    theme = repo / "whmcs" / "theme" / "six_ffc" / "js"
    theme.mkdir(parents=True)
    (theme / "scripts.js").write_bytes(b"\0" * BIG)
    _commit(repo, "chore: refresh the theme bundle")

    result = _run_guard(repo, allowlist=ALLOWLIST)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Allowlisted" in result.stdout
    assert "scripts.js" in result.stdout


def test_allowlist_does_not_exempt_other_paths(tmp_path):
    """The shipped allowlist must not be broad enough to wave through a binary."""
    repo = _init_repo(tmp_path)
    (repo / "actionlint").write_bytes(b"\0" * BIG)
    _commit(repo, "commit a tool binary")

    result = _run_guard(repo, allowlist=ALLOWLIST)
    assert result.returncode == 1, result.stdout + result.stderr


def test_unresolvable_ref_fails_loudly(tmp_path):
    """A shallow checkout must error, never report a false clean."""
    repo = _init_repo(tmp_path)

    result = _run_guard(repo, base="no-such-ref")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "cannot resolve ref" in result.stderr


def test_unscannable_range_fails_loudly(tmp_path):
    """An enumeration failure must exit 2, not read as an empty (clean) range.

    Both refs resolve, so the ref check upstream passes -- the failure happens
    inside `git rev-list --objects`. Swallowing it would report OK on a range
    the guard never actually scanned, which is the precise false negative this
    guard exists to prevent.
    """
    repo = _init_repo(tmp_path)
    (repo / "actionlint").write_bytes(b"\0" * BIG)
    _commit(repo, "commit a tool binary")

    # Remove the tip commit's tree object. The commit still resolves, so the
    # range looks scannable, but walking it cannot complete.
    tree = _git(repo, "rev-parse", "HEAD^{tree}").strip()
    tree_object = repo / ".git" / "objects" / tree[:2] / tree[2:]
    assert tree_object.exists(), "fixture expects loose objects in a fresh repo"
    tree_object.unlink()

    result = _run_guard(repo)
    assert result.returncode == 2, (
        "an unscannable range must exit 2, never a false OK\n"
        + result.stdout
        + result.stderr
    )
    assert "OK:" not in result.stdout
    assert "could not enumerate objects" in result.stderr


def test_workflow_wiring():
    """722 must call the guard on pull_request with full history."""
    wf = load_workflow(WF_FILE)
    steps = wf["jobs"]["validate"]["steps"]

    checkout = next(s for s in steps if "actions/checkout" in str(s.get("uses", "")))
    assert checkout.get("with", {}).get("fetch-depth") == 0, (
        "the guard scans base..head; a shallow checkout cannot resolve the range"
    )

    guard = next(
        s for s in steps if "check-large-blobs.sh" in str(s.get("run", ""))
    )
    assert guard["if"] == "github.event_name == 'pull_request'"
    assert "BASE_SHA" in guard["env"] and "HEAD_SHA" in guard["env"]

    # The guard must run before the slower validators, so an oversized branch
    # fails fast rather than after a full lint pass.
    assert steps.index(guard) < steps.index(
        next(s for s in steps if "workflow name prefixes" in str(s.get("name", "")))
    )


def test_guard_script_is_executable():
    mode = subprocess.run(
        ["git", "ls-files", "-s", "scripts/check-large-blobs.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    ).stdout.split()
    assert mode and mode[0] == "100755", f"expected mode 100755, got {mode[:1]}"


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


def _call(test) -> None:
    """Invoke a test, supplying a private temp dir if it asks for one.

    `run_all.py` executes each module as a plain script -- there is no pytest in
    this suite, so `tmp_path` has to be provided here. Without this runner the
    module defines its tests and exits 0 having executed none of them, which the
    harness reports as a passing module.
    """
    if "tmp_path" in inspect.signature(test).parameters:
        tmp = tempfile.mkdtemp(prefix="wf722-")
        try:
            test(pathlib.Path(tmp))
        finally:
            # git leaves read-only object files; they resist rmtree on Windows.
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        test()


if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            _call(t)
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {e}")
    sys.exit(1 if failures else 0)
