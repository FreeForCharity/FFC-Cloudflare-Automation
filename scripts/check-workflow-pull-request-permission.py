#!/usr/bin/env python3
"""Guard: a body that READS pull requests needs `pull-requests:` in its job's
effective `permissions:` (#1417).

747's `merge-silence` and `open-pr-cap` signals read pull requests out of
`github.rest.issues.listForRepo` -- issues and PRs are one object in the REST
model, told apart only by a `pull_request` key. 747 declared
`contents: read` + `issues: write` and nothing else. An explicit `permissions:`
block sets every scope it does not name to `none`, so the token could not see
pull requests and that listing returned ISSUES ONLY.

WHY IT IS SILENT, WHICH IS THE WHOLE REASON THIS IS A GUARD
    Nothing throws. The call succeeds, the PRs are simply absent, and a count
    taken from a filtered listing is a NUMBER -- so `open-pr-cap` reported
    `OK -- 0 open` while three PRs were open, and `merge-silence` found no
    `merged_at` and reported UNKNOWN. Two signals, one empty result, two
    different verdicts, neither of them right. That is ledger L02's fail-open
    ("could not check" rendered as "checked, fine") inside the workflow whose
    own header declares closing L02 its purpose.

    Measured at the `2026-09-29T04:34:31Z` tick, where ground truth was 3 open
    and 3 merged inside 3h. The differential is what isolates the cause without
    re-reading a token: all three reads used the same token on the same tick,
    `conductor-silence` (issue comments) was CORRECT, and only the two reads
    whose results are pull requests came back empty.

WHAT COUNTS AS A VIOLATION
    A POSITIVE read of pull requests in code a runner executes -- a step's
    `run:` body or an `actions/github-script` step's `script:` -- inside a job
    whose effective `permissions:` block omits `pull-requests`. Two spellings:

      * the PR API itself: `github.rest.pulls.*`, `github.paginate(github.rest.pulls.*)`
      * depending on the `pull_request` key of a listing item being PRESENT:
        `item.pull_request && item.pull_request.merged_at`, `if (i.pull_request)`,
        `.filter((i) => i.pull_request)`

    A NEGATIVE use is NOT a violation, and conflating the two is how this guard
    would earn being switched off (#1019). `!i.pull_request` EXCLUDES pull
    requests from an issues listing -- 228, 737, 740 and 113 all do this to find
    a marker-bearing issue. Those bodies want issues only; a token that cannot
    see PRs gives them exactly what they asked for, so the missing permission
    makes them MORE correct, not less. The boundary is the `!`, and it is
    asserted by a test rather than by inspection.

    THE EVENT PAYLOAD IS NOT AN API READ. `github.event.pull_request` and
    `context.payload.pull_request` are delivered with the event, cost no
    permission, and appear in 722, 727 and 737. Flagging them would report every
    `on: pull_request` workflow in the tree.

    EFFECTIVE, NOT TOP-LEVEL. A job-level `permissions:` REPLACES the top-level
    block rather than merging with it, so the job's own block is the one that
    decides. 737 is the case that makes this load-bearing: its top level is
    `contents: read` alone, and it is CORRECT because both of its jobs declare
    `pull-requests:` themselves. A guard reading only the top level would report
    737 and teach its reader that the report is noise.

    NO BLOCK AT ALL IS OUT OF SCOPE, and that is a deliberate limit. A workflow
    with no `permissions:` anywhere gets the repository's default token scopes,
    which may well include `pull-requests`. This guard cannot see that setting
    and does not guess at it -- it reports the case it can prove, which is an
    explicit block that names other scopes and omits this one. 722 is such a
    workflow and is not a finding.

FAIL CLOSED
    A workflow that will not parse, a `permissions:` that is not a mapping or
    the bare `read-all`/`write-all` string, a `run:`/`script:` that is not a
    string, and a stale freeze entry are each REPORTED. A guard that goes quiet
    on what it does not understand reads as a pass.

THE FREEZE HOLDS ONE ENTRY, AND IT IS A QUESTION RATHER THAN AN ALLOWANCE
    747 was the instance with a MEASURED consequence, and it is fixed here. 737
    and 739 both already declared the scope with a rationale comment at their own
    block, so the convention existed in two places and 747 diverged from it.

    113's `resolve` job is a second instance of the same class whose consequence
    is NOT established, and it is frozen rather than fixed or hidden. It calls
    `issues.get` on a single dispatch-supplied number and reads
    `if (target.pull_request)` to detect "this number is a PR, so skip the
    post-back" -- guarding, by its own comment, against turning a PAID domain
    purchase red after the money is spent. Without the scope there are two
    possible behaviours and they differ in whether that guard still works:

      * `issues.get` refuses the PR number, the `catch` fires, `issueNumber` is
        cleared, and the post-back is skipped -- the SAME outcome as the positive
        branch, so the missing scope costs nothing; or
      * it returns the object with `pull_request` absent, the check reads false,
        and the run tries to comment on a PR anyway -- the exact failure the
        comment says it exists to prevent, now silent.

    Which one happens is a fact about the API under a filtered token, and it was
    not measured. Freezing it says so out loud. It is NOT narrowed out of the
    rule, because a guard tuned until it reports nothing it cannot explain is the
    #1019 shape pointed the other way; and it is NOT "fixed" by adding a scope to
    a paid-purchase path on an unverified model of the failure. The remedy is one
    live measurement, tracked on #1417.

    Every other job in the tree is clean, so this fails on ANY new instance.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# The scope that makes pull requests visible to the ambient GITHUB_TOKEN.
PR_SCOPE = "pull-requests"

# ONE entry, and it is a reasoned exception rather than a tuning -- see THE
# FREEZE HOLDS ONE ENTRY in the docstring. Keys are workflow basenames, values
# the job ids excused within them.
KNOWN_MISSING_PR_PERMISSION: dict[str, tuple[str, ...]] = {
    "113-cloudflare-domain-register.yml": ("resolve",),
}

# Direct use of the pull-request REST namespace. `rest.pulls.` covers
# `github.rest.pulls.list(...)` and a `github.paginate(github.rest.pulls.list, ...)`
# alike, because both name the namespace at the call site.
PULLS_API_RE = re.compile(r"\brest\.pulls\.[A-Za-z_$][\w$]*")

# A `.pull_request` property access. The BASE is not matched forwards, because a
# base can carry an index -- `o.data[0].pull_request` is a real spelling and a
# dotted-identifier pattern cannot cross the `[0]`, so it silently matched
# nothing. Found by this module's own positive case. Instead the occurrence is
# found first and the expression to its left is walked backwards by
# `expression_before`, which handles indexing, and whose result is then tested
# for a leading `!`.
PR_KEY_RE = re.compile(r"\.pull_request\b")

# Characters that can appear inside the expression a `.pull_request` hangs off.
EXPR_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$.[]'\"")

# Bases whose `pull_request` arrives with the webhook event and costs no
# permission. Matched against the END of the base so `context.payload` catches a
# destructured alias only when it is spelled out.
EVENT_PAYLOAD_BASES = ("github.event", "context.payload", "event", "payload")

# A `#`- or `//`-led line is prose and executes nothing. #1019 records four
# guards in 48h that flagged text ABOUT the thing they catch -- and this file's
# own CI step, plus 747's new rationale comment, both necessarily name
# `pull_request`. By leading token only: a trailing comment on a line of code is
# still scanned, which over-reports rather than under-reports.
COMMENT_PREFIXES = ("#", "//")


def strip_comments(body: str) -> str:
    """Drop whole-line comments, keeping line count so reports stay locatable."""
    out = []
    for line in body.splitlines():
        out.append("" if line.lstrip().startswith(COMMENT_PREFIXES) else line)
    return "\n".join(out)


def expression_before(text: str, end: int) -> tuple[str, bool]:
    """Walk backwards from `end` over one expression; return it and whether `!` leads.

    `end` is the index just past the expression, i.e. the position of the `.` in
    `.pull_request`. Indexing is included deliberately, so `o.data[0]` is one
    expression rather than a bare `data` with a stray subscript.
    """
    start = end
    while start > 0 and text[start - 1] in EXPR_CHARS:
        start -= 1
    expr = text[start:end]
    probe = start - 1
    while probe >= 0 and text[probe] in " \t(":
        probe -= 1
    return expr, probe >= 0 and text[probe] == "!"


def positive_pr_reads(body: str) -> list[str]:
    """Return the positive pull-request reads in `body`, as reportable snippets.

    A `.pull_request` occurrence is NEGATIVE -- and therefore not a read -- when
    a `!` immediately precedes its base identifier. That is the `!i.pull_request`
    exclusion filter, which wants issues only.
    """
    text = strip_comments(body)
    hits: list[str] = []

    for match in PULLS_API_RE.finditer(text):
        hits.append(match.group(0))

    for match in PR_KEY_RE.finditer(text):
        base, negated = expression_before(text, match.start())
        if negated or not base:
            continue
        if base in EVENT_PAYLOAD_BASES or base.endswith(
            tuple(f".{b}" for b in EVENT_PAYLOAD_BASES)
        ):
            continue
        hits.append(f"{base}.pull_request")

    return hits


def permission_names(block: object, where: str) -> tuple[set[str] | None, list[str]]:
    """Normalise a `permissions:` value to the set of scopes it NAMES.

    Returns `(None, errors)` when there is no usable explicit block -- either
    because none was declared (out of scope) or because it could not be read
    (reported). The bare `read-all` / `write-all` strings are reported rather
    than interpreted: they do grant the scope, but treating an unparsed string
    as a grant is how a typo becomes a silent pass.
    """
    if block is None:
        return None, []
    if isinstance(block, str):
        if block in ("read-all", "write-all"):
            return {PR_SCOPE}, []
        return None, [f"{where}: `permissions: {block}` is not a recognised form."]
    if not isinstance(block, dict):
        return None, [
            f"{where}: `permissions:` is {type(block).__name__}, not a mapping."
        ]
    return {str(k) for k in block}, []


def step_bodies(job: dict, where: str) -> tuple[list[str], list[str]]:
    """Executable bodies of a job's steps: `run:` and github-script `script:`."""
    bodies: list[str] = []
    errors: list[str] = []
    steps = job.get("steps")
    if steps is None:
        return bodies, errors
    if not isinstance(steps, list):
        return bodies, [f"{where}: `steps:` is not a list."]

    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"{where}: step {index} is not a mapping.")
            continue
        run = step.get("run")
        if run is not None:
            if isinstance(run, str):
                bodies.append(run)
            else:
                errors.append(f"{where}: step {index} `run:` is not a string.")
        uses = step.get("uses")
        if isinstance(uses, str) and "actions/github-script" in uses:
            script = (step.get("with") or {}).get("script")
            if script is None:
                errors.append(
                    f"{where}: step {index} uses github-script with no `script:`."
                )
            elif isinstance(script, str):
                bodies.append(script)
            else:
                errors.append(f"{where}: step {index} `script:` is not a string.")
    return bodies, errors


