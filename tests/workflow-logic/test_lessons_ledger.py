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
import subprocess
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
# Source citations — the path is checked, the LINE never was (#1095)
# ---------------------------------------------------------------------------
#
# The ledger cites source locations as `path:LINE` throughout, and until this
# section nothing validated them. `enforcement_problems` above reads `cells[2]`
# only and calls `Path.exists()`, so a citation was unchecked whenever it sat in
# the Lesson or Evidence column, and its line number was discarded even when it
# did not.
#
# Both halves shipped live defects:
#
#   * L155 cited `Update-CloudflareDns.ps1:1819` for "106 excludes DKIM
#     outright". `:1819` is inside the `'AAAA'` branch of a switch and says
#     nothing about DKIM — wrong by 166 lines, in a row whose conclusion rested
#     on it. Caught by an LLM reviewer on #1093; the 26-test suite was green
#     before and after.
#   * `105-manage-record.yml:178-184` drifted three times in a week as ordinary
#     churn moved content above it, and landed on an `actions/checkout` block.
#
# What is decidable here is narrow, and the narrowness is deliberate: whether
# the cited line still MEANS what the row says is #1087's undecidable half and
# is explicitly out of scope. This asks only that the path resolves, the line
# exists, and it is not contentless.
#
# The blank-line rule is the one that catches drift-by-insertion, and it needed
# widening past "blank" to keep doing so. When #1095 was filed, `105-…:178` was
# an empty line; by the time it was fixed, further churn had moved a bare `#`
# onto it. A citation pointing at a lone comment marker is pointing at nothing
# just as much as one pointing at whitespace, so the test is "no alphanumeric
# character on the line" — which fires on both, and on nothing currently in the
# ledger.
# The extension is DERIVED, not enumerated. The first draft of this guard
# carried a hand-written suffix tuple (`.py .ps1 .yml .yaml .sh .json .js .ts
# .md`) and Copilot found it missing `.tsx`, `.mjs` and `.htaccess` — four live
# citations in this very ledger, silently skipped by the check written to stop
# citations being silently skipped (#1128). That is L133's shape exactly: a
# control whose input set is hand-maintained cannot detect an omitted input, and
# reports clean over precisely the gap it exists to close.
#
# So the rule is structural: a final extension beginning with a LETTER, which is
# what keeps `12.30:45`-shaped prose out (a numeric "extension" is not a file),
# and a line spec that is a comma-separated list of lines and ranges, because
# the ledger writes one — `720-create-repo.yml:193,274` cites two reproductions
# in a single token, and a pattern accepting only `N` and `N-M` reads it as
# prose. Both halves of that citation were stale when this guard was written.
_SPEC = r"\d+(?:-\d+)?(?:,:?\d+(?:-\d+)?)*"

# The `,:?` in `_SPEC` is not cosmetic. L223 writes
# `502-google-analytics-report.yml:230,:239` — a second line with its own colon
# inside one token — and a spec accepting only `,\d` fails to match the WHOLE
# token, so both halves went unchecked rather than one. `_CITATION_SHAPED` does
# not see it either (its tail is `(?:[-,]\d+)*`), so the coverage test below was
# silent about it too: two extractors disagreeing is the alarm this module
# relies on, and they agreed on skipping it. Both citations were stale (#1443).
_CITATION = re.compile(
    r"^(?P<path>[^\s:`]+\.[A-Za-z][A-Za-z0-9]{0,7})" rf":(?P<spec>{_SPEC})$"
)

# Gap 1 of #1443: a citation whose file is named by an EARLIER citation in the
# same row and not repeated — `740-…yml:515` renders … while `:468` already
# computes `failed`. The ledger writes this constantly (26 tokens across 15 rows
# when the rule was added) and `_CITATION` cannot match one, because there is no
# path to match. So the strongest-looking guard in this module was resolving
# roughly half of the coordinates in the rows that carry more than one.
#
# It is not a corner case: on #1441 both of L222's citations shifted by the same
# +14 from the same edit, the anchored one was caught by CI and the bare one
# passed, twice. The bare form is attached to the nearest preceding `path:LINE`
# in the same ROW (not merely the same cell — the Evidence column routinely
# continues a sentence the Lesson column started).
#
# A bare citation with NO preceding anchor stays unresolved, deliberately. That
# is the shape L196 and L155 use on purpose: L196 narrates six coordinates that
# were WRONG and writes them split so the guard does not chase them, and L155
# cites three offsets in a tree pinned to a SHA that is not this one. Neither is
# a coordinate into the current tree, and a guard that resolved them anyway
# would report a row for being accurate. `test_the_ledger_still_carries_bare
# _citations_that_resolve` is what stops that exemption becoming a silent zero.
_BARE_CITATION = re.compile(rf"^:(?P<spec>{_SPEC})$")

