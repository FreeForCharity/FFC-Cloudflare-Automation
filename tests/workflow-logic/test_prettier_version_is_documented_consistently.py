"""Every documented `prettier@<version>` must be the one 722-ci.yml actually checks with.

CLAUDE.md already warns that formatting with a prettier other than the CI-pinned
one produces local-pass/CI-fail loops, because prettier's Markdown reflow changes
between minor releases. On 2026-08-05 (conductor run 101) the repo was handing out
exactly that failure in its own contributor documentation:

    .github/workflows/README.md, "Local usage"
      - Prettier (check): `npx --yes prettier@<3.3.3> --check . --ignore-unknown`
      - Prettier (write): `npx --yes prettier@<3.3.3> --write . --ignore-unknown`

(the angle brackets are this module's, not the README's — see the last paragraph)
while `722-ci.yml` checked with 3.8.1. Measured on that tree, the difference is
not latent: the CI-pinned version reported the tracked tree clean, and the version
the README prescribes wanted to reformat **10 tracked files** — including
`CLAUDE.md` and `docs/workflow-safety-and-approvals.md`, i.e. two of the documents
the Agentic OS routine names as key files. So a contributor who followed the
documented local procedure would rewrite ten files, see the `--write` succeed, and
have CI reject the result. The document that causes the failure and the document
that warns about it were both in the tree, which is why a reader could not have
caught this by being careful.

The guard is a set comparison, not a hand-list of files (ledger L16/L62: remaining
scope must be an exact test over the tree). It scans **every tracked file** for the
`prettier@<version>` spelling and requires each to equal the version parsed out of
722-ci.yml. That way a new mention in a new file is covered the day it lands, and
fixing one mention without fixing its neighbours still fails.

Two properties this module is careful to have, both of them lessons the repo has
already paid for:

  * **It fails closed if it can no longer find the pin.** A consistency test that
    silently stops locating one side of the comparison passes vacuously and reads
    as coverage (ledger L91 / #991 is the same shape: three artifacts agreeing
    perfectly while nothing asks the authority). If the CI step is renamed or the
    invocation is rewritten, `test_the_ci_pin_is_discoverable` fails rather than
    letting the scan compare against nothing.

  * **It names no version literal of its own.** The expected value is parsed from
    722-ci.yml at run time, so bumping the pin needs no edit here — and because
    this module carries no `prettier@x.y.z` token, it is itself inside the scan's
    denominator rather than exempted from it.

    That second property was learned the hard way, and it is why the quote at the
    top carries angle brackets. The first draft reproduced the README's two bad
    lines **verbatim**, prefix included. It passed locally and failed in CI,
    because `_tracked_files()` reads `git ls-files`: when it was tested the module
    was still untracked, so the scan could not see the file introducing the defect.
    Committing put it inside its own denominator and the guard caught its own
    docstring. The local green was not a weak signal, it was a **false** one, and
    the mutation runs inherited the same blind spot. Two consequences worth
    keeping: a guard whose denominator is the tracked tree must be re-run **after**
    `git add`, and a module that quotes the pattern it forbids has to break the
    spelling in order to say so.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT

CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "722-ci.yml"

# Matched against raw bytes, so a tracked binary cannot raise UnicodeDecodeError
# on a cp1252 host and take the module down at import (ledger L07).
_MENTION = re.compile(rb"prettier@(\d+\.\d+\.\d+)")


def _ci_pin() -> str | None:
    """The version 722-ci.yml checks the tree with, or None if it cannot be found."""
    found = {m.group(1).decode("ascii") for m in _MENTION.finditer(CI_WORKFLOW.read_bytes())}
    if len(found) != 1:
        return None
    return found.pop()


def _tracked_files() -> list[pathlib.Path]:
    """Tracked paths only — untracked caches (.pytest_cache) and node_modules are noise."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return [REPO_ROOT / name.decode("utf-8") for name in out.split(b"\0") if name]


def _mentions() -> list[tuple[str, str]]:
    """(repo-relative path, version) for every `prettier@<version>` in the tree."""
    hits = []
    for path in _tracked_files():
        if not path.is_file():
            continue
        for m in _MENTION.finditer(path.read_bytes()):
            rel = path.relative_to(REPO_ROOT).as_posix()
            hits.append((rel, m.group(1).decode("ascii")))
    return sorted(hits)


def test_the_ci_pin_is_discoverable():
    # Without this the comparison below has nothing to compare against and would
    # pass for the wrong reason — the failure mode the module's docstring names.
    assert CI_WORKFLOW.exists(), f"{CI_WORKFLOW} is missing; the CI pin cannot be read"
    pin = _ci_pin()
    assert pin is not None, (
        "could not parse a single `prettier@<version>` out of "
        f"{CI_WORKFLOW.relative_to(REPO_ROOT).as_posix()}. This guard compares every "
        "documented prettier version against the one CI actually runs, so losing the "
        "CI side makes it vacuous rather than lenient. Re-point _MENTION/_ci_pin at "
        "wherever the pin now lives."
    )


def test_the_scan_actually_reaches_the_documentation():
    # A denominator check. If `git ls-files` returns nothing, or the pattern stops
    # matching, the version comparison below is trivially satisfied.
    mentions = _mentions()
    assert len(mentions) >= 2, (
        "expected the CI workflow and at least one document to mention a pinned "
        f"prettier version; found {len(mentions)}: {mentions}. A scan that reaches "
        "nothing cannot detect drift."
    )


def test_every_documented_prettier_version_matches_the_ci_pin():
    pin = _ci_pin()
    assert pin is not None, "see test_the_ci_pin_is_discoverable"
    wrong = [(path, ver) for path, ver in _mentions() if ver != pin]
    assert not wrong, (
        f"722-ci.yml checks the tree with prettier {pin}, but these mention a "
        "different version. Formatting with a version other than the CI pin rewrites "
        "files CI then rejects — and when the mismatch is in contributor docs, the "
        "repo is prescribing that failure. Measured on 2026-08-05, the drift below "
        "wanted to reformat 10 tracked files that CI considered clean:\n  "
        + "\n  ".join(f"{path} says {ver}, CI pins {pin}" for path, ver in wrong)
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
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
