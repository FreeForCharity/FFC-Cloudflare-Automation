"""Guards for the lessons ledger and the two lessons in it that a test can hold (#866).

`docs/lessons-ledger.md` records findings whose rediscovery costs hours. A ledger
is a check on the repo's memory, and this repo's most repeated lesson is that a
check which is merely *configured* proves nothing — so the ledger gets the same
treatment it prescribes:

  * Every row carries evidence and names the tier holding it, and any repo path a
    row names must EXIST. A ledger claiming "enforced by test_x.py" after that
    file is renamed away would be the exact failure mode the document is about,
    one level up — prose that reads as coverage while covering nothing.

  * Two of its rows are mechanically detectable, so they are enforced here rather
    than merely written down:

      L02  `gh` writes its error body to STDOUT, so `... 2>/dev/null || echo <d>`
           captures the error WITH `<d>` appended instead of `<d>`. That is how
           726 printed "✓ Org-level branch ruleset present ({…403…}0)" for months
           (#854). The remaining sites are held as an EXACT debt list: a new one
           fails, and fixing one without deleting its entry fails too.

      L07  A module that reads a repo file without `encoding="utf-8"` raises
           UnicodeDecodeError on a cp1252 host — at import, before any test runs.
           The traceback prints no FAIL lines, so a crashed suite looks exactly
           like a passing one (#866). Every text read/write in these modules must
           therefore name its encoding.

Both guards match the *thing* rather than one spelling of it, which is ledger L17
applied to itself: the shell scan covers `.yml` AND `.yaml` plus composite actions
(Copilot caught the `.yaml` hole on #890 — a valid Actions extension the first
draft skipped) and skips comment lines, since 726 documents the anti-pattern in
prose and must not self-trip; the encoding scan walks parentheses instead of
lines, so a call split across lines cannot slip through.
"""

from __future__ import annotations

import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT, WORKFLOWS

LEDGER = REPO_ROOT / "docs" / "lessons-ledger.md"
HERE = pathlib.Path(__file__).resolve().parent

# A row of the ledger: | L07 | lesson | evidence | enforced by |
_ROW = re.compile(r"^\|\s*(L\d{2})\s*\|(.*)$")

# A backticked token is a claim about the tree only when it looks like a path —
# otherwise cells could not mention `api_get` or `npm ci` without the existence
# check turning into a false failure.
_PATHISH = re.compile(r"`([^`]+)`")
_PATH_SUFFIXES = (".md", ".py", ".yml", ".yaml", ".json", ".ps1", ".csv")


def _rows() -> list[tuple[str, list[str]]]:
    """(id, [lesson, evidence, enforced_by]) for every ledger row."""
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        # Split on unescaped pipes only: a lesson quoting shell (`\|\| echo`) has
        # to escape its pipes for Markdown, and splitting on those would shred the
        # row into fragments — which then fails the evidence/tier checks for a
        # reason that has nothing to do with the row's content.
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", m.group(2))]
        while cells and cells[-1] == "":
            cells.pop()
        rows.append((m.group(1), cells))
    return rows


def _claimed_paths(cell: str) -> list[str]:
    out = []
    for token in _PATHISH.findall(cell):
        token = token.strip()
        if token.startswith("doc —"):
            continue
        if "/" in token or token.endswith(_PATH_SUFFIXES):
            out.append(token)
    return out


def test_the_ledger_exists_and_agents_md_points_at_it():
    assert LEDGER.is_file(), f"{LEDGER} is missing"
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "docs/lessons-ledger.md" in agents, (
        "AGENTS.md must link docs/lessons-ledger.md — an unlinked ledger is one "
        "nobody reads before starting the work it is about (#866 AC1)"
    )


def test_the_ledger_has_rows():
    # Without this, every other guard here passes vacuously on an empty table.
    assert len(_rows()) >= 10, f"expected a populated ledger, parsed {len(_rows())} rows"


def test_every_row_has_a_lesson_evidence_and_a_tier():
    problems = []
    for lid, cells in _rows():
        if len(cells) < 3:
            problems.append(f"{lid}: expected 3 cells (lesson, evidence, enforced by)")
            continue
        lesson, evidence, enforced = cells[0], cells[1], cells[2]
        if len(lesson) < 40:
            problems.append(f"{lid}: lesson too short to be transferable")
        if not ("#" in evidence or "http" in evidence or "`" in evidence):
            problems.append(
                f"{lid}: no evidence reference — a lesson without a link is a rumour"
            )
        if not enforced:
            problems.append(f"{lid}: no tier named")
    assert not problems, "\n".join(problems)


def test_ids_are_unique():
    ids = [lid for lid, _ in _rows()]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"duplicate lesson ids: {dupes} — ids are cited and never reused"


def test_a_guard_the_ledger_names_actually_exists():
    missing = []
    for lid, cells in _rows():
        if len(cells) < 3:
            continue
        for claimed in _claimed_paths(cells[2]):
            if not (REPO_ROOT / claimed).exists():
                missing.append(
                    f"{lid}: names `{claimed}` as its enforcement, and that path "
                    "does not exist — the ledger is claiming coverage it does not have"
                )
    assert not missing, "\n".join(missing)


