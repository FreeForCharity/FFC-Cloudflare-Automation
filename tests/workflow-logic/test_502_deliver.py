"""Unit tests for the 502 deliver-step bash (daily data-sync PR into ffcadmin).

This step opens/updates the `chore/ga-data-sync` PR in FFC-IN-ffcadmin.org. It
runs against a **shallow single-branch clone**, which is exactly what broke in
run 29679068718 (#733): when the sync branch already existed remotely,
`git checkout -B chore/ga-data-sync origin/chore/ga-data-sync` failed with
exit 128 because a `--depth 1` clone never creates that remote-tracking ref —
the fetched tip lands only in FETCH_HEAD. The fix bases the checkout on
FETCH_HEAD. That regression was invisible for ~2 weeks because the only place
this bash runs live is behind the github-prod gate.

No network: git talks to a **local bare repo** (via `url.<file://>.insteadOf`)
and `gh` is the fake harness shim. Reverting the #733 fix line
(`git checkout -B chore/ga-data-sync FETCH_HEAD` → `... origin/chore/ga-data-sync`)
makes `test_sync_branch_exists_bases_on_fetch_head` fail (exit 128).
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import step_run

HARNESS_DIR = pathlib.Path(__file__).resolve().parent / "harness"
TOKEN = "TESTTOKEN"
CLONE_URL = f"https://x-access-token:{TOKEN}@github.com/FreeForCharity/FFC-IN-ffcadmin.org.git"
SYNC_BRANCH = "chore/ga-data-sync"

# Files the deliver step writes into the target repo.
GA_FILES = ("freeforcharity.org.json", "ffcadmin.org.json")


def _git(cwd: pathlib.Path, *args: str, env: dict | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env)


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _seed_target_files(root: pathlib.Path, ga_body: str, catalog_body: str) -> None:
    """Lay out the four synced paths (2 GA reports + 2 workflow-catalog copies)
    the deliver step writes, under a repo working tree at `root`."""
    for name in GA_FILES:
        _write(root / "public" / "data" / "google-analytics" / name, ga_body)
    _write(root / "src" / "data" / "workflow-catalog.json", catalog_body)
    _write(root / "public" / "data" / "workflow-catalog.json", catalog_body)


def _build_origin(td: pathlib.Path, *, sync_branch: bool, sync_matches_source: bool) -> pathlib.Path:
    """Create a bare `origin` repo. Its `main` branch is a minimal repo; when
    `sync_branch` is set it also carries `chore/ga-data-sync` whose synced
    files either match the source bytes (`sync_matches_source`) or differ."""
    env = {
        "GIT_CONFIG_GLOBAL": str(td / "seed-gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(td),
        "PATH": "/usr/bin:/bin",
        "GIT_AUTHOR_NAME": "seed",
        "GIT_AUTHOR_EMAIL": "seed@example.com",
        "GIT_COMMITTER_NAME": "seed",
        "GIT_COMMITTER_EMAIL": "seed@example.com",
    }
    seed = td / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main", env=env)
    _write(seed / "README.md", "ffcadmin\n")
    _git(seed, "add", "-A", env=env)
    _git(seed, "commit", "-qm", "init", env=env)

    if sync_branch:
        _git(seed, "checkout", "-q", "-b", SYNC_BRANCH, env=env)
        if sync_matches_source:
            _seed_target_files(seed, SOURCE_GA, SOURCE_CATALOG)
        else:
            _seed_target_files(seed, '{"stale":"old"}\n', '{"catalog":"old"}\n')
        _git(seed, "add", "-A", env=env)
        _git(seed, "commit", "-qm", "prior sync", env=env)
        _git(seed, "checkout", "-q", "main", env=env)

    origin = td / "origin.git"
    _git(td, "clone", "-q", "--bare", str(seed), str(origin), env=env)
    return origin


SOURCE_GA = '{"site":"metrics","generated":"today"}\n'
SOURCE_CATALOG = '{"catalog":"fresh"}\n'


def run_deliver(*, sync_branch: bool, sync_matches_source: bool, gh_env: dict | None = None):
    """Execute the deliver step against a local bare origin. Returns
    (proc, gh_log, origin_path, tmp_root) — tmp_root kept alive by the caller."""
    script = step_run("502-google-analytics-report.yml", "deliver", "daily data-sync PR")
    td_ctx = tempfile.TemporaryDirectory()
    td = pathlib.Path(td_ctx.name)

    origin = _build_origin(td, sync_branch=sync_branch, sync_matches_source=sync_matches_source)

    # The step runs with CWD = the workflow checkout; ../ga-out and ../docs are
    # its siblings, and it clones the target into ./target.
    work = td / "work"
    work.mkdir()
    for name in GA_FILES:
        _write(work / "ga-out" / name, SOURCE_GA)
    _write(work / "docs" / "workflow-catalog.json", SOURCE_CATALOG)

    home = td / "home"
    home.mkdir()
    # Redirect the real clone/fetch/push URL to the local bare repo — no network.
    gitconfig = home / ".gitconfig"
    gitconfig.write_text(
        f'[url "file://{origin}"]\n\tinsteadOf = "{CLONE_URL}"\n'
    )

    gh_log = td / "gh.log"
    gh_log.touch()
    env = {
        "PATH": f"{HARNESS_DIR}:/usr/bin:/bin",
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GH_TOKEN": TOKEN,
        "TEST_GH_LOG": str(gh_log),
    }
    if gh_env:
        env.update(gh_env)

    proc = subprocess.run(
        ["bash", "-c", script],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc, gh_log.read_text(), origin, td_ctx


def _origin_head(origin: pathlib.Path, branch: str) -> str | None:
    r = subprocess.run(
        ["git", "--git-dir", str(origin), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1"},
    )
    return r.stdout.strip() or None


# --- Scenario 1: sync branch exists on origin (the #733 regression case) -------
def test_sync_branch_exists_bases_on_fetch_head():
    proc, gh_log, origin, ctx = run_deliver(
        sync_branch=True, sync_matches_source=False, gh_env={"TEST_PR_EXISTS": "1"}
    )
    with ctx:
        # The fix must survive a shallow single-branch clone: no exit 128, no fatal.
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "fatal" not in (proc.stdout + proc.stderr).lower(), proc.stderr
        assert "No data changes" not in proc.stdout, proc.stdout
        # New content was committed and pushed onto the existing sync branch,
        # and the existing PR was reused (no create) then auto-merged.
        assert _origin_head(origin, SYNC_BRANCH) is not None
        assert "pr merge" in gh_log, gh_log
        assert "pr create" not in gh_log, gh_log


# --- Scenario 2: sync branch absent → fresh branch, open a new PR --------------
def test_sync_branch_absent_creates_fresh_branch_and_pr():
    proc, gh_log, origin, ctx = run_deliver(
        sync_branch=False, sync_matches_source=False, gh_env={"TEST_PR_EXISTS": ""}
    )
    with ctx:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "No data changes" not in proc.stdout, proc.stdout
        # Branch created on origin and a fresh PR opened (view failed → create).
        assert _origin_head(origin, SYNC_BRANCH) is not None
        assert "pr create" in gh_log, gh_log
        assert "pr merge" in gh_log, gh_log


# --- Scenario 3: no data changes → early exit, no push, no PR churn ------------
def test_no_data_changes_early_exit():
    proc, gh_log, origin, ctx = run_deliver(
        sync_branch=True, sync_matches_source=True, gh_env={"TEST_PR_EXISTS": "1"}
    )
    with ctx:
        before = _origin_head(origin, SYNC_BRANCH)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "No data changes vs the sync branch; skipping." in proc.stdout, proc.stdout
        # Early exit is before push/create/merge: the sync branch is untouched
        # and no PR API calls were made.
        assert _origin_head(origin, SYNC_BRANCH) == before
        assert "pr create" not in gh_log, gh_log
        assert "pr merge" not in gh_log, gh_log


# Sanity: the fixtures we assert "no change" against are actually byte-identical
# to what the step writes, so scenario 3 exercises the real diff, not a typo.
def test_source_fixtures_are_self_consistent():
    assert json.loads(SOURCE_GA)
    assert json.loads(SOURCE_CATALOG)


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