# Gap 2 of #1443: `_has_content` asks only whether the cited line is blank or a
# lone comment marker, so a coordinate that drifts onto real code passes — and
# most lines in a file are real code, so that is the COMMON shift, not the rare
# one. `:454` on #1441 landed on `let gateNote = '';` and shipped green.
#
# The line cannot be checked semantically (that is #1087/#1334's undecidable
# half). What can be checked is an author's own quotation of it, which the
# ledger already writes as ordinary prose: `check-environment-protection.py:221`
# (`HTTPError`). So the convention is the existing house style, given a meaning
# — a backticked token separated from a citation by nothing but whitespace or an
# opening bracket is an ANCHOR, and some cited line must contain it verbatim.
#
# Opt-in by construction: a token with a word in front of it is prose and is not
# read as an anchor. That keeps the 400-odd existing backticks out of it, and it
# is why the rule is documented in the ledger's own "Adding a lesson" section
# (#1443 AC5) — an author who does not know the adjacency is meaningful is the
# only way to get a false positive here.
_ANCHOR_GAP = re.compile(r"^[\s(\[]*$")

# Deliberately looser than `_CITATION`: anything with a `/` or a `.` in front of
# a `:LINE` is citation-SHAPED. Nothing acts on it except the coverage test
# below, whose whole job is to fail when the two disagree — an extractor that
# stops matching is how this class of guard goes quietly green.
_CITATION_SHAPED = re.compile(r"^[^\s:`]*[./][^\s:`]*:\d+(?:[-,]\d+)*$")

# Citations into another FFC repository, which CI here cannot resolve and must
# not report as broken. Every entry names the repo that holds it, so the escape
# hatch stays a statement about a real file somewhere rather than a silent skip
# — an unexplained allowlist is the shape this whole module exists to refuse.
#
# `src/lib/dashboardData.ts` — FreeForCharity/FFC-IN-ffcadmin.org. Cited by L89
# for `readJson()` joining `process.cwd()` with `public/data`; verified against
# a checkout of that repo on 2026-08-09, where `:147` reads
# `const full = path.join(process.cwd(), 'public', 'data', file)`.
# `src/app/automation/page.tsx` — same repo. Cited by L89 for the import that
# makes `workflow-catalog.json` a real consumer of `src/data`; verified the same
# way, where `:3` reads `import catalog from '@/data/workflow-catalog.json'`.
#
# `public/.htaccess` — FreeForCharity/FFC-IN-freeforcharity.org. Cited by L188
# for the extension-keyed `Cache-Control` that stamped a year's TTL on a 429.
# NOT verified: that repo is outside this session's scope, so this entry is
# taken on the row's word where the two above were checked against a checkout.
CROSS_REPO_CITATIONS = {
    "src/lib/dashboardData.ts": "FreeForCharity/FFC-IN-ffcadmin.org",
    "src/app/automation/page.tsx": "FreeForCharity/FFC-IN-ffcadmin.org",
    "public/.htaccess": "FreeForCharity/FFC-IN-freeforcharity.org",
}


def _tracked_files() -> dict[str, list[str]]:
    """Every tracked text file, as repo-relative path -> lines.

    `git ls-files` rather than a walk, so build output, virtualenvs and vendored
    trees cannot make a basename ambiguous — the ambiguity report is only useful
    if its candidate list is files a human actually wrote. Binary and
    undecodable files are dropped: a citation into one is not a thing the ledger
    does, and reading them would cost the walk for nothing.
    """
    out: dict[str, list[str]] = {}
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    for rel in proc.stdout.split("\0"):
        if not rel:
            continue
        path = REPO_ROOT / rel
        try:
            out[rel] = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
    return out


def _has_content(line: str) -> bool:
    """Whether a cited line says anything at all.

    Deliberately not `line.strip()`: see the section note above — a lone `#` is
    the shape the 105 citation drifted onto, and it is as empty a target as
    whitespace. Any letter or digit is enough to count as content, so a real
    comment (`# SUCCESS requires POSITIVE EVIDENCE`) is a legitimate anchor and
    passes.
    """
    return any(ch.isalnum() for ch in line)


def _anchor_sites(quoted: str, lines: list[str]) -> list[int]:
    """Every 1-indexed line of `lines` that contains `quoted`.

    #1334 AC1 asks the failure to name "where the anchor actually is", so that
    fixing a drifted coordinate is a copy-paste rather than a grep. The guard
    already holds both halves — the quote and the file — so doing the grep in
    the message costs one pass and removes the step the author would otherwise
    repeat by hand.

    Returns all of them and lets the caller truncate, because the COUNT is the
    part that changes the advice: one site is a re-point, several mean the quote
    is a weak anchor (`}`, `try {`) and wants replacing, and none means the
    coordinate is not what drifted.
    """
    return [n for n, line in enumerate(lines, 1) if quoted in line]