def test_prose_only_rows_say_why_prose_is_the_ceiling():
    problems = []
    for lid, cells in _rows():
        if len(cells) < 3:
            continue
        enforced = cells[2]
        if not _claimed_paths(enforced):
            reason = enforced.strip("`").strip()
            if not reason.startswith("doc —"):
                problems.append(
                    f"{lid}: names no guard path, so its tier must read "
                    "`doc — <why prose is the ceiling>`"
                )
            elif len(reason) < len("doc — ") + 20:
                problems.append(f"{lid}: `doc —` with no real reason given")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# L02 — `gh` error bodies land on stdout, so `|| <default>` never yields <default>
# ---------------------------------------------------------------------------

# Exact debt, not a ceiling. These are the sites 726's fix (#854) never reached;
# all six are tracked on #889 with the reason they were not fixed in the same PR
# (none of those workflows has extraction tests, and 120 is a gated bulk DNS
# cutover).
# Fixing one REQUIRES editing this list, which is the point: the count is asserted
# both ways so the debt cannot silently grow or silently rot.
# Keyed by repo-relative path, not basename: every composite action is named
# `action.yml`, so basenames collide across directories and two files' counts
# would silently merge into one entry.
KNOWN_ERROR_SWALLOWING = {
    ".github/workflows/120-bulk-cutover-to-github-pages.yml": 4,
    ".github/workflows/729-repo-add-collaborator.yml": 1,
    ".github/workflows/730-repo-audit-environment-gates.yml": 1,
}

_GH_CALL = re.compile(r"\bgh\s+[a-z]")
_FALLBACK = re.compile(r"\|\|\s*(echo|true)\b")


def _shell_carrying_files() -> list[pathlib.Path]:
    """Every file in the tree that can carry an embedded `gh` invocation.

    Both YAML extensions, because Actions accepts `.yaml` and nothing in this repo
    forbids it — `check-workflow-references.py` already globs both, so a `.yml`-only
    scan here would be the outlier. Composite actions are included for the same
    reason: none holds a `gh` call today, and a scan that only looks where the
    problem currently lives goes quiet exactly when it moves (ledger L17).
    """
    files: list[pathlib.Path] = []
    for ext in ("yml", "yaml"):
        files.extend(WORKFLOWS.glob(f"*.{ext}"))
        files.extend((REPO_ROOT / ".github" / "actions").glob(f"*/action.{ext}"))
    return sorted(files)


def _error_swallowing_sites() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in _shell_carrying_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            # 726 documents this very anti-pattern in a comment; a scan that did
            # not skip comments would fail on the explanation of the fix.
            if stripped.startswith("#"):
                continue
            if (
                _GH_CALL.search(stripped)
                and "2>/dev/null" in stripped
                and _FALLBACK.search(stripped)
            ):
                key = path.relative_to(REPO_ROOT).as_posix()
                found.setdefault(key, []).append(f"{lineno}: {stripped}")
    return found


def test_no_new_gh_error_swallowing_sites():
    found = _error_swallowing_sites()
    counts = {name: len(sites) for name, sites in found.items()}
    new = []
    for name, sites in found.items():
        allowed = KNOWN_ERROR_SWALLOWING.get(name, 0)
        if len(sites) > allowed:
            new.append(
                f"{name}: {len(sites)} `gh … 2>/dev/null || <default>` sites, "
                f"{allowed} known:\n    " + "\n    ".join(sites)
            )
    assert not new, (
        "`gh` writes its error body to STDOUT, so this shape captures the error "
        "with the default appended and every downstream comparison takes the wrong "
        "branch — see ledger L02 / #854. Use 726's `api_get` form: capture, check "
        "the exit code, and send the error to stderr.\n" + "\n".join(new)
    )
    assert counts == KNOWN_ERROR_SWALLOWING, (
        "the known-sites list must be exact — a fixed site has to be deleted from "
        f"KNOWN_ERROR_SWALLOWING or it rots into a false claim of remaining debt.\n"
        f"  expected {KNOWN_ERROR_SWALLOWING}\n  found    {counts}"
    )


def test_the_known_sites_are_the_ones_the_ledger_and_889_describe():
    # Pins the shape rather than a count: if a "known" site is edited into some
    # other form, this stops asserting something that is no longer there.
    found = _error_swallowing_sites()
    for name in KNOWN_ERROR_SWALLOWING:
        assert name in found, (
            f"{name} is listed as known debt but no longer matches — if it was "
            "fixed, delete its entry (see #889)"
        )


# ---------------------------------------------------------------------------
# L27 — hashing a shell variable is not hashing the file
# ---------------------------------------------------------------------------

