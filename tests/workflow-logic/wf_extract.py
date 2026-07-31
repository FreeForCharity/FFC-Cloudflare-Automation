"""Shared helpers: extract embedded step scripts from workflow YAML, and the
shell-shape rules that more than one module checks them against.

Tests run against the *actual* script text inside the workflow files, so
they can never drift from what ships — editing a workflow's embedded
logic without updating the tests fails CI, not production.
"""

from __future__ import annotations

import os
import pathlib
import re

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


# --- child process environments ---------------------------------------------
#
# A child environment is INHERITED and then layered on, never built from
# nothing. Handed a dict containing only the variables a test cares about,
# node aborts during startup on Windows — it cannot initialize without the
# platform's own variables (SYSTEMROOT above all) and dies before any test
# logic runs, so the module reports `harness crashed:` rather than a result
# (#943). 26 modules were unrunnable on a Windows host for this reason while
# staying green on Linux and in CI, which is why nothing reported it.
#
# The cause is the *minimal dict*, not the POSIX-looking `/usr/bin:/bin` that
# used to be written into it: #943 measured both, and rewriting PATH alone
# changed nothing while inheriting fixed every crash.
#
# PATH is assembled with os.pathsep and keeps the inherited PATH as its tail,
# so a harness directory still shadows the real `gh`/`curl`/`sleep` while the
# platform's own directories stay reachable. The hardcoded `/usr/bin:/bin`
# tails are dropped rather than translated: on POSIX they are already in the
# inherited PATH, and on Windows they were never anything but noise.
def child_env(*prepend_path: str | pathlib.Path, **overrides: str) -> dict[str, str]:
    """The parent environment, with `prepend_path` entries put in front of PATH
    and `overrides` applied on top.

    Callers that need a child NOT to see something (the harness shims, the
    developer's git config) must override the relevant variable explicitly —
    inheriting means an omission is no longer a guarantee. HARNESS_DIR is a
    test-local directory that is never on a real PATH, so simply not
    prepending it keeps it unreachable.
    """
    env = dict(os.environ)
    if prepend_path:
        env["PATH"] = os.pathsep.join([*(str(p) for p in prepend_path), env.get("PATH", "")])
    env.update(overrides)
    return env


# --- ledger L27: hashing a shell value is not hashing the file --------------
#
# `out=$(cmd)` strips every trailing newline, so a digest taken from the value is
# the content minus its final byte(s). 738 published a canonical hash nobody could
# reproduce with `sha256sum`, and its byte-identity audit reported a
# trailing-newline-only difference as MATCHING (#893).
#
# It lives HERE, not in a test module, because two modules check it — the
# tree-wide scan in `test_lessons_ledger.py` and the step-local guard in
# `test_738_fleet_smoke_drift.py`. They started as two regexes for one rule, and
# they promptly drifted: round 2 of #895 anchored the ledger copy and left 738's
# flagging `cat "$file" | sha256sum`, a false positive on correct code that was
# found only by a later review round. One rule, one implementation.
#
# Anchored on the EMITTING command, which is what separates the defect from code
# that looks like it. `cat "$file" | sha256sum` and `(cd d && cat f) | sha256sum`
# stream the file's real bytes and strip nothing; `printf '%s' "$out" | sha256sum`
# hashes a string that has already lost its trailing newlines. Only echo/printf
# turn a shell value into the bytes being hashed.
_HASH_CMDS = r"(?:sha256sum|sha1sum|md5sum|shasum|cksum)"

