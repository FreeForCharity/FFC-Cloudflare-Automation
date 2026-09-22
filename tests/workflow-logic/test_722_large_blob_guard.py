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
    path_prefix: pathlib.Path | None = None,
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
    if path_prefix is not None:
        # `env.get`, not `env["PATH"]`: a KeyError here would fail the module
        # for a reason that has nothing to do with the guard, and this file's
        # own header records how expensive a harness failure wearing the system
        # under test's clothes is. An absent PATH is not worth a diagnosis --
        # the subprocess would fail to find `bash` a moment later and say so.
        existing = env.get("PATH", "")
        env["PATH"] = str(path_prefix) + (os.pathsep + existing if existing else "")
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


def _grow_tracked_file(tmp_path, name: str, before: bytes, after: bytes):
    """Commit `name` at `before` bytes on the base, then grow it to `after`."""
    repo = _init_repo(tmp_path)
    (repo / name).write_bytes(before)
    _commit(repo, "baseline: the file exists and is under the limit")
    _git(repo, "branch", "-f", "base", "HEAD")

    (repo / name).write_bytes(after)
    _commit(repo, "docs: append to a file the repo already tracks")
    return _run_guard(repo)


def test_a_grown_tracked_text_file_is_not_diagnosed_as_a_committed_binary(tmp_path):
    """AC3 of #1243: the two failures have different causes and different fixes.

    Before this, both produced `This PR introduces one or more blobs over N
    bytes` followed by instructions to delete a file and force-push. Every word
    of that describes a binary swept in by `git add -A`. A reader whose actual
    mistake was appending a table row to a 1 MB Markdown file was handed a
    diagnosis about somebody else's mistake -- measured on #1242, where the
    first reading of the failure was that something binary had been staged.
    """
    result = _grow_tracked_file(
        tmp_path, "ledger.md", b"x" * 900_000, b"x" * BIG
    )
    out = result.stdout + result.stderr
    assert result.returncode == 1, out

    # The headline must not claim something was introduced -- nothing was.
    assert "already tracks" in result.stdout, out
    assert "an existing file grew" in result.stdout, out

    # The per-file line must classify it and name both sizes, the limit and the
    # overage, so the reader can see how far over it is without measuring.
    assert "TRACKED text file that GREW" in result.stdout, out
    assert "900000 bytes" in result.stdout, out
    assert str(BIG) in result.stdout, out
    assert "1048576" in result.stdout, out
    assert f"over by {BIG - 1048576}" in result.stdout, out

    # And the remedy that actually applies must be present. In this fixture
    # nothing was introduced, so the branch rewrite must be disclaimed -- and
    # the mixed-case warning must NOT fire, or the disclaimer would be hedged
    # for a case that is not happening.
    assert "A TRACKED TEXT FILE GREW PAST THE LIMIT" in result.stderr, out
    assert "so it does not apply here" in result.stderr, out
    assert "ALSO INTRODUCES A NEW OVERSIZED BLOB" not in result.stderr, out

    # The two halves of the message must agree. A list header reading
    # "blobs INTRODUCED in this PR's commits" sits two lines under a headline
    # saying nothing new was committed, and a reader resolves that contradiction
    # by believing the word they already expected -- which is the whole failure
    # being fixed here, reintroduced by a heading.
    assert "introduced" not in result.stdout.lower(), out
    assert "large-blob-allowlist.txt" in result.stderr, out


def test_a_text_blob_far_larger_than_the_sniff_window_is_still_read_as_text(tmp_path):
    """The classification must not collapse on exactly the files it is for.

    Every blob this guard reports is, by definition, over 1 MiB. A sniff that
    only worked on small inputs would be green on every fixture a reviewer
    thinks to write by hand and useless in production, so the size is the point
    of this case rather than incidental to it.

    It is also what keeps the `head -c <window>` shape out: piping a multi-MB
    blob into `head` leaves `git` with SIGPIPE once the consumer closes, and
    under `set -o pipefail` that is a failure for exactly these files.
    Measured on ubuntu bash 5.2.21: `git cat-file blob $sha | head -c 8192 >
    /dev/null` exits 141, while the same producer feeding `head -c 8192 | wc -c`
    inside a command substitution exits 0 on five consecutive runs -- so the
    hazard is real in one spelling and does not reproduce in the other. The
    guard sidesteps the question by reading the whole stream through `tr`.
    """
    result = _grow_tracked_file(
        tmp_path, "ledger.md", b"x" * 900_000, b"x" * BIG
    )
    out = result.stdout + result.stderr
    assert "text file" in result.stdout, out
    assert "unreadable content" not in result.stdout, out


