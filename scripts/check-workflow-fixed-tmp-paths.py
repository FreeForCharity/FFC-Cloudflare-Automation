#!/usr/bin/env python3
"""Guard: a workflow body must not name a fixed `/tmp/<name>` path (#1247).

Seven workflows wrote their scratch files to fixed, unqualified `/tmp` paths --
`/tmp/entries.json`, `/tmp/smoke-dispatched.tsv`, `/tmp/probes/<host>.head` and
eleven more. `tests/workflow-logic/` extracts those bodies and executes them
verbatim, so two concurrent test processes shared one file and each read the
other's half-written state.

WHY THIS IS STATIC AND NOT A TEST
    On a GitHub-hosted runner the defect is unreachable: one job per VM, so the
    paths cannot collide, and the workflow-logic suite runs its modules in
    sequence. Nothing in CI can turn red on it. It is reachable only locally,
    which is exactly where AGENTS.md's L191 treatment/control comparison runs --
    and that recipe starts the composed suite and the control suite AT THE SAME
    TIME. Same asymmetry as #929/#945/#972: a class CI cannot observe has to be
    read out of the source.

WHY IT IS WORTH A GUARD RATHER THAN A ONE-TIME CLEANUP
    The contamination is nondeterministic about WHICH SIDE of the comparison it
    lands on, so it manufactures a difference where none exists -- and that
    difference reads as a regression introduced by the PR under review.
    Measured 2026-09-07 composing #1238 + #1245 + #1246 onto `main`: `test_738`
    failed on the composed side alone. Three clean PRs looked like one broken
    fleet-drift audit; a serial re-run showed identical failure sets on both
    sides. The cost is paid in the direction of a false accusation against an
    author, which is the direction a reviewer is least likely to double-check.

WHAT COUNTS AS A VIOLATION
    A `/tmp` whose next character cannot continue a directory name -- that is,
    anything but a letter, a digit, `_`, `-` or `.` -- in code a runner will
    execute: a step's `run:` body, or the `script:` of an
    `actions/github-script` step, in a workflow or in a composite action. That
    is `/tmp/entries.json`, and it is also the bare directory -- `tmpd=/tmp`,
    `cd /tmp`, `>/tmp`. `/tmpfs`, `/tmp_old`, `/tmp-build` and `/tmp.d` are
    different directories and are not findings; the boundary is asserted by a
    test, not by inspection, and it is stated as the exact character set
    because the first version of this sentence said "not continued into a
    different name" while the pattern excluded only `[A-Za-z0-9_]` -- so
    `/tmp-build` was a finding and the docstring said it was not. Raised by
    Copilot on #1263.

    THE BARE DIRECTORY IS THE SAME DEFECT (#1260). This guard shipped matching
    the two-character sequence `/tmp/`, which is exactly right about the REMEDY
    -- neither accepted spelling produces it -- and that is a different question
    from whether every DEFECT spelling matches. One did not: assigning the bare
    directory into a variable and appending the segments later.
    `tmpd=/tmp` with `"$tmpd/smoke-body"` downstream is the identical
    collision, and it was measured passing on a mutant of 738 with the freeze
    still reporting 0 entries. Found by getting a mutation wrong and reading the
    green as a result rather than as a failed experiment.

    Reads are findings too, not just writes. A read is how the collision
    SURFACES -- 738 died on `json.loads` of a file another process was
    truncating -- and a body that only reads a fixed path is depending on some
    other body having written one.

    The remedy is `RUNNER_TEMP`, which every runner sets per job and which the
    workflow-logic harness now sets per test process (`wf_extract.runner_temp`):

        tmpd="${RUNNER_TEMP:-/tmp}"                 # bash
        const tmpd = process.env.RUNNER_TEMP || "/tmp";   # github-script / node

    Both spellings end at a bare `/tmp`, which the rule above does match, so
    they are STRIPPED FROM THE LINE before it is scanned rather than listed as
    exceptions. The distinction is not cosmetic: an exception list is matched
    against the finding, so `${RUNNER_TEMP:-/tmp/x}` would be excused by an
    entry for `${RUNNER_TEMP:-/tmp}`, whereas stripping leaves the `/x` behind
    and it is reported. A guard that flagged the remedy it prescribes is how a
    guard earns being switched off (#1019) -- all 15 remedy sites in the tree
    are clean, and that is pinned by a test rather than assumed.

    COMMENT LINES ARE EXCLUDED. A `#`- or `//`-led line is prose, it executes
    nothing, and #1019 records four separate guards in 48h that flagged text
    ABOUT the thing they catch -- including this file's own CI step, whose
    explanation necessarily names the paths. The exclusion is by leading token
    only: a trailing comment on a line of code is still scanned, which
    over-reports rather than under-reports.

FAIL CLOSED
    A workflow that will not parse, a `run:` that is not a string, and a stale
    freeze entry are each REPORTED. A guard that goes quiet on what it does not
    understand reads as a pass, which is the failure this repo keeps
    rediscovering.

THE FREEZE IS EMPTY, AND THAT IS THE POINT
    The population was 22 references across 7 files and is now 0, so this fails
    on ANY instance anywhere in the tree. An entry in `KNOWN_FIXED_TMP_PATHS`
    would be an explicit, reasoned exception -- not the normal state.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
ACTIONS = REPO_ROOT / ".github" / "actions"

# The two accepted remedy spellings, stripped from a line BEFORE it is matched
# (see `scan_body`). They are removed rather than excepted so that the allowance
# stays "these exact forms do not survive to be matched": an exception list is
# something a near-miss spelling slips through, and `${RUNNER_TEMP:-/tmp/x}` is
# a near-miss that must still be a finding.
ACCEPTED_RUNNER_TEMP = (
    re.compile(r"\$\{RUNNER_TEMP:-/tmp\}"),
    re.compile(r"""process\.env\.RUNNER_TEMP\s*\|\|\s*['"]/tmp['"]"""),
)

