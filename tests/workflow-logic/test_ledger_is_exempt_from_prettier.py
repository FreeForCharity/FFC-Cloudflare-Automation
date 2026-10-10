"""The lessons ledger must stay exempt from Prettier, and the exemption must stay narrow.

Prettier aligns every cell of a markdown table to the widest cell in its column.
`docs/lessons-ledger.md` carries a ~145-row table whose columns are already
2131/856/945 characters wide, so one new row with a wider `evidence` or
`enforced by` cell re-pads EVERY OTHER ROW.

Measured with the CI-pinned prettier@3.8.1 and this repo's `.prettierrc.json`,
on `main`'s ledger:

  * insert one row with a 1200-char final cell -> **145 rows rewritten**, final
    column widens 945 -> 1205;
  * insert one row whose cells are all NARROWER than the column maxima -> 1 row
    rewritten (its own). The re-pad is triggered by exceeding the max, not by
    adding a row, which is why some ledger PRs are `+1/-0` (#1586) and others
    are `149/145` (#1584: columns 856 -> 964 and 945 -> 1082).

Two costs, both paid repeatedly before the exemption:

  * every ledger PR conflicted with every other ledger PR for a purely cosmetic
    reason, and the conflict hunk spanned the whole table -- so a union
    resolution duplicates all 145 rows rather than the one that collided. The
    conflict signal carried no information about real compatibility.
  * the single new row was buried in a ~150-line diff.

**CI could not see any of it.** Each padding width is canonical for its own
content, so `prettier --check` exits 0 on the narrow table and on the re-padded
one alike -- measured both ways against `origin/main` and `#1584`'s head. That
is why this is enforced here rather than left to the formatter.

The exemption also removes the destructive half of #1055: prettier hard-wrapping
a row that sits one blank line adrift from its table, which destroyed the lesson
while every guard stayed green. `orphaned_row_problems` in
`test_lessons_ledger.py` still reports the adrift row -- prettier simply no
longer eats it first.

What could rot, and is therefore pinned below:

  1. someone removes the entry, and the re-pad silently returns;
  2. someone "simplifies" it to `docs/`, which stops Prettier checking EVERY
     doc in the repo -- a far larger hole than the one being closed;
  3. the ledger is renamed and the entry is left behind, so the exemption
     points at nothing and the new path re-pads;
  4. the matcher below drifts and makes test 1 vacuously true.

Run: python3 tests/workflow-logic/test_ledger_is_exempt_from_prettier.py
"""

from __future__ import annotations

import fnmatch
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
IGNORE_FILE = REPO / ".prettierignore"
LEDGER_PATH = "docs/lessons-ledger.md"


def ignore_patterns(text: str) -> list[str]:
    """The pattern lines of a `.prettierignore`, comments and blanks dropped."""
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def _matches(pattern: str, path: str) -> bool:
    """One gitignore-style pattern against one repo-relative POSIX path.

    Deliberately a SUBSET of gitignore: this file only has to be right about the
    spellings `.prettierignore` actually uses (plain paths, `dir/` prefixes,
    `*.ext` globs, and a leading `/` for root-anchoring). It is validated against
    a known population in
    `test_the_matcher_agrees_with_entries_whose_answer_is_already_known`, because
    a matcher that cannot return the right answer on cases whose answer is known
    is measuring its own bugs rather than the ignore file.
    """
    anchored = pattern.startswith("/")
    pat = pattern.lstrip("/")

    if pat.endswith("/"):
        # A directory prefix: it ignores everything beneath it.
        prefix = pat
        if path.startswith(prefix):
            return True
        # Unanchored directory names match at any depth.
        return not anchored and f"/{prefix}" in f"/{path}"

    if "/" in pat or anchored:
        # Path-ish pattern: match the whole relative path, or any prefix
        # directory of it (`a/b` also ignores `a/b/c`).
        return path == pat or path.startswith(pat + "/") or fnmatch.fnmatch(path, pat)

    # No slash: gitignore matches such a pattern against the BASENAME at any
    # depth (this is what makes `*.csv` cover `data/zones/x.csv`).
    return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pat)