def test_a_new_binary_keeps_the_binary_diagnosis(tmp_path):
    """Opposite polarity: the original message must survive for its own case.

    A classification that always said "a tracked text file grew" would pass the
    test above and be useless. PR #910's actual shape must still read as what
    it was.
    """
    repo = _init_repo(tmp_path)
    (repo / "actionlint").write_bytes(b"\0" * BIG)
    _commit(repo, "commit a tool binary")

    result = _run_guard(repo)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "introduces one or more blobs" in result.stdout, out
    assert "NEW binary file" in result.stdout, out
    assert "not present on base" in result.stdout, out

    # The text-file remedy must NOT fire here: it tells the reader not to delete
    # anything, which is exactly the wrong advice for a swept-in binary.
    assert "A TRACKED TEXT FILE GREW" not in result.stderr, out
    assert "git push --force-with-lease" in result.stderr, out


def test_a_grown_tracked_binary_is_distinguished_from_both(tmp_path):
    """Third combination: tracked (so not "introduced") but binary (so not text).

    Pinned because the two flags are independent and a single boolean would
    conflate them -- the headline follows "was it already tracked", the remedy
    paragraph follows "is it text".
    """
    result = _grow_tracked_file(
        tmp_path, "vendor.bin", b"\0" * 900_000, b"\0" * BIG
    )
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "TRACKED binary file that GREW" in result.stdout, out
    assert "already tracks" in result.stdout, out
    assert "A TRACKED TEXT FILE GREW" not in result.stderr, out


def test_a_right_aligned_wc_count_does_not_turn_text_into_binary(tmp_path):
    """The size comparison must survive a `wc` that pads its count.

    The sniff compares `wc -c` output against the size `cat-file --batch-check`
    reported, and those two come from different tools. BSD-family `wc`
    right-aligns, so the count can arrive as `"  1200000"` -- a string compare
    would then read every text blob as binary and hand a Markdown file the
    binary remedy, which is the exact defect this PR exists to remove, restored
    by a detail of output formatting.

    GNU coreutils 9.4 reading stdin does not pad (measured on this host), so the
    only way to exercise it is to supply a `wc` that does. The shim calls the
    real binary by absolute path -- resolved before it goes on PATH, or it would
    recurse -- and right-aligns the result.
    """
    if sys.platform == "win32":
        # The shim is a shebang script made executable with chmod; neither
        # travels to a Windows filesystem. The behaviour under test is not
        # platform-specific, so covering it on POSIX covers it.
        return

    real_wc = shutil.which("wc")
    assert real_wc, "fixture needs a real `wc` to delegate to"

    repo = _init_repo(tmp_path)
    (repo / "ledger.md").write_bytes(b"x" * 900_000)
    _commit(repo, "baseline text file under the limit")
    _git(repo, "branch", "-f", "base", "HEAD")
    (repo / "ledger.md").write_bytes(b"x" * BIG)
    _commit(repo, "grow it")

    shim_dir = tmp_path / "bsd-wc"
    shim_dir.mkdir()
    shim = shim_dir / "wc"
    shim.write_text(
        "#!/bin/sh\n"
        f'out=$("{real_wc}" "$@") || exit $?\n'
        'printf "%10s\\n" "$out"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)

    # Precondition: the shim really does pad, or this test proves nothing.
    padded = subprocess.run(
        [str(shim), "-c"],
        input="abc",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    ).stdout
    assert padded.startswith(" "), f"shim did not pad: {padded!r}"

    result = _run_guard(repo, path_prefix=shim_dir)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "TRACKED text file that GREW" in result.stdout, out
    assert "binary" not in result.stdout, out


def test_a_dot_path_is_still_recognised_as_tracked(tmp_path):
    """A path under `.github/` must classify the same as any other.

    The obvious way to ask "how big was this on the base" is
    `git cat-file -s "<rev>:<path>"`, and on the Windows git-bash host that runs
    this suite MSYS rewrites that argument when the path starts with a dot --
    `origin/main:.github/x` reaches git as `origin\\main;.github\\x`. The lookup
    would fail, the file would come back absent, and every `.github/...` file
    would be reported as NEW: silently, and in the direction of the message this
    guard was changed to stop producing.

    Stated plainly: on ubuntu this case **cannot** fail, because MSYS is not
    there to mangle anything -- reverting the lookup to the `cat-file` form
    leaves it green in CI. It is here for the Windows run, which is the only
    place the difference is observable, and it is the whole reason the dot-path
    is in the fixture rather than a plain filename.
    """
    repo = _init_repo(tmp_path)
    workflows = repo / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "big.yml").write_bytes(b"x" * 900_000)
    _commit(repo, "baseline: a dot-path file under the limit")
    _git(repo, "branch", "-f", "base", "HEAD")

    (workflows / "big.yml").write_bytes(b"x" * BIG)
    _commit(repo, "grow it")

    result = _run_guard(repo)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "TRACKED text file that GREW" in result.stdout, out
    assert "NEW " not in result.stdout, out


