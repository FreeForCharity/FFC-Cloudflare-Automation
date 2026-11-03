#!/usr/bin/env python3
"""Measure the instruction corpus GitHub Copilot code review loads, against its budget.

Why this exists (#1696). AGENTS.md "Who merges" condition 2 requires a real
`<!-- ccr-overview-v2 -->` Copilot review on a PR's current head, so a PR the
reviewer refuses can never satisfy the merge condition. #1596 has now taken a
week of refusals while green, conflict-free and fully thread-resolved. The
instruction corpus is the one input to that reviewer's prompt this repository
controls, and both halves of it are append-only by explicit convention, so it
only ever grows -- silently, with no reading anywhere of how much of the
reviewer's window it already occupies.

What this measures, and what it deliberately does NOT claim:

  * It reports the corpus as a share of the reviewer's own recorded prompt
    budget. That is a FLOOR on the fixed overhead, not the whole of it: the same
    job log shows the prompt also carrying `Retrieved memory prompts (20
    memories)` and `[skills] catalogEntries=5`, neither of which lives in this
    repository.
  * It does NOT predict a refusal. #1696 falsified five per-PR size metrics in
    a row (instruction bytes, patch bytes, conversation bytes, changed-file
    content bytes, commit-message bytes); on every one of them the refused PR
    was smaller than a PR reviewed cleanly the same day. A guard claiming to
    predict would be wrong in the reassuring direction. Refusals are already
    occurring at today's share -- see REFUSALS_OBSERVED_AT, which the report
    prints beside the verdict so "OK" cannot be read as "review works".

The two numbers that are measured rather than chosen, both read directly from
the reviewer's own job log (`actions/jobs/120411610818/logs`, the 2026-10-23
12:28Z refusal on #1596, autofind 0.1.164), not from the refusal body:

  * `MAX_PROMPT_TOKENS = 110000`, which that log states as `MaxPromptTokens=110000`.
  * the loaded file set, which that log ENUMERATES:
        [customInstructions] Built 2 custom instruction(s) for
        FreeForCharity/FFC-Cloudflare-Automation; names=["AGENTS.md","CLAUDE.md"]

That enumeration is the whole reason this script exists in the shape it does.
The refusal body names the files to shorten as "`.github/copilot-instructions.md`,
`.github/instructions/**/*.instructions.md`, `AGENTS.md`, or `REVIEW.md`" -- and
does NOT mention `CLAUDE.md`, which is 61% of the corpus it just refused. Three
issues and #1696's own acceptance criteria took their file list from that
message. A guard built to it would measure 102 KB of a 261 KB corpus and report
headroom that does not exist, wrong in the reassuring direction from its first
commit. `CLAUDE.md` is pinned in the candidate set by test, for that reason.

A third log line is worth recording here because it contradicts the public body
outright: the internal error is `all files are too large to fit within the
prompt budget` with `configurationError: true`. The public review body blames
the custom instructions; the engine blames the files. Both budgets are the same
window, which is why trimming the corpus is still the lever -- but the body is
not evidence about which term overflowed.

Thresholds are POLICY KNOBS, not measurements, and are deliberately set above
today's reading so that crossing one is news:

  * ADVISORY_SHARE -- report WARN, exit 0. Growth worth a second look.
  * HARD_SHARE -- exit 1. The corpus has eaten so much of the window that the
    trim decision (#1696 AC1, a policy change reserved to @clarkemoyer) can no
    longer wait.

Enforcement reaches CI through `tests/workflow-logic/test_copilot_instruction_budget.py`,
which measures the live tree and fails over HARD_SHARE. That needs no workflow
edit: `run_all.py` picks the module up by name.

Usage:
    python3 scripts/check-copilot-instruction-budget.py
    python3 scripts/check-copilot-instruction-budget.py --json
    python3 scripts/check-copilot-instruction-budget.py --root /path/to/checkout

Exit codes: 0 within the thresholds (OK or WARN), 1 over the hard threshold,
2 the corpus could not be measured. The third one exists because the second
reading of this script found the one remaining fail-open: a `--root` that does
not exist, or one where the corpus has been renamed or moved, measured 0 B,
scored 0.0% and exited 0, so "I could not find the corpus" and "the corpus is
comfortably small" were the same output. That is the direction of error the
whole module is written against, so `main()` now refuses a root that is not a
directory and one where no candidate is present, and reports a `CorpusError`
from anywhere as exit 2 rather than letting it surface as a traceback whose
status is indistinguishable from OVER.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

# Read from the reviewer's job log; see the module docstring for provenance.
MAX_PROMPT_TOKENS = 110000

# The crude but conventional estimate. Named, and kept crude on purpose: a
# tokenizer dependency would make this guard unrunnable in the sandboxes that
# have to run it, and the question it answers ("how much of the window is gone
# before the PR is even read") does not turn on the third significant figure.
BYTES_PER_TOKEN = 4

# Literal paths. AGENTS.md and CLAUDE.md are MEASURED as loaded; the other two
# are documented by the refusal body and absent from this repository today, and
# are counted so that adding one is not a silent budget increase.
CANDIDATE_PATHS = (
    "AGENTS.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
    "REVIEW.md",
)

# Recursive, so a nested instruction file cannot hide from the total.
CANDIDATE_GLOBS = (".github/instructions/**/*.instructions.md",)

ADVISORY_SHARE = 0.60
HARD_SHARE = 0.70

# Printed beside every verdict. A share under the advisory line means the corpus
# has not grown, NOT that the reviewer is working.
REFUSALS_OBSERVED_AT = (
    "Copilot has refused PR #1596 at a corpus of 260,832 B (59.3% of budget) "
    "continuously since 2026-10-21; see #1696. A verdict of OK here means the "
    "corpus has not grown, not that review works."
)


class CorpusError(RuntimeError):
    """A candidate file exists and could not be measured.

    Fail-closed on purpose. Treating an unreadable instruction file as 0 bytes
    would under-report the corpus, which is the one direction of error this
    guard must not make.
    """


def discover(root: pathlib.Path) -> list[tuple[str, bool]]:
    """Every candidate instruction path, with whether it is present.

    Returns (relative path, present) pairs, sorted, with the literal candidates
    before the globbed ones so the report reads in a stable order regardless of
    what the filesystem hands back.
    """
    found: list[tuple[str, bool]] = [
        (name, (root / name).is_file()) for name in CANDIDATE_PATHS
    ]
    globbed: set[str] = set()
    for pattern in CANDIDATE_GLOBS:
        for hit in root.glob(pattern):
            if hit.is_file():
                globbed.add(hit.relative_to(root).as_posix())
    found.extend((name, True) for name in sorted(globbed))
    return found


def measure(
    root: pathlib.Path,
    budget_tokens: int = MAX_PROMPT_TOKENS,
    bytes_per_token: int = BYTES_PER_TOKEN,
) -> dict:
    """Total the corpus and express it as a share of the reviewer's budget.

    Sizes are read as BYTES. Reading these as text would put them through
    universal-newline translation, which this repository has already been
    burned by twice (CLAUDE.md's CR-counting notes): a CRLF checkout would
    under-report every file by one byte per line.
    """
    files = []
    total = 0
    for name, present in discover(root):
        size = None
        if present:
            try:
                size = len((root / name).read_bytes())
            except OSError as exc:
                raise CorpusError("cannot measure %s: %s" % (name, exc)) from exc
            total += size
        files.append({"path": name, "present": present, "bytes": size})

    est_tokens = total // bytes_per_token
    budget_bytes = budget_tokens * bytes_per_token
    share = est_tokens / budget_tokens if budget_tokens else 0.0
    for entry in files:
        entry["share_of_corpus"] = (
            entry["bytes"] / total if entry["bytes"] and total else 0.0
        )
    return {
        "files": files,
        "total_bytes": total,
        "estimated_tokens": est_tokens,
        "budget_tokens": budget_tokens,
        "budget_bytes": budget_bytes,
        "share_of_budget": share,
        "advisory_share": ADVISORY_SHARE,
        "hard_share": HARD_SHARE,
        "verdict": verdict(share),
        "refusals_observed_at": REFUSALS_OBSERVED_AT,
    }


def verdict(share: float) -> str:
    """OK below the advisory line, WARN between the two, OVER at or above hard."""
    if share >= HARD_SHARE:
        return "OVER"
    if share >= ADVISORY_SHARE:
        return "WARN"
    return "OK"


def render(report: dict) -> str:
    lines = ["Copilot code-review instruction corpus:", ""]
    for entry in report["files"]:
        if entry["present"]:
            lines.append(
                "  %-45s %9d B  %5.1f%% of corpus"
                % (entry["path"], entry["bytes"], 100 * entry["share_of_corpus"])
            )
        else:
            lines.append("  %-45s   (absent)" % entry["path"])
    lines.extend(
        [
            "",
            "  total                                         %9d B" % report["total_bytes"],
            "  estimated tokens (%d B/token)                  %9d"
            % (BYTES_PER_TOKEN, report["estimated_tokens"]),
            "  reviewer budget (MaxPromptTokens)             %9d tokens"
            % report["budget_tokens"],
            "  share of budget consumed before the PR is read  %5.1f%%"
            % (100 * report["share_of_budget"]),
            "  advisory / hard thresholds                      %5.1f%% / %5.1f%%"
            % (100 * report["advisory_share"], 100 * report["hard_share"]),
            "",
            "  VERDICT: %s" % report["verdict"],
            "  NOTE: %s" % report["refusals_observed_at"],
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        default=None,
        help="repository checkout to measure (default: this script's repository)",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    root = (
        pathlib.Path(args.root)
        if args.root
        else pathlib.Path(__file__).resolve().parents[1]
    )

    try:
        if not root.is_dir():
            raise CorpusError("not a directory: %s" % root)
        if not any(present for _, present in discover(root)):
            raise CorpusError(
                "no instruction file found under %s -- an empty corpus is "
                "'the corpus could not be found', never 'the corpus is small'"
                % root
            )
        report = measure(root)
    except CorpusError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report))
    return 1 if report["verdict"] == "OVER" else 0


if __name__ == "__main__":
    sys.exit(main())