def prettier_ignores(path: str, text: str) -> bool:
    return any(_matches(p, path) for p in ignore_patterns(text))


# Paths whose answer is already known from reading `.prettierignore` itself, used
# to prove the matcher works before any conclusion is drawn from it. The negative
# side is the load-bearing half: a matcher that returned True for everything
# would satisfy every other test in this file.
KNOWN_IGNORED = [
    "docs/workflow-catalog.json",
    ".github/workflows/README.md",
    "data/dependabot-affected-repos.json",
    "docs/template-previews/anything.md",
    "assets/ffc-footer.tsx",
    "whmcs/theme/style.css",
    "some/nested/export.csv",
]

KNOWN_NOT_IGNORED = [
    "AGENTS.md",
    "CLAUDE.md",
    "README.md",
    "docs/ffc-repo-map.md",
    "docs/workflow-safety-and-approvals.md",
    "docs/charity-onboarding-lifecycle.md",
    "tests/workflow-logic/run_all.py",
    ".github/workflows/722-ci.yml",
]


def test_the_matcher_agrees_with_entries_whose_answer_is_already_known():
    text = IGNORE_FILE.read_text(encoding="utf-8")
    assert KNOWN_IGNORED and KNOWN_NOT_IGNORED, (
        "both known populations must be non-empty, or this check passes without "
        "exercising the matcher at all"
    )
    wrong = [p for p in KNOWN_IGNORED if not prettier_ignores(p, text)]
    assert not wrong, (
        "the matcher failed to ignore paths that `.prettierignore` plainly lists, so "
        "no negative result from it can be trusted: " + ", ".join(wrong)
    )
    wrong = [p for p in KNOWN_NOT_IGNORED if prettier_ignores(p, text)]
    assert not wrong, (
        "the matcher ignored paths that `.prettierignore` does NOT list, which would "
        "make the exemption test below vacuously true: " + ", ".join(wrong)
    )


def test_the_lessons_ledger_is_exempt_from_prettier():
    text = IGNORE_FILE.read_text(encoding="utf-8")
    assert prettier_ignores(LEDGER_PATH, text), (
        f"`{LEDGER_PATH}` is not ignored by .prettierignore. Prettier aligns markdown "
        "table columns to the widest cell, so the next lesson row with a wide "
        "`evidence` or `enforced by` cell will re-pad all ~145 rows (measured: 145 "
        "rows rewritten, final column 945 -> 1205). Every ledger PR then conflicts "
        "with every other ledger PR over padding, and `prettier --check` cannot see "
        "it because each width is canonical for its own content. Restore the "
        f"`{LEDGER_PATH}` entry."
    )


def test_the_exemption_does_not_swallow_the_rest_of_docs():
    text = IGNORE_FILE.read_text(encoding="utf-8")
    siblings = [
        "docs/ffc-repo-map.md",
        "docs/workflow-safety-and-approvals.md",
        "docs/charity-onboarding-lifecycle.md",
        "docs/google-api.md",
    ]
    present = [s for s in siblings if (REPO / s).exists()]
    assert present, (
        "none of the sibling docs used to prove the exemption is narrow exist any "
        "more -- update this list, or the check below proves nothing"
    )
    swallowed = [s for s in present if prettier_ignores(s, text)]
    assert not swallowed, (
        "the ledger exemption has been widened and is now ignoring other docs: "
        + ", ".join(swallowed)
        + ". An entry like `docs/` or `docs/*.md` stops Prettier checking every doc "
        "in the repo -- a much larger hole than the table re-pad it was meant to "
        f"close. Ignore exactly `{LEDGER_PATH}` and nothing else under docs/."
    )


def test_the_exempted_path_still_exists():
    assert (REPO / LEDGER_PATH).is_file(), (
        f"`{LEDGER_PATH}` is listed in .prettierignore but no such file exists. A "
        "rename that leaves the entry behind is silent: the stale entry keeps "
        "passing the exemption check above while the ledger's new path is back "
        "under Prettier and re-pads on the next wide row. Point the entry at the "
        "new path."
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
            print(f"  FAIL {t.__name__}: {str(e)[:500]}")
    sys.exit(1 if failures else 0)