def citation_problems(
    text: str,
    files: dict[str, list[str]] | None = None,
    label: str = "docs/lessons-ledger.md",
) -> list[str]:
    """Every `path:LINE` citation in `text` that the tree does not back.

    Reads EVERY cell of every row, not `cells[2]` — that restriction is what let
    L155's 166-line-wrong pointer ship green, since it sat in the Lesson column.

    Pure over (`text`, `files`) so both the rules and their WIRING are testable
    against planted fixtures: a check only ever run against the real ledger is
    enforced by the ledger's current contents, which is not enforcement.
    """
    if files is None:
        files = _tracked_files()
    by_basename: dict[str, list[str]] = {}
    for rel in files:
        by_basename.setdefault(rel.rsplit("/", 1)[-1], []).append(rel)

    problems: list[str] = []
    for lid, cells in _rows_from_text(text):
        # Reset per ROW, not per cell: the bare form continues a sentence, and
        # the ledger's sentences run across the column boundary.
        anchor_path: str | None = None
        for column, cell in enumerate(cells):
            tokens = list(_PATHISH.finditer(cell))
            for index, match in enumerate(tokens):
                token = match.group(1).strip()
                full = _CITATION.match(token)
                bare = _BARE_CITATION.match(token)
                if full:
                    anchor_path = full.group("path")
                    cited, spec = full.group("path"), full.group("spec")
                elif bare:
                    if anchor_path is None:
                        continue
                    cited, spec = anchor_path, bare.group("spec")
                else:
                    continue
                parts = []
                for piece in spec.split(","):
                    first, _, last = piece.lstrip(":").partition("-")
                    parts.append((int(first), int(last or first)))
                shown = token if full else f"{cited}{token}"
                where = f"{label}: {lid} (column {column + 1}) cites `{shown}`"

                # The anchor, if the author wrote one. Read before resolution so
                # the "which line" and "what it says" checks report separately:
                # a row can be right about the file and wrong about the line.
                quoted = None
                if index + 1 < len(tokens):
                    between = cell[match.end() : tokens[index + 1].start()]
                    following = tokens[index + 1].group(1)
                    if (
                        _ANCHOR_GAP.match(between)
                        and following.strip()
                        and not _CITATION.match(following.strip())
                        and not _BARE_CITATION.match(following.strip())
                    ):
                        quoted = following

                if cited in CROSS_REPO_CITATIONS:
                    continue
                if cited in files:
                    resolved = cited
                else:
                    candidates = sorted(by_basename.get(cited.rsplit("/", 1)[-1], []))
                    if not candidates:
                        problems.append(
                            f"{where}, and no tracked file matches that path. Correct "
                            "it, or — if it is deliberately a file in another FFC repo "
                            "— add it to CROSS_REPO_CITATIONS naming the repo"
                        )
                        continue
                    if len(candidates) > 1:
                        problems.append(
                            f"{where}, whose basename is ambiguous: {candidates}. "
                            "Write the path out from the repo root so the citation "
                            "names one file"
                        )
                        continue
                    resolved = candidates[0]

                lines = files[resolved]
                covered: list[str] = []
                for start, end in parts:
                    if end < start:
                        problems.append(
                            f"{where}, which is a backwards line range ({start}-{end})"
                        )
                        continue
                    if start < 1 or end > len(lines):
                        problems.append(
                            f"{where} -> {resolved}, which has {len(lines)} lines — "
                            f"the cited range {start}-{end} is outside the file"
                        )
                        continue
                    covered.extend(lines[start - 1 : end])
                    if not _has_content(lines[start - 1]):
                        problems.append(
                            f"{where} -> {resolved}:{start}, which is blank or a bare "
                            "comment marker. Content moved above it and the citation "
                            "did not follow; re-derive the line from what the row "
                            "describes"
                        )
                if quoted is not None and covered:
                    if not any(quoted in line for line in covered):
                        sites = _anchor_sites(quoted, lines)
                        # Which of the two explanations applies is decidable, and
                        # the old message left it to the author (#1334 AC1). If
                        # the quote is somewhere in the file the coordinate
                        # drifted and the fix is the line number below; if it is
                        # nowhere, "re-derive it from the quote" is impossible
                        # advice and the line was reworded or the quote is prose.
                        if not sites:
                            fix = (
                                "It appears nowhere in that file, so the "
                                "coordinate is not what drifted: the line was "
                                "reworded, or the token is ordinary prose — put a "
                                "word between it and the citation so it stops "
                                "reading as a claim about the line"
                            )
                        elif len(sites) == 1:
                            fix = (
                                f"It is on line {sites[0]}, so the coordinate "
                                f"drifted — re-point the citation to `:{sites[0]}`"
                            )
                        else:
                            shown = ", ".join(str(n) for n in sites[:5])
                            more = "" if len(sites) <= 5 else ", …"
                            fix = (
                                f"It is on {len(sites)} lines ({shown}{more}), so "
                                "re-point the citation to the one the row means — "
                                "and prefer a quote that occurs once, or the anchor "
                                "pins a shape rather than a line"
                            )
                        problems.append(
                            f"{where} -> {resolved}, and the row quotes "
                            f"`{quoted}` beside it — which none of the cited lines "
                            f"contains. {fix}"
                        )
    return problems