def scan_all() -> tuple[list[str], list[str], int]:
    """Findings, hard errors, and the number of workflow files scanned."""
    findings: list[str] = []
    hard_errors: list[str] = []
    scanned = 0

    for path in sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml")):
        scanned += 1
        name = path.name
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            hard_errors.append(f"{name}: will not parse as YAML ({exc}).")
            continue
        if not isinstance(doc, dict):
            hard_errors.append(f"{name}: top level is not a mapping.")
            continue

        top_names, errors = permission_names(doc.get("permissions"), name)
        hard_errors.extend(errors)

        jobs = doc.get("jobs")
        if jobs is None:
            continue
        if not isinstance(jobs, dict):
            hard_errors.append(f"{name}: `jobs:` is not a mapping.")
            continue

        for job_id, job in jobs.items():
            where = f"{name}:{job_id}"
            if not isinstance(job, dict):
                hard_errors.append(f"{where}: job is not a mapping.")
                continue

            bodies, errors = step_bodies(job, where)
            hard_errors.extend(errors)

            reads: list[str] = []
            for body in bodies:
                reads.extend(positive_pr_reads(body))
            if not reads:
                continue

            # A job-level block REPLACES the top-level one; only fall back to the
            # top level when the job declares none of its own.
            job_names, errors = permission_names(job.get("permissions"), where)
            hard_errors.extend(errors)
            effective = job_names if job_names is not None else top_names

            if effective is None:
                # No explicit block anywhere -- repository default, unknowable
                # here. Out of scope by design (see the docstring).
                continue
            if PR_SCOPE in effective:
                continue

            snippets = ", ".join(sorted(set(reads))[:4])
            findings.append(
                f"{where}: reads pull requests ({snippets}) but its effective "
                f"`permissions:` names {sorted(effective)} and omits "
                f"`{PR_SCOPE}`. That listing will return issues only, and it "
                f"will not raise."
            )

    return findings, hard_errors, scanned


