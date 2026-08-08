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
#
# `\d{2,}`, not `\d{2}`: the ledger passed L99 in #1054 and a two-digit pattern
# matches `L10` in `| L109 |` and then demands a `|` where the `9` is, so it does
# not match AT ALL. Eleven rows (L100–L110) were invisible to every `_rows()`-based
# check the moment they landed — including the guard-path existence check, which is
# this module's whole reason for existing. It failed silently and in the reassuring
# direction: fewer rows parsed, nothing to complain about (#1055).
_ROW = re.compile(r"^\|\s*(L\d{2,})\s*\|(.*)$")

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


def _rows_from_text(text: str) -> list[tuple[str, list[str]]]:
    """(id, [lesson, evidence, enforced_by]) for every row in `text`."""
    rows = []
    for line in text.splitlines():
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


def _rows() -> list[tuple[str, list[str]]]:
    return _rows_from_text(LEDGER.read_text(encoding="utf-8"))


def _claimed_paths(cell: str) -> list[str]:
    out = []
    for token in _PATHISH.findall(cell):
        token = token.strip()
        if token.startswith("doc —"):
            continue
        if "/" in token or token.endswith(_PATH_SUFFIXES):
            out.append(token)
    return out


# A skill is a repo artifact at `.claude/skills/<name>/SKILL.md`, so naming one
# in the tier column is as much a claim about the tree as naming a file. But a
# skill name carries no `/` and no suffix, so `_claimed_paths` cannot see it and
# the existence check above walked straight past it. L175 shipped naming an
# `ffc-environment-quirks` skill that has never existed, and every test in this
# module stayed green (#1108) — the blind spot is the same "prose that reads as
# coverage while covering nothing" this file was written for, one token shape
# over. It was caught by a reviewer reading the row, which is the tier this
# check exists to replace.
#
# The trailing word `skill` is what makes the token a claim: `run-checks` on its
# own is a phrase, `` `run-checks` skill `` is an assertion that the directory is
# there. Tokens containing `/` are left to `_claimed_paths`, so a row spelling
# the pointer out in full is checked once, as a path, rather than twice and
# wrongly.
_SKILL_CLAIM = re.compile(r"`([^`]+)`\s+skill\b", re.IGNORECASE)


def skill_claim_problems(cell: str, lid: str = "row") -> list[str]:
    """Skills a tier cell names that are not in the tree."""
    problems = []
    for name in _SKILL_CLAIM.findall(cell):
        name = name.strip()
        if "/" in name:
            continue
        try:
            present = (REPO_ROOT / ".claude" / "skills" / name / "SKILL.md").is_file()
        except OSError:
            # A token too long (or otherwise unrepresentable) to be a filename is
            # not a skill either, so it is a broken claim and must be REPORTED.
            # Raising here would be worse than useless: a check that dies mid-walk
            # takes the rest of the module with it, and a harness reading PASS/FAIL
            # lines scores the crash as "no test noticed" — found while mutating
            # this very rule, where widening it to every backticked token turned a
            # prose cell into a 300-character path and the run into an OSError that
            # read as a SURVIVED mutation.
            present = False
        if not present:
            problems.append(
                f"{lid}: names the `{name}` skill as its enforcement, and "
                f".claude/skills/{name}/SKILL.md does not exist — the ledger is "
                "claiming coverage it does not have"
            )
    return problems


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


def enforcement_problems(text: str) -> list[str]:
    """Every enforcement a row claims that the tree does not back.

    Pure over `text` so the WIRING is testable, not just the two rules it calls.
    While mutating this guard, deleting the `skill_claim_problems` call from a
    file-reading version left the whole module green — the real ledger had just
    been corrected, so the walk had nothing to find and the deletion was
    invisible. A rule that is only ever run against a clean ledger is enforced
    by the ledger's current contents, which is not enforcement.
    """
    missing = []
    for lid, cells in _rows_from_text(text):
        if len(cells) < 3:
            continue
        for claimed in _claimed_paths(cells[2]):
            if not (REPO_ROOT / claimed).exists():
                missing.append(
                    f"{lid}: names `{claimed}` as its enforcement, and that path "
                    "does not exist — the ledger is claiming coverage it does not have"
                )
        missing.extend(skill_claim_problems(cells[2], lid))
    return missing