# `/tmp` not continued into a longer directory name, plus whatever path follows.
#
# The negative lookahead is the boundary: `/tmp` and `/tmp/entries.json` match,
# `/tmpfs`, `/tmp_old`, `/tmp-build` and `/tmp.d` do not. The class is every
# character that can continue a name, NOT just the identifier characters --
# `-` and `.` are legal in a directory name and were missing at first, which
# made `/tmp-build` a finding (Copilot, #1263). Nothing is lost by excluding
# them: this guard is about the SHARED `/tmp`, and a path one character longer
# is a different directory, so flagging it is the #1019 shape.
#
# Matching the bare directory is what #1260 added -- `tmpd=/tmp` with
# `"$tmpd/entries.json"` downstream is the identical collision, spelled without
# a trailing segment, and the `/tmp/` rule read it as clean while reporting the
# freeze at 0.
#
# The trailing class keeps the whole path in the finding text, so a report still
# names the file and not just the directory. The backtick is in the terminator
# set because a JS template literal is one of the two spellings this guard has
# to read: without it, `` `/tmp/probes/x.head` `` captures its own closing
# backtick into the finding text, so the reported snippet is not the path and a
# freeze entry would have to carry the punctuation to match. Raised by Copilot
# on #1256.
FIXED_TMP_RE = re.compile(r"/tmp(?![A-Za-z0-9_.-])[^\s\"'`);:,]*")

# Leading tokens that make a line prose rather than code, in the two languages a
# workflow body is written in.
COMMENT_PREFIXES = ("#", "//")

# file -> the offending snippets deliberately left in place, with a reason in a
# comment beside each. Empty: the population is zero (#1247).
KNOWN_FIXED_TMP_PATHS: dict[str, tuple[str, ...]] = {}