def test_every_source_citation_in_the_ledger_resolves_and_points_at_content():
    problems = citation_problems(LEDGER.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_the_ledger_actually_contains_citations_to_check():
    """The positive control: without it, the test above passes on zero work.

    Every rule in `citation_problems` is a filter, so a regex that stops
    matching — a new suffix, a changed cell format, `_rows_from_text` returning
    nothing — turns the guard silently green. That is the same silent-zero shape
    as #1055, and this module has been burned by it once already.
    """
    text = LEDGER.read_text(encoding="utf-8")
    found = [
        token
        for _, cells in _rows_from_text(text)
        for cell in cells
        for token in _PATHISH.findall(cell)
        if _CITATION.match(token.strip())
    ]
    assert len(found) >= 10, (
        f"expected the ledger's usual population of `path:LINE` citations, saw "
        f"{len(found)} — if the ledger really has stopped citing sources, lower "
        "this; until then it means the extractor stopped matching"
    )


def test_no_citation_shaped_token_escapes_the_extractor():
    """The coverage check the first draft of this guard needed and lacked.

    Every rule in `citation_problems` runs behind `_CITATION`, so a token that
    pattern does not match is not reported as anything — it is simply absent,
    and the guard stays green. That is how the hand-written suffix tuple hid
    four live citations (`.tsx`, `.mjs`, `.htaccess`) from the check written to
    stop citations going unchecked, until Copilot read the list (#1128).

    Comparing the strict extractor against a deliberately loose one is what
    makes the omission loud: a future citation shape this guard cannot parse
    fails HERE, naming the token, instead of being skipped in silence. Note the
    two patterns must not share code, or they agree by construction.
    """
    skipped = []
    for lid, cells in _rows():
        for cell in cells:
            for token in _PATHISH.findall(cell):
                token = token.strip()
                if _CITATION_SHAPED.match(token) and not _CITATION.match(token):
                    skipped.append(f"{lid}: `{token}`")
    assert not skipped, (
        "these tokens look like `path:LINE` citations and the extractor does not "
        "match them, so nothing checks them:\n  " + "\n  ".join(skipped) + "\n"
        "Widen `_CITATION` — do not widen `_CITATION_SHAPED`, which exists to "
        "disagree."
    )


def test_the_extractor_matches_the_extensions_the_ledger_actually_uses():
    """The #1128 regression, pinned by extension rather than by count.

    `.tsx` and `.mjs` are the two Copilot named; `.htaccess` is the third the
    sweep found, and it is the interesting one — it is a dotfile, so the
    "extension" is the whole name and a tuple of suffixes was never going to
    hold it. `.py`/`.yml` are here as the control: widening must not drop what
    the enumerated list already covered.
    """
    for ext in ("py", "yml", "ps1", "md", "tsx", "mjs", "htaccess", "json"):
        token = f"some/path.{ext}:12"
        assert _CITATION.match(token), (
            f"`{token}` must parse as a citation — the ledger cites .{ext} files"
        )
    for prose in ("12.30:45", "npm ci", "docs/notes.md", "L43:5"):
        assert not _CITATION.match(prose), (
            f"`{prose}` is not a citation and must not be parsed as one"
        )


def test_every_cross_repo_allowance_names_a_repo():
    """The escape hatch must stay a claim about a real file somewhere."""
    for path, repo in CROSS_REPO_CITATIONS.items():
        assert re.fullmatch(r"[\w.-]+/[\w.-]+", repo), (
            f"{path} is allowed as cross-repo but its value {repo!r} is not an "
            "owner/repo — an allowlist entry that names no repo is a silent skip"
        )
        assert not (REPO_ROOT / path).exists(), (
            f"{path} is allowlisted as cross-repo but DOES exist here — remove the "
            "entry so the citation is checked like every other one"
        )


# The self-tests below are what make the two above worth having (L09/L47).
# Each plants its own tree, so they discriminate whether or not the real ledger
# happens to be clean at the time.
_CITE_FILES = {
    "scripts/thing.py": ["import os", "", "def main():", "    return 1"],
    "docs/notes.md": ["# Notes", "prose"],
    "a/dup.yml": ["on: push", "jobs: {}"],
    "b/dup.yml": ["on: pull_request", "jobs: {}"],
}


def _cite_row(lid: str, lesson: str, evidence: str, enforced: str) -> str:
    return f"| {lid} | {lesson} | {evidence} | {enforced} |\n"


def test_the_citation_guard_sees_a_path_that_does_not_resolve():
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "a lesson", "`scripts/gone.py:3`", "`doc — why`"
    )
    problems = citation_problems(planted, _CITE_FILES, label="planted.md")
    assert len(problems) == 1 and "L90" in problems[0], problems
    assert "no tracked file matches" in problems[0], problems


def test_the_citation_guard_sees_an_ambiguous_basename_and_lists_the_candidates():
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "a lesson", "`dup.yml:1`", "`doc — why`"
    )
    problems = citation_problems(planted, _CITE_FILES, label="planted.md")
    assert len(problems) == 1 and "ambiguous" in problems[0], problems
    assert "a/dup.yml" in problems[0] and "b/dup.yml" in problems[0], (
        f"the failure must list BOTH candidates, or it cannot be acted on: {problems}"
    )


def test_the_citation_guard_sees_a_line_past_the_end_of_the_file():
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "a lesson", "`scripts/thing.py:99`", "`doc — why`"
    )
    problems = citation_problems(planted, _CITE_FILES, label="planted.md")
    assert len(problems) == 1 and "outside the file" in problems[0], problems
    assert "4 lines" in problems[0], f"name the real length: {problems}"


