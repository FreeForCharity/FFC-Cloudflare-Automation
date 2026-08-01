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
from wf_extract import REPO_ROOT, WORKFLOWS, hashes_a_shell_value

LEDGER = REPO_ROOT / "docs" / "lessons-ledger.md"
HERE = pathlib.Path(__file__).resolve().parent

# A row of the ledger: | L07 | lesson | evidence | enforced by |
_ROW = re.compile(r"^\|\s*(L\d{2})\s*\|(.*)$")

# A backticked token is a claim about the tree only when it looks like a path —
# otherwise cells could not mention `api_get` or `npm ci` without the existence
# check turning into a false failure.
_PATHISH = re.compile(r"`([^`]+)`")
_PATH_SUFFIXES = (".md", ".py", ".yml", ".yaml", ".json", ".ps1", ".csv")


def split_table_cells(line: str) -> list[str]:
    r"""Split one Markdown table row on its real cell separators (#964).

    A `|` separates cells only when the run of backslashes immediately before it
    is EVEN, because GFM resolves the escape before inline code is parsed:

      ``\|``    an escaped pipe, inside a cell  (``\|\|`` renders as ``||``)
      ``\\|``   a literal backslash, then a SEPARATOR
      ``\\\|``  a literal backslash, then an escaped pipe — L43's row

    A `(?<!\\)\|` lookbehind gets the middle case backwards, and it fails in the
    direction that hides damage: two cells merge into one, so the row reads a
    column short while still rendering as a table.
    """
    cells: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            # Consume the escape PAIR, so the escaped character can never be read
            # as a separator and a doubled backslash cannot shield the pipe after
            # it. This is what makes the rule "even number of backslashes".
            buf.append(line[i : i + 2])
            i += 2
            continue
        if ch == "|":
            cells.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    cells.append("".join(buf))
    return cells


def _row_cells(line: str) -> list[str]:
    """The cells of a table row, without the empties the outer pipes produce.

    Exactly one leading and one trailing empty are dropped — they are the row's
    delimiting pipes. Dropping every trailing empty would silently swallow a
    genuinely blank final cell, which is one of the shapes #964 is about.
    """
    parts = split_table_cells(line.strip())
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.strip() for p in parts]


def _rows() -> list[tuple[str, list[str]]]:
    """(id, [lesson, evidence, enforced_by]) for every ledger row."""
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        m = _ROW.match(line.strip())
        if not m:
            continue
        # A lesson quoting shell (`\|\| echo`) has to escape its pipes for
        # Markdown, and splitting on those would shred the row into fragments —
        # which then fails the evidence/tier checks for a reason that has nothing
        # to do with the row's content.
        cells = _row_cells(line)[1:]
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
# Table structure — a stray pipe truncates a lesson, and so does "fixing" a
# correct escape (#964)
# ---------------------------------------------------------------------------
#
# The cells of this ledger routinely quote shell, so pipes inside them must be
# written `\|`. Nothing checked that, and it fails in both directions:
#
#   * a genuine unescaped `|` splits the row into extra columns and truncates the
#     lesson at the pipe — the table still renders, it is just wrong;
#   * a reviewer deleting a correct escape does the same damage. That is not
#     hypothetical: on #950 Copilot filed a finding that ``\|\|`` was
#     over-escaped, and applying it would have split the row. Settling it took a
#     manual GFM render (#950 comment 5146563050).
#
# Both directions are the same observable: the row's cell count stops matching
# the header's. So the check is a column count, not a pipe-hunt — and it is
# deliberately local, with no `gh api markdown` call: a CI test must not depend
# on the network or the shared API budget.

