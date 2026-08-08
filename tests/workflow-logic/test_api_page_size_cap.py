"""Guard: no `per_page` literal may exceed GitHub's 100 cap.

GitHub's REST API caps `per_page` at 100 and **does not say so**. A request for
double that returns 200 OK with the first 100 items and a `Link` header, and
every client that reads the body without following it gets a silently truncated
collection. There is no error, no warning, and no field in the response that
distinguishes "this is everything" from "this is page one".

(Note the spelling throughout this file: the over-cap value is never written out
as a literal, in the docstring or in the fixtures. This module scans the whole
tree, so a verbatim example would make it fail on itself — L190's shape, where a
guard's own prose is the likeliest place to write the forbidden thing.)

WHY THAT DIRECTION IS THE DANGEROUS ONE
    A truncated sweep under-reports. Run 124's scheduled-workflow health check
    asked `actions/workflows` for twice the cap, got 100 of the repo's 114
    workflows, and reported **2 red scheduled workflows against a truth of 3** —
    228 fell off the end. Both numbers it printed were plausible: this repo has
    ~103 workflow *files*, so "100 active workflows" reads as a complete answer,
    and a health check saying "2 failures, all tracked" is exactly what a clean
    run looks like. The only thing that exposed it was disagreeing with the
    previous run's count.

    That is the shape worth guarding: the failure produces a smaller number, and
    a smaller number is what everyone wants to see.

THE TELL, AND WHY IT IS NOT ENOUGH
    A result count that equals the page size exactly is the signature — but the
    cap is 100 and 100 is also a perfectly ordinary true answer, so the tell is
    unreliable in precisely the case that matters. The remedy is not vigilance;
    it is never asking for more than the API will give, and paginating.

SCOPE
    `per_page` only. GraphQL's `first:` is also capped at 100, but the API
    **rejects** an over-cap value with an explicit error rather than clamping it,
    so it cannot fail silently. `gh --limit` is not a page size — the CLI
    paginates internally to satisfy it.

    Every `per_page` in the tree is 100 or below today (39 at 100, the rest
    smaller), so this module currently freezes a property that is already true.
    That is the cheapest moment to freeze it: nothing has to be burned down, and
    the next hand-written sweep cannot reintroduce the defect.

WHY THE SECOND TEST IS THE LOAD-BEARING ONE
    The tree is clean by construction, so a scanner that returned `[]`
    unconditionally would satisfy the first assertion forever — the same trap
    `test_subprocess_encoding.py` records. `test_the_scan_can_actually_see_an_
    over_cap_value` feeds the extractor the exact shapes that caused the
    under-report and requires findings, and `test_the_scan_sees_the_real_tree`
    pins a non-trivial live count so a broken walk cannot pass by scanning
    nothing (L143).
"""

from __future__ import annotations

import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# `per_page` written any of the ways this repo writes it: a query string
# (`?per_page=100`), a YAML/JS mapping value (`per_page: 100`), or a quoted key
# in a JS object literal. The value is what matters; the separator is noise.
PER_PAGE = re.compile(r"""["']?per_page["']?\s*[:=]\s*["']?(\d+)""")

API_CAP = 100

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".next", "dist", "build", ".venv"}
SCAN_SUFFIXES = {".py", ".mjs", ".js", ".ts", ".yml", ".yaml", ".ps1", ".sh", ".md"}


def find_over_cap(text: str, label: str = "<text>") -> list[str]:
    """Every `per_page` literal in `text` above the API cap, as readable lines.

    Takes text rather than a path so the detector can be exercised against a
    synthetic sample — a guard that can only be pointed at a clean tree cannot
    be shown to fail.
    """
    findings = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in PER_PAGE.finditer(line):
            value = int(match.group(1))
            if value > API_CAP:
                findings.append(f"{label}:{number}: per_page={value} exceeds the {API_CAP} cap")
    return findings


def _scan_tree() -> tuple[list[str], int]:
    """(findings, files scanned) across the repository."""
    findings: list[str] = []
    scanned = 0
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        findings.extend(find_over_cap(text, path.relative_to(REPO_ROOT).as_posix()))
    return findings, scanned


def test_no_page_size_in_the_tree_exceeds_the_api_cap():
    findings, _ = _scan_tree()
    assert not findings, (
        "GitHub silently clamps per_page to 100 and returns page one with no error, "
        "so each of these truncates its collection and under-reports:\n  "
        + "\n  ".join(findings)
    )


def test_the_scan_can_actually_see_an_over_cap_value():
    """The load-bearing case: the real defect, in each spelling the repo uses.

    The values are interpolated rather than typed out, so this fixture is not
    itself a finding when the tree walk reaches this file.
    """
    query, mapping, literal = API_CAP * 2, API_CAP * 2 + 50, API_CAP * 5
    sample = "\n".join(
        [
            f'd = gh("repos/o/r/actions/workflows?per_page={query}")',  # the run-124 shape
            f"          per_page: {mapping}",
            f"  {{ owner, repo, 'per_page': {literal} }}",
        ]
    )
    findings = find_over_cap(sample, "sample")
    assert len(findings) == 3, f"expected all three spellings flagged, got: {findings}"
    assert "sample:1" in findings[0] and str(query) in findings[0]


def test_a_value_at_or_below_the_cap_is_not_a_finding():
    """100 is the correct answer, not a near-miss — flagging it would train the
    next author to silence this guard."""
    sample = "per_page=100\nper_page: 100\nper_page=50\nper_page: 1"
    assert find_over_cap(sample, "sample") == []


def test_the_scan_sees_the_real_tree():
    """A walk that matched nothing would satisfy every assertion above (L143)."""
    _, scanned = _scan_tree()
    assert scanned > 200, f"only {scanned} files scanned — the walk is not reaching the tree"
    live = 0
    for path in (REPO_ROOT / ".github" / "workflows").glob("*.yml"):
        live += len(PER_PAGE.findall(path.read_text(encoding="utf-8-sig")))
    assert live > 10, f"only {live} per_page literals found in workflows — the pattern is not matching"


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