def test_the_citation_guard_sees_a_line_that_content_moved_off():
    """Both shapes of the 105 drift: the empty line, and the bare `#`.

    `105-manage-record.yml:178` was empty when #1095 was filed and a lone `#` by
    the time it was fixed — the same defect, one week of churn apart. A rule
    written as `line.strip()` catches the first and passes the second.
    """
    blank = _FIXTURE_HEADER + _cite_row(
        "L90", "a lesson", "`scripts/thing.py:2`", "`doc — why`"
    )
    problems = citation_problems(blank, _CITE_FILES, label="planted.md")
    assert len(problems) == 1 and "blank or a bare comment marker" in problems[0], (
        f"a citation onto an empty line must fail: {problems}"
    )

    marker = {"scripts/thing.py": ["import os", "   #", "def main():"]}
    problems = citation_problems(blank, marker, label="planted.md")
    assert len(problems) == 1 and "blank or a bare comment marker" in problems[0], (
        f"a citation onto a lone comment marker must fail too: {problems}"
    )

    real_comment = {"scripts/thing.py": ["import os", "# a real remark", "x = 1"]}
    assert not citation_problems(blank, real_comment, label="planted.md"), (
        "a comment with actual text is a legitimate anchor and must NOT fail — "
        "the ledger cites several"
    )


def test_the_citation_guard_reads_every_column_not_just_the_tier():
    """The regression that caused defect 1 (#1095).

    L155's wrong pointer sat in the **Lesson** column, where `_claimed_paths`
    never looked. This fixture puts a bad citation in each of the three columns
    with a clean tier cell, so reverting the walk to `cells[2]` leaves it green.
    """
    planted = _FIXTURE_HEADER + _cite_row(
        "L90",
        "a lesson citing `scripts/thing.py:99` in its prose",
        "reviewed at `scripts/gone.py:1`",
        "`scripts/thing.py:1`",
    )
    problems = citation_problems(planted, _CITE_FILES, label="planted.md")
    assert len(problems) == 2, f"one finding per bad citation expected: {problems}"
    assert any("column 1" in p for p in problems), (
        f"the Lesson column must be read — this is the #1095 bug itself: {problems}"
    )
    assert any("column 2" in p for p in problems), (
        f"the Evidence column must be read: {problems}"
    )
    assert not any("column 3" in p for p in problems), (
        f"the tier cell here is a GOOD citation and must stay silent: {problems}"
    )


def test_the_citation_guard_leaves_good_citations_and_non_citations_alone():
    """Its discriminators. Without these the rule is `report everything`."""
    clean = _FIXTURE_HEADER + _cite_row(
        "L90",
        "resolved exactly at `scripts/thing.py:1` and by basename at `thing.py:3-4`",
        "`npm ci`, `--paginate`, and a bare `scripts/thing.py` with no line",
        "`docs/notes.md:1`",
    )
    assert not citation_problems(clean, _CITE_FILES, label="planted.md"), (
        "exact paths, unique basenames, ranges, and tokens that are not citations "
        "must all pass"
    )


# ---------------------------------------------------------------------------
# #1443 gap 1 — the bare `:NNN` sibling
# ---------------------------------------------------------------------------
_BARE_FILES = {
    "scripts/thing.py": ["import os", "", "def main():", "    return 1"],
    "docs/notes.md": ["# Notes", "prose"],
}


def test_the_citation_guard_resolves_a_bare_line_against_the_rows_earlier_path():
    """AC1. `path.py:1` then `:2` — both resolved against the same file."""
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1` and also `:2`", "#1", "`doc — why`"
    )
    problems = citation_problems(planted, _BARE_FILES, label="planted.md")
    assert len(problems) == 1, f"exactly the bare one must fail: {problems}"
    assert "scripts/thing.py:2" in problems[0], (
        f"the finding must name the file the bare form resolved to: {problems}"
    )
    # The discriminator: identical row, bare line moved onto content.
    good = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1` and also `:3`", "#1", "`doc — why`"
    )
    assert not citation_problems(good, _BARE_FILES, label="planted.md"), (
        "a bare citation onto a real line must pass — otherwise the rule above "
        "is `report every bare token`"
    )


def test_a_bare_citation_with_no_earlier_path_in_the_row_is_left_alone():
    """The L196/L155 exemption, which is the whole reason it is opt-in.

    Those rows write coordinates that were WRONG, or that belong to a tree
    pinned to another SHA. Resolving them against this tree would report a row
    for being accurate — the direction that gets a guard switched off (#1432).
    """
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "drifted onto a blank at `:2` on that branch", "#1", "`doc — why`"
    )
    assert not citation_problems(planted, _BARE_FILES, label="planted.md"), (
        "a bare `:NNN` with no path anywhere before it names no file and must "
        "not be guessed at"
    )


def test_the_bare_form_carries_across_the_column_boundary():
    """Scoped to the ROW, not the cell: L228's sentence runs across it."""
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1`", "and the repair at `:2`", "`doc — why`"
    )
    problems = citation_problems(planted, _BARE_FILES, label="planted.md")
    assert len(problems) == 1 and "column 2" in problems[0], (
        f"the Evidence cell's bare citation must resolve too: {problems}"
    )


def test_the_comma_colon_spec_is_parsed_rather_than_skipped_whole():
    """L223 writes `…yml:230,:239`. The old spec matched neither half.

    The failure mode was not a missed line but a missed TOKEN: `_CITATION`
    rejected the whole thing, `_CITATION_SHAPED` rejected it too, and the
    coverage test that exists to catch exactly that disagreement was silent
    because they agreed. Both cited lines were stale.
    """
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1,:2`", "#1", "`doc — why`"
    )
    problems = citation_problems(planted, _BARE_FILES, label="planted.md")
    assert len(problems) == 1 and ":2" in problems[0], (
        f"the second element of a `N,:M` spec must be checked: {problems}"
    )