_DELIM_CELL = re.compile(r"^:?-{3,}:?$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _tables(text: str) -> list[dict]:
    """Every GFM table in `text`, as {header, delim, rows} of (lineno, cells).

    A table is a `|` line followed by a delimiter line — which is what separates
    a real table from the `\\|`-quoting prose above it, and from the bullet list
    in "Adding a lesson". Fenced code is skipped: a shell sample inside a fence
    is not a table row, whatever it starts with.
    """
    lines = text.splitlines()
    tables: list[dict] = []
    i, fenced = 0, False
    while i < len(lines):
        if _FENCE.match(lines[i]):
            fenced = not fenced
            i += 1
            continue
        if fenced or not lines[i].lstrip().startswith("|"):
            i += 1
            continue
        if i + 1 >= len(lines) or not lines[i + 1].lstrip().startswith("|"):
            i += 1
            continue
        delim = _row_cells(lines[i + 1])
        if not delim or not all(_DELIM_CELL.match(c) for c in delim):
            i += 1
            continue
        table = {
            "header": (i + 1, _row_cells(lines[i])),
            "delim": (i + 2, delim),
            "rows": [],
        }
        j = i + 2
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            table["rows"].append((j + 1, _row_cells(lines[j])))
            j += 1
        tables.append(table)
        i = j
    return tables


def _row_label(cells: list[str]) -> str:
    """How to name the offending row: by ID when it has one, else by content."""
    if cells and re.fullmatch(r"L\d+", cells[0].strip()):
        return cells[0].strip()
    first = (cells[0] if cells else "").strip()
    return f"`{first[:40]}…`" if first else "<empty first cell>"


def column_count_problems(
    text: str, label: str = "docs/lessons-ledger.md"
) -> list[str]:
    """Rows whose cell count disagrees with their table's header."""
    problems = []
    for table in _tables(text):
        hline, header = table["header"]
        want = len(header)
        dline, delim = table["delim"]
        if len(delim) != want:
            problems.append(
                f"{label}:{dline}: delimiter row has {len(delim)} cells, "
                f"header at line {hline} declares {want}"
            )
        for lineno, cells in table["rows"]:
            if len(cells) != want:
                problems.append(
                    f"{label}:{lineno}: row {_row_label(cells)} has {len(cells)} "
                    f"cells, header at line {hline} declares {want} — an unescaped "
                    "`|` inside a cell splits the row and truncates the text at "
                    "the pipe (write it `\\|`); deleting a correct `\\|` escape "
                    "does the same damage"
                )
    return problems


def id_problems(text: str, label: str = "docs/lessons-ledger.md") -> list[str]:
    """Lesson IDs that are missing, malformed, or reused.

    Gaps are legal and deliberately unchecked: L37–L41 are reserved by long-lived
    draft PRs, and an ID in an open PR is only a reservation until it merges.
    """
    problems: list[str] = []
    seen: dict[str, list[int]] = {}
    for table in _tables(text):
        _, header = table["header"]
        if not header or header[0].strip().lower() != "id":
            continue
        for lineno, cells in table["rows"]:
            got = cells[0].strip() if cells else ""
            if not re.fullmatch(r"L\d+", got):
                problems.append(
                    f"{label}:{lineno}: ID column reads {got!r}, expected `L<n>` — "
                    "rows are cited by ID from issues and PRs"
                )
                continue
            seen.setdefault(got, []).append(lineno)
    for lid, lines in sorted(seen.items()):
        if len(lines) > 1:
            problems.append(
                f"{label}: duplicate lesson id {lid} at lines "
                + ", ".join(str(n) for n in lines)
                + " — ids are cited and never reused (L43: a hand-resolved merge "
                "conflict shipped a duplicate L36 with nothing to catch it)"
            )
    return problems


def test_every_ledger_row_has_the_column_count_its_header_declares():
    problems = column_count_problems(LEDGER.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_every_lesson_id_is_present_well_formed_and_unique():
    problems = id_problems(LEDGER.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


# The three self-tests below are what make the two above worth having. Each is
# mutation-proof in permanent form (L47/L09): neuter `column_count_problems` or
# `id_problems` and these flip red, while the real-ledger tests above stay green
# vacuously — which is precisely the "green for the wrong reason" shape this
# repo keeps rediscovering.

_FIXTURE_HEADER = (
    "| ID  | Lesson | Evidence | Enforced by |\n| --- | --- | --- | --- |\n"
)


def test_the_column_count_guard_sees_a_stray_pipe():
    planted = _FIXTURE_HEADER + (
        "| L90 | run `a | b` and read the exit status | #1 | `doc — why` |\n"
    )
    problems = column_count_problems(planted, label="planted.md")
    assert problems, "an unescaped `|` inside a cell must fail the column count"
    first = problems[0]
    assert "L90" in first and "5 cells" in first and "declares 4" in first, (
        f"the failure must name the row and the observed vs expected counts: {problems}"
    )


def test_the_column_count_guard_sees_a_correct_escape_being_deleted():
    """The #950 direction: a reviewer 'fixing' `\\|\\|` back to `||`.

    The escaped form is L42's real shape, so this pins both readings of the same
    row against each other rather than inventing a fixture.
    """
    escaped = _FIXTURE_HEADER + (
        "| L91 | a `\\|\\|` fallback hides the crash | #947 | `doc — why prose` |\n"
    )
    assert not column_count_problems(escaped, label="planted.md"), (
        "`\\|` is an escaped pipe inside a cell and must NOT be read as a separator"
    )
    de_escaped = escaped.replace("\\|\\|", "||")
    problems = column_count_problems(de_escaped, label="planted.md")
    assert problems and "L91" in problems[0], (
        "removing a correct escape splits the row and must fail: " f"{problems}"
    )


def test_the_id_guard_sees_a_planted_duplicate_and_an_empty_cell():
    planted = _FIXTURE_HEADER + (
        "| L36 | first lesson | #1 | `doc — why` |\n"
        "| L36 | second lesson | #2 | `doc — why` |\n"
        "|     | third lesson | #3 | `doc — why` |\n"
    )
    problems = id_problems(planted, label="planted.md")
    dupe = [p for p in problems if "duplicate" in p]
    assert dupe and "3" in dupe[0] and "4" in dupe[0], (
        f"a duplicate id must be reported with BOTH line numbers: {problems}"
    )
    assert any("ID column reads ''" in p for p in problems), (
        f"an empty ID cell must be reported: {problems}"
    )


def test_the_cell_splitter_reads_backslashes_the_way_gfm_does():
    # `\\|` — a literal backslash followed by a SEPARATOR — is the case a
    # `(?<!\\)\|` lookbehind gets wrong, and it is wrong in the hiding direction:
    # it merges two cells, so a row reads one column short instead of failing.
    assert split_table_cells(r"a\\|b") == ["a\\\\", "b"]
    assert split_table_cells(r"a\|b") == [r"a\|b"]
    # L43's real shape: a literal backslash, then an escaped pipe, one cell.
    assert split_table_cells(r"grep -oE '^\\\| L[0-9]{2}'") == [
        r"grep -oE '^\\\| L[0-9]{2}'"
    ]
    # A blank final cell survives, so `| a | |` is two cells and not one.
    assert _row_cells("| a | |") == ["a", ""]


# ---------------------------------------------------------------------------
# L02 — `gh` error bodies land on stdout, so `|| <default>` never yields <default>
# ---------------------------------------------------------------------------

# Exact debt, not a ceiling — and now EMPTY. The six sites 726's fix (#854) never
# reached were converted in #889: 120's four (bind cert-state x2, smoke run list,
# smoke run view) onto a `gh_get` helper, 729's role read-back and 730's
# environment list onto `api_get`, each with an extraction test module
# (test_120_cutover_gh_errors.py, test_729_add_collaborator.py,
# test_730_environment_gate_audit.py).
# Fixing one REQUIRES editing this list, which is the point: the count is asserted
# both ways so the debt cannot silently grow or silently rot. An empty dict now
# means any NEW site fails CI on its first commit.
# Keyed by repo-relative path, not basename: every composite action is named
# `action.yml`, so basenames collide across directories and two files' counts
# would silently merge into one entry.
KNOWN_ERROR_SWALLOWING: dict[str, int] = {}

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
    # Vacuous while KNOWN_ERROR_SWALLOWING is empty (#889 closed the last six) —
    # deliberately kept, because the debt list is the thing that may grow again,
    # and the exact-count assertion above is what holds the empty case.
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
#
# Both patterns are anchored on the EMITTING command, which is what separates the
# defect from correct code that looks like it. `cat "$file" | sha256sum` and
# `(cd d && cat f) | sha256sum` stream the file's real bytes through the pipe and
# strip nothing; `printf '%s' "$out" | sha256sum` hashes a string that has already
# lost its trailing newlines. Only echo/printf turn a shell value into the bytes
# being hashed, so requiring one of them is the difference between a guard and a
# tripwire — an unanchored form flagged three safe shapes, including one in the
# variable pattern (measured on #895).
# The rule itself lives in `wf_extract.hashes_a_shell_value` — two modules check
# it (this tree-wide scan and 738's step-local guard), and when they each held
# their own regex the two drifted within a single review (#895 rounds 2 and 5).
# The samples below are its self-test: they must distinguish a correct
# implementation from the wrong ones, which is a stronger bar than passing.


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
            if hashes_a_shell_value(stripped):
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
    # thing. The first shape is the exact line 738 shipped before #893.
    for shape in (
        """printf '%s' "$out" | sha256sum | cut -d' ' -f1""",
        """echo "${body}" | sha256sum""",
        """echo "$(cat /tmp/body)" | md5sum""",
        # Single-quoted: a triple-quoted form needs `\"` to escape the trailing
        # quote, which reads like a stray backslash in the shell sample it is not.
        'sha256sum <<<"$out"',
        # Substitutions containing their OWN pipe. Every sample above happens to
        # be pipe-free inside `$( )`, so all of them passed against a pattern that
        # was blind to this entire spelling — the self-test proved nothing about
        # it (#895 round 4). A sample family that cannot distinguish two
        # implementations is not covering the difference between them.
        'echo "$(cat f | tr -d " ")" | sha256sum',
        'printf "%s" "$(gh api x | jq -r .body)" | sha256sum',
        'sha256sum <<<"$(cat f | tail -1)"',
    ):
        assert hashes_a_shell_value(shape), (
            f"the scan would not flag {shape!r} — it cannot hold L27"
        )


def test_the_variable_hashing_scan_leaves_correct_code_alone():
    """The other direction, and the one #895's review was right about.

    A tripwire that fires on safe shapes gets deleted or worked around, so each of
    these is a form that streams real file bytes (or hashes a path) and must pass.
    All three of the middle group were false positives before the emitting-command
    anchor; `cat "$file" | sha256sum` slipped through the *variable* pattern, which
    the review did not name.
    """
    for ok in (
        "sha256sum /tmp/smoke-body | cut -d' ' -f1",
        'sha256sum "$file"',
        'cat "$file" | sha256sum',
        'cat "$(which tool)" | sha256sum',
        # Masking must not turn a safe line into a hit: this one names a path via
        # a substitution that contains a pipe, and still streams real bytes.
        'cat "$(ls -1 d | head -1)" | sha256sum',
        # `$((…))` is arithmetic, not command substitution — a number with no
        # trailing newlines to strip, so it is not the L27 defect. The masking
        # regex swallowed it until `(?!\()` was added (#895 round 5).
        'echo "$((n + 1))" | sha256sum',
        'printf "%s" "$((count * 2))" | md5sum',
        # …and in the here-string form too. Carving arithmetic out of one form and
        # not the other is how a single rule ends up disagreeing with itself: this
        # sample was flagged while the pipe form above passed (#895 round 6).
        'sha256sum <<<"$((n + 1))"',
        "(cd d && cat f) | sha256sum",
        "{ cat f; } | sha256sum",
        'echo "$name: ok" | tee -a "$log"',
    ):
        hit = hashes_a_shell_value(ok)
        assert hit is None, (
            f"the scan false-positives on {ok!r} (pattern {hit.pattern if hit else ''}) "
            "— it streams the file's real bytes and strips nothing"
        )


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