def test_a_guard_the_ledger_names_actually_exists():
    missing = enforcement_problems(LEDGER.read_text(encoding="utf-8"))
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
    """Lesson IDs that are malformed or reused.

    Gaps are `gap_problems`' business, not this function's. This docstring used to
    read "Gaps are legal and deliberately unchecked: L37–L41 are reserved by
    long-lived draft PRs" — a statement that was true when written and expired
    silently when those PRs merged, leaving a blanket exemption over L38, which by
    then was not a reservation but a row lost in a merge (#1113).
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


# A row start, matched ANYWHERE in the line rather than anchored to its start:
# once prettier has reflowed an orphan, the row can end up appended to the
# paragraph above it, and an anchored pattern would report the lesson as simply
# absent — which is the same silence the orphan already produces.
_ROW_ANYWHERE = re.compile(r"\|\s*(L\d{2,})\s*\|")


def orphaned_row_problems(
    text: str, label: str = "docs/lessons-ledger.md"
) -> list[str]:
    """Rows that are not inside a table (#1055).

    Every check above operates on lines that LOOK like rows, and is sound about
    the rows it sees. None of them asks whether a row is in a table, so a row
    separated from its table by a single blank line is not a malformed row — it
    is a row in a table of its own, and the tree is green.

    That gap is not theoretical, because the formatter closes it destructively:

      1. author inserts a row one blank line off — still well-formed
      2. the guard passes, legitimately
      3. `prettier --write` (pre-commit, lint-staged, or by hand) sees a `|` line
         with no delimiter under it, so it is a PARAGRAPH, and hard-wraps it
      4. four cells become one, and nobody re-runs the guard, because step 2

    Measured on run 90 by orphaning two real rows and running the CI-pinned
    prettier: `L48` reddened only incidentally, via the cell count, with a message
    about unescaped pipes that points at the wrong cause; `L109` — three digits,
    so invisible to `_ROW` before the fix above — was 19 PASS / 0 FAIL.
    """
    lines = text.splitlines()
    in_table = {lineno for t in _tables(text) for lineno, _ in t["rows"]}
    problems: list[str] = []
    fenced = False
    for lineno, line in enumerate(lines, start=1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        # A sample row inside a fence is documentation, not a table (the same
        # carve-out `_tables` makes), and `in_table` lines are the good case.
        if fenced or lineno in in_table:
            continue
        m = _ROW_ANYWHERE.search(line)
        if not m:
            continue
        # State the OBSERVATION, then the likely cause — not the cause as though
        # it were measured. What is checked here is table membership: this line
        # carries a row start and no header/delimiter pair put it in a table. The
        # preceding line is never inspected, and a row start absorbed into a
        # paragraph can sit directly under a perfectly good table row, so a
        # message asserting "the line above is not a row" is sometimes just false
        # (Copilot, #1056). A guard that names a cause it did not measure sends
        # the next reader to the wrong place — the 726-preflight failure mode.
        problems.append(
            f"{label}:{lineno}: row {m.group(1)} is on a line that no table "
            "contains — a ledger row start appears here, but no header/delimiter "
            "pair puts it in a table. Usually the row is one blank line adrift "
            "from its table; after `prettier --write` it can instead be a row "
            "already reflowed into a paragraph, in which case its other cells are "
            "on the following lines and are no longer cells. A row one blank line "
            "adrift is still well-formed, so every other guard here passes; "
            "prettier then hard-wraps it and the lesson is destroyed while the "
            "tree stays green (#1055). Move the row back against its table."
        )
    return problems


def test_every_ledger_row_has_the_column_count_its_header_declares():
    problems = column_count_problems(LEDGER.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_every_ledger_row_is_contiguous_with_its_table():
    problems = orphaned_row_problems(LEDGER.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_both_row_parsers_agree_on_which_lessons_exist():
    """The cross-check that would have named the `L\\d{2}` blindness outright.

    Two parsers read this file — `_ROW` line by line, and `_tables` by table
    structure — and five checks hang off the first while three hang off the
    second. When they disagree, the rows in the gap are held by whichever set of
    checks did not see them, and nothing says so: `_rows()` returned 98 of 109 for
    as long as three-digit ids existed, and every count it fed still looked
    plausible (#1055).

    Deliberately a set comparison and not a count against a constant (AC5): a
    literal would need editing by the next author to add a lesson, and would go
    green again the moment they did.
    """
    text = LEDGER.read_text(encoding="utf-8")
    by_line = {lid for lid, _ in _rows()}
    by_table = {
        cells[0].strip()
        for t in _tables(text)
        for _, cells in t["rows"]
        if cells and re.fullmatch(r"L\d+", cells[0].strip())
    }
    assert by_line == by_table, (
        "the line parser and the table parser disagree about which lessons exist "
        f"— only `_ROW` saw {sorted(by_line - by_table)}, only `_tables` saw "
        f"{sorted(by_table - by_line)}. Rows in the gap are unchecked by half this "
        "module and nothing else reports it."
    )


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


def test_the_enforcement_walk_reports_a_bad_path_and_a_bad_skill_together():
    """Both rules reached from the walk, on one planted row.

    A real skill and a real path in the same cell are the discriminator: without
    them a walk that reported everything would pass the first half of this.
    """
    planted = _FIXTURE_HEADER + (
        "| L95 | a lesson | #1 | `scripts/no-such-guard.py`; "
        "`ffc-environment-quirks` skill |\n"
        "| L96 | a lesson | #2 | `tests/workflow-logic/test_lessons_ledger.py`; "
        "`run-checks` skill |\n"
    )
    problems = enforcement_problems(planted)
    assert any("L95" in p and "no-such-guard.py" in p for p in problems), (
        f"the walk must reach the path rule: {problems}"
    )
    assert any("L95" in p and "ffc-environment-quirks" in p for p in problems), (
        f"the walk must reach the skill rule: {problems}"
    )
    assert not [p for p in problems if "L96" in p], (
        f"a row whose path and skill both exist must be silent: {problems}"
    )


def test_the_skill_guard_sees_a_named_skill_that_is_not_in_the_tree():
    """The #1108 shape: a tier cell naming a skill nobody ever wrote.

    Pinned against a REAL skill in the same assertion, because a check that
    reports every skill missing would pass the first half on its own.
    """
    planted = "`ffc-environment-quirks` skill, CRLF section"
    problems = skill_claim_problems(planted, "L90")
    assert problems and "L90" in problems[0], (
        f"a skill that is not in the tree must be reported: {problems}"
    )
    assert ".claude/skills/ffc-environment-quirks/SKILL.md" in problems[0], (
        f"the failure must name the path it looked for: {problems}"
    )
    assert not skill_claim_problems("`run-checks` skill", "L91"), (
        "a skill that IS in the tree must not be reported"
    )


def test_the_skill_guard_reports_rather_than_raises_on_an_impossible_name():
    """A claim the filesystem cannot even be asked about is still a broken claim."""
    absurd = "x" * 300
    problems = skill_claim_problems(f"`{absurd}` skill", "L94")
    assert problems and "L94" in problems[0], (
        "a name too long to be a filename must be reported, not raised"
    )


def test_the_skill_guard_reads_only_tokens_claimed_as_skills():
    """Its discriminators: without these the rule is `every backticked token`."""
    assert not skill_claim_problems("`npm ci` and a skilled reviewer", "L92"), (
        "a backticked token not followed by the word `skill` is not a claim"
    )
    assert not skill_claim_problems(
        "`.claude/skills/run-checks/SKILL.md` skill", "L93"
    ), "a path-shaped token is `_claimed_paths`' job, and must not be re-resolved"


def test_the_orphan_guard_sees_a_row_one_blank_line_adrift():
    """Step 1–2 of the sequence: the state prettier has not reached yet.

    The row is perfectly well-formed here — four cells, a real id — which is why
    every other check in this module passes on it and why catching it at THIS
    point is the whole value. Once prettier runs, the lesson text is gone.
    """
    planted = _FIXTURE_HEADER + (
        "| L90 | a lesson long enough to be transferable to a reader | #1 | `doc — why` |\n"
        "\n"
        "| L91 | an orphan, well-formed, one blank line from its table | #2 | `doc — why` |\n"
    )
    assert not column_count_problems(planted, label="planted.md"), (
        "the orphan is a well-formed 4-cell row, so the column count is silent — "
        "that silence is what this guard is for"
    )
    problems = orphaned_row_problems(planted, label="planted.md")
    assert len(problems) == 1, f"exactly the orphan must fail, not its table: {problems}"
    assert "L91" in problems[0] and "L90" not in problems[0], (
        f"the failure must name the orphaned row: {problems}"
    )


def test_the_orphan_guard_sees_a_row_prettier_has_already_reflowed():
    """Step 3: the shape on disk after `npx prettier@3.8.1 --write`.

    Reproduced on run 90 against the real ledger. The first line still starts
    `| L109 |`, so anything anchored on the id keeps matching; the trailing pipe
    and the other three cells are on continuation lines that are prose. The cell
    count cannot see it at all — the lines are in no table — so this case needs
    its own check rather than riding on AC1's contiguity rule.
    """
    reflowed = _FIXTURE_HEADER + (
        "| L100 | a lesson long enough to be transferable to a reader | #1 | `doc — why` |\n"
        "\n"
        "| L109 | **A filter the API silently ignores returns an empty list, and an\n"
        "empty list reads as 'nothing has happened yet'.** Polling `actions/runs` for\n"
        "CI returned an empty array eight times. | #1049 | `doc — why prose is it` |\n"
    )
    assert not column_count_problems(reflowed, label="planted.md"), (
        "the reflowed row is in no table, so the column-count guard is structurally "
        "unable to see it — asserted so this test fails if that stops being true"
    )
    problems = orphaned_row_problems(reflowed, label="planted.md")
    assert problems and "L109" in problems[0], (
        f"a row prettier has reflowed into prose must fail, naming it: {problems}"
    )


def test_the_orphan_message_claims_only_what_the_check_measured():
    """A row start absorbed into a paragraph directly under a good table row.

    The first draft of this message said "the line above it is neither a row, a
    delimiter, nor a header" — a cause `orphaned_row_problems` never inspects. It
    checks table MEMBERSHIP, and the two come apart exactly here: line 4 below is
    flagged while the line above it is a perfectly good table row, so the claim
    was false and pointed the reader at the wrong line (Copilot, #1056).

    Pinned as a case rather than fixed in prose, because the wording is what a
    future author edits without re-deriving what the function actually looks at.
    """
    planted = _FIXTURE_HEADER + (
        "| L90 | a lesson long enough to be transferable to a reader | #1 | `doc — why` |\n"
        "prose that absorbed a row start | L91 | a | b | c |\n"
    )
    problems = orphaned_row_problems(planted, label="planted.md")
    assert problems and "L91" in problems[0], (
        f"a row start outside any table must be reported wherever it sits: {problems}"
    )
    assert "line above" not in problems[0], (
        "the message must not assert anything about the preceding line — this "
        f"fixture's preceding line IS a table row: {problems[0]}"
    )


def test_the_orphan_guard_leaves_a_correct_table_and_a_fenced_sample_alone():
    """The other direction: a tripwire that fires on safe shapes gets deleted.

    The fenced sample matters specifically — this module's own docstrings and the
    ledger's "Adding a lesson" section teach the row format by showing one, and a
    guard that reddened on the documentation of its own rule would be removed
    within a run.
    """
    clean = _FIXTURE_HEADER + (
        "| L90 | a lesson long enough to be transferable to a reader | #1 | `doc — why` |\n"
        "| L91 | a second row, contiguous with the first, as rows should be | #2 | `x.py` |\n"
        "\n"
        "Prose about the table, then an example of the row format:\n"
        "\n"
        "```\n"
        "| L92 | a sample row inside a fence is documentation, not a table | #3 | `x.py` |\n"
        "```\n"
    )
    assert not orphaned_row_problems(clean, label="planted.md"), (
        "contiguous rows and a fenced sample row are both correct and must pass"
    )


def test_the_row_pattern_matches_a_three_digit_lesson_id():
    """The blindness itself, pinned (#1055).

    `L\\d{2}` matches `L10` inside `L109` and then requires a `|` where the `9`
    is, so the row does not match at all — and an unmatched row is skipped, not
    reported. The ledger crossed L99 in #1054, so this is the difference between
    the guard-path existence check covering 109 rows and covering 98.
    """
    row = "| L109 | a lesson long enough to be transferable to a reader | #1 | `x.py` |"
    m = _ROW.match(row)
    assert m and m.group(1) == "L109", (
        "a three-digit lesson id must parse as a row — with `L\\d{2}` this returns "
        "None and eleven real rows go unchecked in silence"
    )
    assert re.compile(r"^\|\s*(L\d{2})\s*\|(.*)$").match(row) is None, (
        "the pre-fix pattern must be shown NOT to match this row, or this test is "
        "asserting nothing about the bug it exists for"
    )
    assert _ROW.match("| L07 | still a two-digit id | #1 | `x.py` |"), (
        "widening the pattern must not drop the two-digit ids it already held"
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


# --------------------------------------------------------------------------
# Undeclared gaps (#1113)
#
# `id_problems` above guards the DUPLICATE outcome of a hand-resolved table
# conflict, and its docstring used to exempt gaps outright: "Gaps are legal and
# deliberately unchecked: L37-L41 are reserved by long-lived draft PRs." That
# sentence was true when it was written and stopped being true without anyone
# touching it — L37, L39, L40 and L41 all merged, and the blanket exemption then
# covered the one id in that range that had NOT merged.
#
# L38 was on the branch at `7b3733d`, and the merge `28a4b8b` ("Merge main into
# conductor/lessons-r54 (ledger table conflict)") re-emitted all 27 rows of the
# table and brought 26 of them back. The diff is a wall of near-identical +/-
# lines with one row removed in the middle of it, which is why review did not
# see it and why nothing else did either: deletion is the mirror image of the
# duplicate L43 already guards, and only one of the two directions was held.
#
# A gap cannot be judged offline — an id reserved by an open PR is a legitimate
# hole in `main` until that PR merges, and CI has no way to enumerate open PRs.
# So the invariant is declarative: a skipped id must be DECLARED, with the PR
# holding it. An undeclared gap fails, and so does a declaration for an id that
# has since landed, which is what keeps the block from growing into a second
# blanket exemption.
_RESERVED_BLOCK = re.compile(r"<!--\s*reserved-ids\b(.*?)-->", re.DOTALL)
_RESERVED_ENTRY = re.compile(r"^(L\d+)(?:\s+(\S.*?))?$")


def declared_reservations(text: str) -> tuple[dict[str, str], list[str]]:
    """Ids the ledger declares as reserved, plus complaints about the block itself."""
    reservations: dict[str, str] = {}
    problems: list[str] = []
    for block in _RESERVED_BLOCK.findall(text):
        for raw in block.splitlines():
            entry = raw.strip()
            if not entry:
                continue
            matched = _RESERVED_ENTRY.match(entry)
            if not matched:
                problems.append(
                    f"reserved-ids: cannot read {entry!r} — one `L<n> <holder>` per line"
                )
                continue
            lid, holder = matched.group(1), (matched.group(2) or "").strip()
            if not holder:
                problems.append(
                    f"reserved-ids: {lid} names no holder — a reservation with no PR "
                    "behind it is indistinguishable from a row that fell out of a merge"
                )
                continue
            reservations[lid] = holder
    return reservations, problems


def gap_problems(text: str, label: str = "docs/lessons-ledger.md") -> list[str]:
    """Skipped ids that nothing accounts for, and declarations that have expired."""
    present: set[int] = set()
    for table in _tables(text):
        _, header = table["header"]
        if not header or header[0].strip().lower() != "id":
            continue
        for _lineno, cells in table["rows"]:
            got = cells[0].strip() if cells else ""
            if re.fullmatch(r"L\d+", got):
                present.add(int(got[1:]))
    reservations, problems = declared_reservations(text)
    if len(present) < 2:
        return problems
    declared = {int(lid[1:]): holder for lid, holder in reservations.items()}
    for number in sorted(set(range(min(present), max(present) + 1)) - present):
        if number in declared:
            continue
        problems.append(
            f"{label}: L{number} is missing and undeclared — every id between "
            f"L{min(present)} and L{max(present)} is either a row or a declared "
            "reservation. If an open PR holds it, add it to the `reserved-ids` "
            "block; otherwise a row was dropped (L38 was, by merge 28a4b8b)"
        )
    for number, holder in sorted(declared.items()):
        if number in present:
            problems.append(
                f"{label}: L{number} is declared as reserved by {holder} but is now "
                "a row — drop it from the `reserved-ids` block, or the block turns "
                "into the blanket exemption it replaced"
            )
    return problems


def test_every_gap_in_the_ledger_is_a_declared_reservation():
    problems = gap_problems(LEDGER.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


# The four self-tests below are what make the one above worth having (L09/L47):
# neuter `gap_problems` and these flip red, while the real-ledger test stays
# green vacuously.
_GAP_FIXTURE = _FIXTURE_HEADER + (
    "| L10 | a | #1 | `doc — why` |\n"
    "| L11 | b | #2 | `doc — why` |\n"
    "| L13 | c | #3 | `doc — why` |\n"
)


def test_the_gap_guard_sees_a_row_deleted_from_the_middle():
    problems = gap_problems(_GAP_FIXTURE, label="planted.md")
    assert len(problems) == 1, problems
    assert "L12 is missing and undeclared" in problems[0], problems


def test_the_gap_guard_accepts_a_declared_reservation():
    declared = _GAP_FIXTURE + "\n<!-- reserved-ids\nL12 #999\n-->\n"
    assert not gap_problems(declared, label="planted.md")


def test_the_gap_guard_reports_a_reservation_that_has_already_landed():
    landed = (
        _FIXTURE_HEADER
        + "| L10 | a | #1 | `doc — why` |\n| L11 | b | #2 | `doc — why` |\n"
        + "\n<!-- reserved-ids\nL11 #999\n-->\n"
    )
    problems = gap_problems(landed, label="planted.md")
    assert len(problems) == 1, problems
    assert "declared as reserved by #999 but is now a row" in problems[0], problems


def test_a_reservation_must_name_who_holds_it():
    # A bare id would let anyone silence a dropped row by listing its number.
    problems = gap_problems(
        _GAP_FIXTURE + "\n<!-- reserved-ids\nL12\n-->\n", label="planted.md"
    )
    assert any("names no holder" in p for p in problems), problems
    assert any("undeclared" in p for p in problems), problems


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