# Command substitutions are MASKED before matching: both halves of the pattern
# need "up to the pipe that feeds the hash" (`[^|]*`), and a substitution may hold
# pipes of its own — `echo "$(cat f | tr -d ' ')" | sha256sum` is the defect with
# an internal pipe. On a raw line `[^|]*` stops at that inner pipe and the whole
# spelling goes unseen.
#
# `(?!\()` excludes `$((…))`, which is ARITHMETIC expansion: it yields a number
# with no trailing newlines to strip, so masking it would flag
# `echo "$((n + 1))" | sha256sum` as a defect it is not. That is also exactly how
# bash disambiguates the two — a command substitution wrapping a subshell has to
# be written `$( (…) )`.
_SUBST_SPAN = re.compile(r"\$\((?!\()(?:[^()]|\([^()]*\))*\)")
_SUBST_MARK = "\x00SUBST\x00"  # cannot occur in a workflow line
_MARK_RE = re.escape(_SUBST_MARK)

# What "a shell value" is, defined ONCE and shared by both forms below. Spelling
# it separately in each was how the arithmetic carve-out ended up applying to the
# pipe form and not the here-string form — `echo "$((n + 1))" | sha256sum` passed
# while `sha256sum <<<"$((n + 1))"` was flagged, from one rule with two spellings
# of its central term (#895 round 6, reported openly and in the low-confidence
# block). Requiring a variable NAME (or the substitution marker) rather than a
# bare `$` is what excludes `$((…))` here, matching what masking does for the
# pipe form.
_VALUE = r"(?:\$\{?\w+\}?|" + _MARK_RE + r")"

# One pattern, not two: a variable and a masked substitution are the same defect
# in the same position.
_HASHED_EMIT = re.compile(r"(?:echo|printf)\b[^|]*" + _VALUE + r"\"?\s*\|\s*" + _HASH_CMDS)
# `sha256sum <<<"$out"` is the same defect wearing a here-string: the stripped
# value gets exactly one newline appended, which is right only by coincidence.
# The marker is in _VALUE, so masking cannot hide `sha256sum <<<"$(cmd)"`.
_HASHED_HERESTRING = re.compile(
    _HASH_CMDS + r"\s*(?:-\S+\s+)*<<<\s*\"?" + _VALUE
)
HASH_PATTERNS = (_HASHED_EMIT, _HASHED_HERESTRING)


def hashes_a_shell_value(line: str) -> re.Pattern | None:
    """The pattern flagging this line as ledger-L27 defective, or None.

    Substitutions are masked first, so pipes inside `$( )` cannot hide the pipe
    that feeds the hash.
    """
    masked = _SUBST_SPAN.sub(_SUBST_MARK, line)
    return next((p for p in HASH_PATTERNS if p.search(masked)), None)


def load_workflow(filename: str) -> dict:
    # encoding="utf-8" is required, not cosmetic. Workflow files contain ✓/❌/⚠️ and
    # em-dashes; without it Python on Windows decodes as cp1252 and raises
    # UnicodeDecodeError, which crashes the whole module before any test runs. The
    # crash prints a traceback rather than FAIL lines, so a harness that greps for
    # "FAIL" reports zero failures — a crashed suite looks exactly like a passing one.
    return yaml.safe_load((WORKFLOWS / filename).read_text(encoding="utf-8"))


def find_step(workflow: dict, job_id: str, name_substring: str) -> dict:
    jobs = workflow.get("jobs", {})
    if job_id not in jobs:
        raise KeyError(f"job '{job_id}' not found (have: {list(jobs)})")
    for step in jobs[job_id].get("steps", []):
        if name_substring.lower() in str(step.get("name", "")).lower():
            return step
    raise KeyError(f"no step matching '{name_substring}' in job '{job_id}'")


def step_run(workflow_file: str, job_id: str, name_substring: str) -> str:
    """The shell script body of a run: step."""
    step = find_step(load_workflow(workflow_file), job_id, name_substring)
    return step["run"]


def step_github_script(workflow_file: str, job_id: str, name_substring: str) -> str:
    """The JS body of an actions/github-script step."""
    step = find_step(load_workflow(workflow_file), job_id, name_substring)
    uses = step.get("uses", "")
    if "github-script" not in uses:
        raise ValueError(f"step is not a github-script step (uses: {uses})")
    return step["with"]["script"]