class Finding:
    def __init__(self, source: str, job: str, step: str, line: int, text: str) -> None:
        self.source = source
        self.job = job
        self.step = step
        self.line = line
        self.text = text

    def __str__(self) -> str:
        return (
            f"{self.source}:{self.line} job '{self.job}' step '{self.step}' "
            f"names a fixed temp path: {self.text.strip()}"
        )


def executable_bodies(doc: dict) -> list[tuple[str, str, str]]:
    """Every (job, step, body) a runner will execute in this document.

    Covers workflow `jobs.<id>.steps[]` and composite `runs.steps[]`, and reads
    both a step's `run:` and an `actions/github-script` step's `with.script:`.
    A step can carry both only in the sense that `uses:` and `run:` are mutually
    exclusive, so no body is ever collected twice.
    """
    bodies: list[tuple[str, str, str]] = []

    def collect(job_id: str, steps: object) -> None:
        if not isinstance(steps, list):
            return
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            name = str(step.get("name") or step.get("id") or f"#{index}")
            run = step.get("run")
            if run is not None:
                # Not a string means this is not something we can read; say so
                # rather than skipping it.
                bodies.append((job_id, name, run if isinstance(run, str) else repr(run)))
            uses = str(step.get("uses") or "")
            with_block = step.get("with")
            if "github-script" in uses and isinstance(with_block, dict):
                script = with_block.get("script")
                if script is not None:
                    bodies.append(
                        (job_id, name, script if isinstance(script, str) else repr(script))
                    )

    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        for job_id, job in jobs.items():
            if isinstance(job, dict):
                collect(str(job_id), job.get("steps"))

    runs = doc.get("runs")
    if isinstance(runs, dict):
        collect("<composite>", runs.get("steps"))

    return bodies


def mask_accepted(line: str) -> str:
    """The line with the accepted RUNNER_TEMP spellings blanked out.

    Blanked to spaces of the same width rather than deleted, so that removing
    one form can never join its neighbours into a `/tmp` that was not written
    (and so a column offset would still line up, should a caller ever want one).
    """
    masked = line
    for pattern in ACCEPTED_RUNNER_TEMP:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def scan_body(body: str) -> list[tuple[int, str]]:
    """(line number within the body, matched text) for each fixed temp path."""
    hits: list[tuple[int, str]] = []
    for offset, raw in enumerate(body.splitlines(), start=1):
        stripped = raw.strip()
        if stripped.startswith(COMMENT_PREFIXES):
            continue
        for match in FIXED_TMP_RE.finditer(mask_accepted(raw)):
            hits.append((offset, match.group(0)))
    return hits


def _body_line_offset(source_text: str, body: str) -> int:
    """Line number in the file where `body` starts, or 0 if it cannot be found.

    Best effort, for the error message only: YAML block scalars are re-indented
    by the loader, so the body's own text is matched a line at a time on its
    first non-empty line. A miss costs a line number, never a finding.
    """
    first = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    if not first:
        return 0
    for number, raw in enumerate(source_text.splitlines(), start=1):
        if raw.strip() == first:
            return number
    return 0


def scan_file(path: pathlib.Path) -> tuple[list[Finding], list[str]]:
    """Findings and hard errors for one workflow or action file."""
    # Repo-relative where possible; a fixture path from a test lives outside the
    # tree, and a crash there would make the fail-closed cases untestable.
    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [], [f"{rel}: could not be read ({exc}) — refusing to report it as clean"]
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [], [f"{rel}: could not be parsed ({exc}) — refusing to report it as clean"]
    if not isinstance(doc, dict):
        return [], [f"{rel}: did not parse to a mapping — refusing to report it as clean"]

    findings: list[Finding] = []
    for job_id, step_name, body in executable_bodies(doc):
        base = _body_line_offset(text, body)
        for offset, matched in scan_body(body):
            findings.append(
                Finding(rel, job_id, step_name, base + offset - 1 if base else offset, matched)
            )
    return findings, []