def test_classification_never_swallows_an_offender(tmp_path):
    """Two offenders of different shapes must both be reported, and the
    remedies must be true of both.

    The classification only shapes the message; it must not be able to decide
    that a file is fine. A branch that reports one offender and drops the other
    is the false-clean this guard exists to prevent, one file at a time.

    The stderr half is the more dangerous one, and it shipped wrong: the
    grown-text paragraph said "there is no binary to delete" and "does not
    apply to a tracked file" unconditionally, so in this exact fixture the
    guard printed a `NEW binary file` entry and then told the reader, four
    lines later, that there was no binary and the rewrite did not apply. A
    reader who believed it would leave a 1.2 MB binary permanently reachable
    from `main`, which is the whole failure this guard exists to prevent --
    reached through its own remediation text.
    """
    repo = _init_repo(tmp_path)
    (repo / "ledger.md").write_bytes(b"x" * 900_000)
    _commit(repo, "baseline text file under the limit")
    _git(repo, "branch", "-f", "base", "HEAD")

    (repo / "ledger.md").write_bytes(b"x" * BIG)
    (repo / "actionlint").write_bytes(b"\0" * BIG)
    _commit(repo, "one of each")

    result = _run_guard(repo)
    out = result.stdout + result.stderr
    assert result.returncode == 1, out
    assert "ledger.md" in result.stdout, out
    assert "actionlint" in result.stdout, out
    # Mixed shapes: the generic headline is the honest one, because something
    # WAS introduced.
    assert "introduces one or more blobs" in result.stdout, out

    # Both remedies, and no sentence that contradicts the other offender.
    assert "A TRACKED TEXT FILE GREW PAST THE LIMIT" in result.stderr, out
    assert "ALSO INTRODUCES A NEW OVERSIZED BLOB" in result.stderr, out
    assert "The branch rewrite below DOES apply" in result.stderr, out
    assert "so it does not apply here" not in result.stderr, out
    # The two claims that were false here before this was pinned.
    assert "no binary to delete" not in result.stderr, out
    assert "nothing to delete: the limit" in result.stderr, out


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
    assert "BASE_SHA" in guard["env"] and "HEAD_SHA" in guard["env"]

    # The guard must cover merge_group as well as pull_request. This job is a
    # required check and the merge group is where required checks are evaluated
    # for a queued PR, so a pull_request-only guard can be satisfied without
    # ever running -- and 722 does trigger on merge_group.
    triggers = wf[True] if True in wf else wf["on"]
    assert "merge_group" in triggers, (
        "722 no longer triggers on merge_group; re-check whether the guard's "
        "event filter still needs to cover it"
    )
    condition = guard["if"]
    for event in ("pull_request", "merge_group"):
        assert f"github.event_name == '{event}'" in condition, (
            f"the guard skips {event} runs: {condition}"
        )
    for expr in ("github.event.merge_group.base_sha", "github.event.merge_group.head_sha"):
        assert expr in str(guard["env"]), (
            f"{expr} missing; a merge_group run has no github.event.pull_request "
            "to read the range from"
        )

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