# ---------------------------------------------------------------------------
# #1443 gap 2 — the quoted anchor
# ---------------------------------------------------------------------------
def test_a_quoted_anchor_beside_a_citation_must_appear_on_a_cited_line():
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1` (`import sys`)", "#1", "`doc — why`"
    )
    problems = citation_problems(planted, _BARE_FILES, label="planted.md")
    assert len(problems) == 1 and "import sys" in problems[0], (
        f"a quote the cited line does not contain must be reported: {problems}"
    )
    ok = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1` (`import os`)", "#1", "`doc — why`"
    )
    assert not citation_problems(ok, _BARE_FILES, label="planted.md"), (
        "the same shape with a quote that IS on the line must pass"
    )
    # A range anchors anywhere inside itself, not only on its first line.
    span = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:3-4` (`return 1`)", "#1", "`doc — why`"
    )
    assert not citation_problems(span, _BARE_FILES, label="planted.md"), (
        "an anchor on the last line of a cited range must satisfy it"
    )


# ---------------------------------------------------------------------------
# #1334 AC1 — the failure names where the anchor actually is
# ---------------------------------------------------------------------------
#
# The rule above was already the load-bearing half; what #1334 asks for on top
# is that the message state "the found value" so the repair is a copy-paste.
# Worth more than ergonomics: the pre-#1334 text offered the author two
# explanations — "the coordinate drifted" or "the quote is not an anchor" — and
# made them work out which. That is decidable from what the guard already holds,
# and the two need OPPOSITE fixes, so a message that names both points half the
# readers at the wrong one. Grepping the resolved file settles it.
_DRIFT_FILES = {
    "scripts/thing.py": [
        "import os",
        "",
        "def main():",
        "    return 1",
        "    # once: the anchor moved here",
        "}",
        "}",
    ],
}


def test_a_drifted_anchor_is_reported_with_the_line_it_moved_to():
    """One occurrence: the message must hand back the coordinate to paste."""
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1` (`the anchor moved here`)", "#1", "`doc — why`"
    )
    problems = citation_problems(planted, _DRIFT_FILES, label="planted.md")
    assert len(problems) == 1, problems
    assert "It is on line 5" in problems[0] and "`:5`" in problems[0], (
        "a drifted anchor with one site must name that line and offer it as the "
        f"replacement coordinate: {problems}"
    )
    # The discriminator: the SAME row citing the line the anchor is on passes,
    # so the assertion above is about drift and not about the quote's presence.
    good = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:5` (`the anchor moved here`)", "#1", "`doc — why`"
    )
    assert not citation_problems(good, _DRIFT_FILES, label="planted.md"), (
        "citing the line the anchor is on must pass"
    )


def test_an_anchor_that_is_nowhere_in_the_file_says_so_instead_of_advising_a_re_derive():
    """The branch whose old advice was impossible to follow.

    "Re-derive the coordinate from the quote" cannot be done when the quote is
    not in the file at all — the line was reworded, or the token was never an
    anchor. Naming that is the difference between a fixable failure and one the
    author argues with.
    """
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1` (`def teardown():`)", "#1", "`doc — why`"
    )
    problems = citation_problems(planted, _DRIFT_FILES, label="planted.md")
    assert len(problems) == 1, problems
    assert "appears nowhere in that file" in problems[0], (
        f"a quote absent from the file must be reported as absent: {problems}"
    )
    assert "re-point" not in problems[0], (
        "and it must NOT offer a coordinate to re-point to — there is none, and "
        f"advising one is what sends the author looking for a line: {problems}"
    )


def test_a_weak_anchor_is_reported_with_its_count_not_a_single_coordinate():
    """Several occurrences: naming one of them would be a guess.

    `}` is the shape that produces this, and it is worth saying out loud rather
    than picking the first hit — a quote occurring many times pins a shape, not
    a line, so the anchor is weak even once the coordinate is corrected.
    """
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1` (`}`)", "#1", "`doc — why`"
    )
    problems = citation_problems(planted, _DRIFT_FILES, label="planted.md")
    assert len(problems) == 1, problems
    assert "on 2 lines (6, 7)" in problems[0], (
        f"a multi-site anchor must name the count and the lines: {problems}"
    )
    assert "occurs once" in problems[0], (
        f"and must say the quote itself wants replacing: {problems}"
    )


def test_the_anchor_site_scan_is_one_indexed_and_finds_every_occurrence():
    """The helper under all three branches, pinned directly.

    An off-by-one here is invisible through the messages — every branch would
    still fire, just naming a neighbouring line — so it is asserted rather than
    inferred. `:5` is the coordinate the ledger would be told to paste.
    """
    lines = _DRIFT_FILES["scripts/thing.py"]
    assert _anchor_sites("the anchor moved here", lines) == [5]
    assert _anchor_sites("}", lines) == [6, 7]
    assert _anchor_sites("nothing here", lines) == []


def test_a_backticked_token_separated_by_a_word_is_not_read_as_an_anchor():
    """AC3's false-positive control, and the convention's whole escape hatch.

    The ledger carries several hundred backticked tokens; only the ones written
    hard against a citation are claims about that line. If prose counted, every
    row would be a finding and the guard would be switched off within a week.
    """
    prose = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1` and then `npm ci` ran", "#1", "`doc — why`"
    )
    assert not citation_problems(prose, _BARE_FILES, label="planted.md"), (
        "a word between the citation and the next token means it is prose"
    )
    # Nor may a second citation be eaten as the first one's anchor.
    pair = _FIXTURE_HEADER + _cite_row(
        "L90", "`scripts/thing.py:1` `docs/notes.md:2`", "#1", "`doc — why`"
    )
    assert not citation_problems(pair, _BARE_FILES, label="planted.md"), (
        "two adjacent citations are two citations, not a citation and an anchor"
    )