def scan_all() -> tuple[list[Finding], list[str], int]:
    paths = sorted(WORKFLOWS.glob("*.yml")) + sorted(ACTIONS.glob("*/action.yml"))
    findings: list[Finding] = []
    errors: list[str] = []
    for path in paths:
        file_findings, file_errors = scan_file(path)
        findings.extend(file_findings)
        errors.extend(file_errors)
    return findings, errors, len(paths)


def freeze_key(source: str) -> str:
    """The key a finding is frozen under.

    A **workflow** is keyed by basename, matching the freeze convention every
    sibling guard in `scripts/` already uses.

    A **composite action** cannot be, and this is the whole reason the function
    exists: all six of them are named `action.yml`, so a basename key names all
    six at once. One entry would silently excuse the other five, and an error
    line reading `action.yml: /tmp/x` would not say which action it meant.
    They are keyed by repo-relative path instead, which is unambiguous and is
    also what the finding already prints. Raised by Copilot on #1256.
    """
    normalized = source.replace("\\", "/")
    return normalized if normalized.endswith("/action.yml") else pathlib.Path(normalized).name


def current_map(findings: list[Finding]) -> dict[str, list[str]]:
    current: dict[str, list[str]] = {}
    for finding in findings:
        current.setdefault(freeze_key(finding.source), []).append(finding.text)
    return current


def compare(
    current: dict[str, list[str]],
    known: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    """Errors: unfrozen findings, and freeze entries that no longer describe the tree."""
    known = KNOWN_FIXED_TMP_PATHS if known is None else known
    errors: list[str] = []

    for name, texts in sorted(current.items()):
        allowed = set(known.get(name, ()))
        new = sorted(set(texts) - allowed)
        if new:
            errors.append(f"{name}: {', '.join(new)}")

    for name, texts in sorted(known.items()):
        # A slash means the key is a repo-relative composite-action path; a bare
        # name is a workflow basename. Both are resolved against the tree, so a
        # freeze entry naming a file that no longer exists is still an error.
        target = (REPO_ROOT / name) if "/" in name else (WORKFLOWS / name)
        if not target.is_file():
            errors.append(
                f"{name}: listed in KNOWN_FIXED_TMP_PATHS but no such file exists. "
                f"The freeze has stopped describing the tree."
            )
            continue
        gone = sorted(set(texts) - set(current.get(name, ())))
        if gone:
            errors.append(
                f"{name}: KNOWN_FIXED_TMP_PATHS still lists {', '.join(gone)}, which "
                f"is no longer in the file. Delete the stale entry — a freeze nobody "
                f"prunes stops being a list of known exceptions."
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    findings, hard_errors, scanned = scan_all()
    errors = hard_errors + compare(current_map(findings))

    if errors:
        print("::error::fixed /tmp path(s) in workflow bodies\n")
        for error in errors:
            print(f"  {error}")
        print()
        for finding in findings:
            print(f"  {finding}")
        print(
            "\nThe workflow-logic harness executes these bodies verbatim, so a fixed "
            "/tmp path is shared state between concurrent test processes — and the "
            "L191 treatment/control comparison in AGENTS.md runs two suites at once. "
            "The bare directory counts: `tmpd=/tmp` with `\"$tmpd/entries.json\"` "
            "downstream is the same collision, spelled without a trailing segment "
            "(#1260). "
            'Write under `tmpd="${RUNNER_TEMP:-/tmp}"` (bash) or '
            '`process.env.RUNNER_TEMP || "/tmp"` (github-script), which is what every '
            "runner already sets per job (#1247)."
        )
        return 1

    print(
        f"fixed temp paths OK: {scanned} workflow/action file(s) scanned; no "
        f"executable body names a fixed /tmp path. The freeze "
        f"(KNOWN_FIXED_TMP_PATHS) holds {len(KNOWN_FIXED_TMP_PATHS)} entr"
        f"{'y' if len(KNOWN_FIXED_TMP_PATHS) == 1 else 'ies'}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