# `out=$(cmd)` strips every trailing newline, so a digest taken from the variable
# is the content minus its final byte(s). 738 published a canonical hash nobody
# could reproduce with `sha256sum`, and its byte-identity audit reported a
# trailing-newline-only difference as MATCHING (#893).
#
# Scanned tree-wide rather than in 738 alone: the anti-pattern is a shell habit,
# not a property of that workflow, and a guard that only looks where the defect
# currently lives goes quiet the moment it moves (ledger L17). Debt is zero and
# the assertion says so — a second site fails on its first commit.
_HASH_CMDS = r"(?:sha256sum|sha1sum|md5sum|shasum|cksum)"
_HASHED_VARIABLE = re.compile(r"\$\{?\w+\}?\"?\s*\|\s*" + _HASH_CMDS)
# `$(cmd) | sha256sum` has the same defect for the same reason — the substitution
# strips the newlines before the hash ever sees the bytes.
_HASHED_SUBSTITUTION = re.compile(r"\)\"?\s*\|\s*" + _HASH_CMDS)


def _variable_hashing_sites() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in _shell_carrying_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            # 738 explains the defect in a comment, exactly as 726 does for L02.
            if stripped.startswith("#"):
                continue
            if _HASHED_VARIABLE.search(stripped) or _HASHED_SUBSTITUTION.search(
                stripped
            ):
                key = path.relative_to(REPO_ROOT).as_posix()
                found.setdefault(key, []).append(f"{lineno}: {stripped}")
    return found


def test_no_workflow_hashes_a_shell_variable_instead_of_a_file():
    found = _variable_hashing_sites()
    assert not found, (
        "a digest taken from a shell variable is the content MINUS its trailing "
        "newlines, because command substitution strips them (ledger L27 / #893). "
        "The published digest then matches nothing `sha256sum` produces, and two "
        "files differing only in trailing newlines hash the same — which silently "
        "defeats a byte-identity comparison. Redirect the bytes to a file and hash "
        "the file:\n  "
        + "\n  ".join(f"{name}\n    " + "\n    ".join(s) for name, s in found.items())
    )


def test_the_variable_hashing_scan_can_actually_see_the_defect():
    # A zero-debt assertion is only meaningful if the pattern matches the real
    # thing. This is the exact line 738 shipped before #893, plus the substitution
    # variant, checked against the same regexes the scan uses.
    for shape in (
        """printf '%s' "$out" | sha256sum | cut -d' ' -f1""",
        """echo "${body}" | sha256sum""",
        """$(cat /tmp/body) | md5sum""",
    ):
        assert _HASHED_VARIABLE.search(shape) or _HASHED_SUBSTITUTION.search(shape), (
            f"the scan would not flag {shape!r} — it cannot hold L27"
        )
    # And it must not flag hashing a file, or every fixed site fails instead.
    for ok in ("sha256sum /tmp/smoke-body | cut -d' ' -f1", 'sha256sum "$file"'):
        assert not (
            _HASHED_VARIABLE.search(ok) or _HASHED_SUBSTITUTION.search(ok)
        ), f"the scan false-positives on {ok!r}"


# ---------------------------------------------------------------------------
# L07 — a module that crashes at import looks exactly like a passing one
# ---------------------------------------------------------------------------

_TEXT_IO = re.compile(r"\.(read_text|write_text)\(|(?<![\w.])open\(")
# Binary mode takes no encoding, so it is not a violation — it is the correct
# way to avoid the question (check-workflow-doc-consistency.py hashes bytes).
_BINARY_MODE = re.compile(r"""["'][rwax]b\+?["']""")


def _text_io_calls_missing_encoding(path: pathlib.Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    out = []
    for m in _TEXT_IO.finditer(src):
        depth, j = 1, m.end()
        while j < len(src) and depth:
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        args = src[m.end() : j]
        if "encoding=" in args or _BINARY_MODE.search(args):
            continue
        line = src.count("\n", 0, m.start()) + 1
        out.append(f"{path.name}:{line}: {src[m.start():j + 1][:90]}")
    return out


def test_text_io_in_the_harness_and_checks_declares_an_encoding():
    targets = sorted(HERE.glob("*.py")) + sorted((REPO_ROOT / "scripts").glob("*.py"))
    violations = []
    for path in targets:
        if path.resolve() == pathlib.Path(__file__).resolve():
            continue
        violations.extend(_text_io_calls_missing_encoding(path))
    assert not violations, (
        "text I/O without an explicit encoding — on a cp1252 host the ✓/❌/em-dashes "
        "in workflow files and step summaries raise UnicodeDecodeError at IMPORT, "
        "which prints a traceback and no FAIL lines, so a crashed module reads as a "
        "passing one (ledger L07). Pass encoding=\"utf-8\" (or open in binary):\n  "
        + "\n  ".join(violations)
    )


def test_this_guard_covers_the_module_that_reads_every_workflow():
    # wf_extract is the import-time reader every audit module funnels through, so
    # it is the one file where a missing encoding takes the whole suite down.
    assert 'encoding="utf-8"' in (HERE / "wf_extract.py").read_text(encoding="utf-8")


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