def test_the_1441_regression_a_shifted_bare_citation_is_reported():
    """AC2, derived from the shipped row rather than a hand-written before (L47).

    On #1441 an inserted comment block shifted `740-…yml` by +14. L222 carries
    two citations into that file; the anchored one landed on a blank line and CI
    caught it, the bare one landed on `let gateNote = '';` and passed twice. The
    fixture re-creates exactly that by walking the live row's bare citation back
    to its pre-shift value, so the test decays with the ledger instead of
    asserting against a frozen copy of it.

    #1447 shifted the same file twice and reproduced the split precisely both
    times. First by +39: the anchored `:515` landed on a blank line and CI
    caught it, while the bare `:468` landed on a lone `}`. Then a review fix
    added comments and shifted it again by +11, landing the anchored `:581` on
    a blank line and the bare `:507` on a lone `try {`. Both bare coordinates
    were real content, so the blank rule stayed silent for both and only the
    quoted anchor caught them. The pre-shift coordinate below is therefore that
    PR's own latest shift, not a number chosen to make the test pass — and the
    fact that it recurred within one PR is the argument for the anchor rule.
    """
    row = next(
        line
        for line in LEDGER.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("| L222 ")
    )
    assert "`:518`" in row, (
        "L222 no longer carries the bare citation this regression is about — "
        "re-derive the fixture from whichever row does, or drop this test"
    )
    shifted = _FIXTURE_HEADER + row.replace("`:518`", "`:507`") + "\n"
    problems = citation_problems(shifted, label="planted.md")
    assert problems, (
        "the pre-#1441 coordinate must be reported; it is real code, so only "
        "the quoted anchor can catch it"
    )
    assert any("const failed" in p for p in problems), (
        f"and it must be the ANCHOR that catches it, not the blank rule: {problems}"
    )
    assert not citation_problems(
        _FIXTURE_HEADER + row + "\n", label="planted.md"
    ), "the row as shipped must be clean — otherwise the above proves nothing"


def test_the_ledger_still_carries_bare_citations_and_anchors_to_check():
    """Both new rules are filters, so both can go silently green (#1055).

    `test_the_ledger_actually_contains_citations_to_check` guards the anchored
    form for exactly this reason; these two shapes need the same positive
    control or a regex that stops matching reads as a clean ledger.
    """
    text = LEDGER.read_text(encoding="utf-8")
    bare = anchors = 0
    for _, cells in _rows_from_text(text):
        seen_path = False
        for cell in cells:
            tokens = list(_PATHISH.finditer(cell))
            for i, m in enumerate(tokens):
                tok = m.group(1).strip()
                if _CITATION.match(tok):
                    seen_path = True
                elif _BARE_CITATION.match(tok) and seen_path:
                    bare += 1
                else:
                    continue
                if i + 1 < len(tokens):
                    nxt = tokens[i + 1].group(1).strip()
                    if (
                        _ANCHOR_GAP.match(cell[m.end() : tokens[i + 1].start()])
                        and nxt
                        and not _CITATION.match(nxt)
                        and not _BARE_CITATION.match(nxt)
                    ):
                        anchors += 1
    assert bare >= 10, (
        f"expected the ledger's usual population of resolvable bare citations, "
        f"saw {bare} — if they really are gone, lower this; until then it means "
        "the bare extractor stopped matching"
    )
    assert anchors >= 3, (
        f"saw {anchors} quoted anchors — the convention is documented in the "
        "ledger's 'Adding a lesson' section and used by L222/L223/L244; a zero "
        "here means the adjacency rule stopped firing, not that authors stopped"
    )


def test_the_citation_guard_honours_the_cross_repo_allowlist_and_nothing_else():
    allowed = next(iter(CROSS_REPO_CITATIONS))
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", f"a lesson citing `{allowed}:147`", "#1", "`doc — why`"
    )
    assert not citation_problems(planted, _CITE_FILES, label="planted.md"), (
        f"{allowed} is allowlisted as cross-repo and must not be reported"
    )
    sibling = _FIXTURE_HEADER + _cite_row(
        "L90", "a lesson citing `src/lib/other.ts:9`", "#1", "`doc — why`"
    )
    assert citation_problems(sibling, _CITE_FILES, label="planted.md"), (
        "the allowlist must cover the ONE path it names, not its directory"
    )