def current_map(findings: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for finding in findings:
        where = finding.split(":", 2)
        out.setdefault(where[0], []).append(where[1])
    return out


def compare(current: dict[str, list[str]]) -> list[str]:
    """Report anything in the freeze that has stopped describing the tree."""
    errors: list[str] = []
    for name, job_ids in KNOWN_MISSING_PR_PERMISSION.items():
        if not (WORKFLOWS / name).is_file():
            errors.append(
                f"{name}: listed in KNOWN_MISSING_PR_PERMISSION but no such "
                f"workflow exists. The freeze has stopped describing the tree."
            )
            continue
        gone = sorted(set(job_ids) - set(current.get(name, ())))
        if gone:
            errors.append(
                f"{name}: KNOWN_MISSING_PR_PERMISSION still lists job(s) "
                f"{', '.join(gone)}, which no longer read pull requests without "
                f"the scope. Delete the stale entry — a freeze nobody prunes "
                f"stops being a list of known exceptions."
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    findings, hard_errors, scanned = scan_all()

    excused = {
        f"{name}:{job_id}"
        for name, job_ids in KNOWN_MISSING_PR_PERMISSION.items()
        for job_id in job_ids
    }
    live = [f for f in findings if f.split(": ", 1)[0] not in excused]
    errors = hard_errors + compare(current_map(findings))

    if live or errors:
        print("::error::pull-request read without `pull-requests:` permission\n")
        for error in errors:
            print(f"  {error}")
        for finding in live:
            print(f"  {finding}")
        print(
            "\nIssues and pull requests are one object in the REST model, so "
            "`issues.listForRepo` returns both — and an explicit `permissions:` "
            "block sets every scope it does not name to `none`. Without "
            f"`{PR_SCOPE}: read` the token cannot see pull requests, the listing "
            "returns issues only, and NOTHING RAISES: a count taken from it is a "
            "number, so blindness renders as a healthy zero (ledger L02). "
            "Measured on 747, where `open-pr-cap` reported `0 open` against a "
            "ground truth of 3 (#1417). Add the scope to the job's own block, "
            "with a note saying which read needs it — 737 and 739 both do."
        )
        return 1

    print(
        f"pull-request permission OK: {scanned} workflow file(s) scanned; every "
        f"job that reads pull requests declares `{PR_SCOPE}` in its effective "
        f"`permissions:`. The freeze (KNOWN_MISSING_PR_PERMISSION) holds "
        f"{len(KNOWN_MISSING_PR_PERMISSION)} entr"
        f"{'y' if len(KNOWN_MISSING_PR_PERMISSION) == 1 else 'ies'}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