def test_the_citation_guard_reports_a_backwards_range():
    planted = _FIXTURE_HEADER + _cite_row(
        "L90", "a lesson", "`scripts/thing.py:4-2`", "`doc — why`"
    )
    problems = citation_problems(planted, _CITE_FILES, label="planted.md")
    assert len(problems) == 1 and "backwards line range" in problems[0], problems


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


# --- The append-only marker in CLAUDE.md, which no test held ----------------
#
# `CLAUDE.md` carries a marker reading "Everything below this line is
# append-only on purpose", because `docs/lessons-ledger.md` cites that file by
# `CLAUDE.md:<line>` and an insertion ABOVE the marker renumbers every one of
# those anchors. Run 189 broke L180's citation in exactly that way, and runs
# 216-217 each shipped a CLAUDE.md PR that had to be hand-placed against the
# rule because nothing enforced it.
#
# The citation guard earlier in this module catches the CONSEQUENCE, but only
# for rows carrying a quoted anchor — it is opt-in, so an un-enrolled row can
# survive a shift by landing on another non-blank line (#1455: 45 of 53 rows
# rested on that alone). This holds the RULE instead, which is cheaper and does
# not depend on enrolment.
#
# The pin is the whole mechanism: appending BELOW the marker cannot move it, so
# a correct change leaves this green, while any insertion above it fails with
# the delta. If content above the marker is ever legitimately removed, updating
# MARKER_LINE forces re-verifying the `CLAUDE.md:` citations in the same commit
# — that coupled work is the point, not a chore the constant imposes.

CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
_MARKER = "append-only on purpose"
MARKER_LINE = 1795


def marker_problems(text, label="CLAUDE.md", expected=MARKER_LINE):
    problems = []
    sites = [i for i, line in enumerate(text.splitlines(), 1) if _MARKER in line]
    if not sites:
        problems.append(
            f"{label}: the append-only marker ({_MARKER!r}) is gone. It is the "
            "only thing telling an agent that new sections go at the END of this "
            "file, so without it every `CLAUDE.md:<line>` citation in the ledger "
            "is one insertion away from pointing at the wrong line"
        )
        return problems
    if len(sites) > 1:
        problems.append(
            f"{label}: the append-only marker appears on lines {sites} — it must "
            "be unique, or 'below the marker' names more than one place and the "
            "rule stops being checkable"
        )
    site = sites[0]
    if site != expected:
        moved = site - expected
        where = "later" if moved > 0 else "earlier"
        problems.append(
            f"{label}: the append-only marker moved from line {expected} to "
            f"{site} ({abs(moved)} line(s) {where}), so content was added or "
            f"removed ABOVE it. Put your addition below line {site} instead — the "
            "marker exists because the ledger cites this file by line number. If "
            "you really did change content above it, update MARKER_LINE in "
            "tests/workflow-logic/test_lessons_ledger.py and re-verify every "
            "`CLAUDE.md:` citation in docs/lessons-ledger.md in the same commit"
        )
    return problems


def test_the_append_only_marker_is_where_the_ledger_anchors_assume_it_is():
    problems = marker_problems(CLAUDE_MD.read_text(encoding="utf-8"))
    assert not problems, "\n".join(problems)


def test_claude_md_actually_carries_the_marker_this_guard_pins():
    # Guards the guard (L09/L47): if the sentinel is ever reworded, the test
    # above would report "the marker is gone" rather than going vacuous-green,
    # but assert the real file carries it so the pin is never checked against a
    # file that lost the rule entirely.
    assert _MARKER in CLAUDE_MD.read_text(encoding="utf-8"), (
        "CLAUDE.md no longer contains the append-only sentinel this guard pins"
    )


# The four self-tests below are what make the two above worth having (L09/L47):
# neuter `marker_problems` and these flip red, while the real-file test stays
# green vacuously.
_MARKER_FIXTURE = (
    "intro\n"
    "section one\n"
    "\n"
    "> **Everything below this line is append-only on purpose.**\n"
    "appended section\n"
)  # the marker sits on line 4


def test_the_marker_guard_sees_content_inserted_above_the_marker():
    shifted = "preamble someone added\n" + _MARKER_FIXTURE
    problems = marker_problems(shifted, label="planted.md", expected=4)
    assert len(problems) == 1, problems
    assert "moved from line 4 to 5" in problems[0], problems
    assert "1 line(s) later" in problems[0], problems


def test_the_marker_guard_leaves_an_append_below_the_marker_alone():
    # The correct way to add to the file: everything after the marker.
    appended = _MARKER_FIXTURE + "a brand new section\nand its body\n"
    assert not marker_problems(appended, label="planted.md", expected=4)


def test_the_marker_guard_sees_the_marker_deleted_outright():
    problems = marker_problems("intro\nsection one\n", label="planted.md", expected=4)
    assert len(problems) == 1, problems
    assert "is gone" in problems[0], problems


def test_the_marker_guard_sees_a_duplicated_marker():
    doubled = _MARKER_FIXTURE + _MARKER_FIXTURE
    problems = marker_problems(doubled, label="planted.md", expected=4)
    assert any("must" in p and "unique" in p for p in problems), problems
    assert any("[4, 9]" in p for p in problems), problems


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
