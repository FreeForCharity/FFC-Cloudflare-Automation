#!/usr/bin/env python3
"""PostToolUse hook for Bash: the secret backstop on the path the agent uses (#1292).

WHAT THIS CLOSES
    `post_edit.py` re-scans a file after Edit / Write / MultiEdit. It reads
    `tool_input.file_path`, and a Bash tool call has no such field, so for every
    file a shell command writes that hook exits 0 having scanned nothing. The
    standing operating instruction for these sessions is to prefer Bash for file
    work -- `sed`, heredocs, short scripts -- which makes the UNCOVERED path the
    normal one and the Edit path the exception.

    `guard_bash.py` scans the COMMAND TEXT before execution, so a secret typed
    into a heredoc is already blocked there, and this hook does not claim
    otherwise. Where that coverage stops is content which is not literal in the
    command:

      * a redirect that captures output -- `az keyvault secret show ... > f`,
        `curl ... -o f`, `gh api ... > f`;
      * a script the command invokes, which writes content the command string
        never contains;
      * a generated or templated file whose secret-shaped value is assembled at
        run time.

    In each of those the pre-execution scan sees a clean command and no
    post-write scan runs at all. This hook runs one.

WHAT IT SCANS, AND WHY NOT MORE
    A Bash tool call names no path, so the written set has to be recovered. The
    tractable recovery is `git status --porcelain`: modified + added +
    untracked, in the clone this hook lives in AND in the repository the command
    ran in. Those two differ for a Conductor that `cd`s into a clone, and for a
    multi-repo worker whose five clones sit side by side, so reading only the
    first would miss the sibling a command just wrote to.

    Deleted entries are skipped -- there is nothing left to read, and counting
    them against the caps would spend budget on nothing.

    A file written outside every repository read above is NOT scanned. That is a
    real gap, and the hook names it rather than staying quiet: write targets the
    command states explicitly (`>`, `>>`, `-o`, `--output`) which resolve
    outside those roots are reported once per session per path.

COST
    This runs after every Bash command, so the budget is stated and enforced,
    and whatever the caps exclude is reported rather than dropped:

      * MAX_FILES       files scanned per invocation
      * MAX_FILE_BYTES  bytes read per file
      * MAX_TOTAL_BYTES bytes read per invocation

    The caps alone would not make this cheap. `docs/lessons-ledger.md` is
    ~1.8 MiB and a full `find_secrets` pass over it costs ~480 ms measured on
    this repo -- and it is dirty for most of the life of a typical branch, so a
    cap-only design charges half a second to every command in the session. The
    freshness filter is what keeps the common case cheap: a dirty file is
    re-scanned only while its mtime is at or after the watermark this hook wrote
    on its previous run in the same session. The ledger is therefore scanned
    when it is written and not on each of the next fifty commands. The first run
    of a session has no watermark and scans the whole dirty set under the caps.

    Consequence worth knowing rather than discovering: a finding is reported
    once per write, exactly as `post_edit.py` reports once per edit -- not once
    per command for the rest of the session.

CONTRACT
    A finding names the file and the pattern on stderr and exits 2, the
    convention `post_edit.py` already uses; this does not invent a second
    severity. Everything else exits 0, advisories on stdout. Like every hook
    here it fails OPEN: any unexpected error exits 0 rather than blocking work,
    because a bug in a backstop must not be able to stop the session.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

HOOK_DIR = os.path.dirname(os.path.abspath(__file__))
# .claude/hooks/post_bash.py -> .claude/hooks -> .claude -> repo root
OWN_REPO = os.path.dirname(os.path.dirname(HOOK_DIR))

# --- Caps. Stated here because the hook runs after every Bash command. -------
MAX_FILES = 64
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
# How many out-of-clone paths one session's state file remembers, so the
# advisory is not repeated and the file cannot grow without bound.
MAX_NOTED_PATHS = 200
# Recorded watermark is backdated by this much. mtime granularity and the gap
# between a write and this hook running are both real, and re-scanning one
# extra second of writes is cheaper than missing one.
WATERMARK_SLACK_SECONDS = 1.0
# How many entries of each advisory kind to name before summarising.
MAX_LISTED = 5

_RENAME_ARROW = " -> "


def _unquote(path):
    """Undo git's C-style quoting of an unusual path name.

    git quotes a path containing a space, a quote or a non-ASCII byte:
    `?? "a b.txt"`, `?? "caf\\303\\251.md"`. The octal escapes are UTF-8 bytes,
    so the round trip is unicode_escape (which yields one char per byte) and
    then latin-1 -> utf-8. Fails open to the raw inner text: a mangled name
    costs one unreadable file, while raising here would retire the whole scan.
    """
    if len(path) >= 2 and path.startswith('"') and path.endswith('"'):
        inner = path[1:-1]
        try:
            return inner.encode("ascii", "backslashreplace").decode(
                "unicode_escape"
            ).encode("latin-1").decode("utf-8")
        except Exception:
            return inner
    return path


def parse_status(porcelain):
    """Repo-relative paths with content to re-scan, from `git status --porcelain`.

    Pure over the text, so the parse is testable without building a repository
    -- the same reason `post_edit.ledger_citing_rows` is pure over the ledger.

    The format is `XY<space>path`, X the index status and Y the work-tree one.
    Three things a split on whitespace gets wrong:

      * a rename or copy prints `R  old -> new`, and the file to scan is `new`;
      * an unusual path is C-quoted (see `_unquote`);
      * `D` on either side means the file is gone, and `!!` is an ignored entry
        nobody asked about.
    """
    paths = []
    for line in (porcelain or "").splitlines():
        if len(line) < 4 or line[2] != " ":
            continue
        x, y, rest = line[0], line[1], line[3:]
        if x == "D" or y == "D" or (x == "!" and y == "!"):
            continue
        if _RENAME_ARROW in rest and (x in "RC" or y in "RC"):
            rest = rest.split(_RENAME_ARROW, 1)[1]
        rest = _unquote(rest.strip())
        if rest and rest not in paths:
            paths.append(rest)
    return paths


def _git(args, cwd):
    """Run git with an explicit codec (#962) and a bounded wait."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )


def toplevel(cwd):
    """The work-tree root containing `cwd`, or None."""
    if not cwd or not os.path.isdir(cwd):
        return None
    try:
        proc = _git(["rev-parse", "--show-toplevel"], cwd)
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    out = (proc.stdout or "").strip()
    return os.path.abspath(out) if out else None


def scan_roots(cwd, own_repo=None):
    """The repositories to scan: this clone, plus the one the command ran in."""
    roots = []
    for candidate in (own_repo if own_repo is not None else OWN_REPO, toplevel(cwd)):
        if not candidate:
            continue
        norm = os.path.abspath(candidate)
        if norm not in roots:
            roots.append(norm)
    return roots


def changed_paths(root):
    """Repo-relative changed paths in `root`. Fails open to an empty list."""
    try:
        proc = _git(["status", "--porcelain"], root)
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return parse_status(proc.stdout)


# --- Per-session freshness state --------------------------------------------


def state_file(session_id, tmpdir=None):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "nosession"))[:80]
    return os.path.join(tmpdir or tempfile.gettempdir(), "ffc-post-bash-" + safe + ".json")


def load_state(path):
    """(watermark, noted_paths). Any problem reads as "no state".

    Fails toward MORE scanning on purpose: the opposite default would let an
    unreadable or truncated temp file silently retire the backstop, which is
    the failure shape this whole issue is about.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return 0.0, []
        watermark = float(data.get("watermark") or 0.0)
        noted = [p for p in (data.get("noted") or []) if isinstance(p, str)]
        return watermark, noted
    except Exception:
        return 0.0, []


def save_state(path, watermark, noted):
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(
                {"watermark": watermark, "noted": noted[-MAX_NOTED_PATHS:]}, fh
            )
    except Exception:
        pass


def fresh(root, rel_paths, watermark, getmtime=os.path.getmtime):
    """Paths whose mtime is at or after `watermark`; all of them when it is 0.

    An unreadable mtime scans the file rather than skipping it. A path that
    cannot be stat'd is the one a scan should look at, not the one it should
    assume is old.
    """
    if not watermark:
        return list(rel_paths)
    out = []
    for rel in rel_paths:
        try:
            if getmtime(os.path.join(root, rel)) >= watermark:
                out.append(rel)
        except Exception:
            out.append(rel)
    return out


# --- Scanning ---------------------------------------------------------------

SKIP_BINARY = "binary"
READ_PARTIAL = "truncated"


def scan_file(abs_path, max_bytes=MAX_FILE_BYTES):
    """(findings, bytes_read, note) for one file. Never raises.

    Read in BINARY and decode here rather than opening in text mode: a file a
    shell command produced can hold any bytes, and the point is to look at what
    landed on disk rather than at what a codec was willing to return. A file
    with NUL bytes in its head is treated as binary and skipped -- the repo
    tracks ~6 MiB of webfonts and minified bundles, and spending the byte budget
    on them would starve the text file that actually changed.
    """
    try:
        with open(abs_path, "rb") as fh:
            chunk = fh.read(max_bytes + 1)
    except Exception:
        return [], 0, None
    note = READ_PARTIAL if len(chunk) > max_bytes else None
    chunk = chunk[:max_bytes]
    if b"\0" in chunk[:8192]:
        return [], len(chunk), SKIP_BINARY
    return common.find_secrets(chunk.decode("utf-8", "replace")), len(chunk), note


# --- Writes this hook cannot see --------------------------------------------

# Write targets a command states explicitly. Deliberately conservative: `>` /
# `>>` redirects and `-o` / `--output` only. It does not model `tee`, `cp`,
# `mv`, or what a script the command invokes writes, because an over-eager
# parse would name files the command never touched -- and an advisory that
# cries wolf is one the next agent learns to skip past. The cases it does catch
# are the ones #1292 names: output capture whose command text is clean.
_WRITE_TARGET = re.compile(
    r"""(?:
          \d?>>?
        | (?<![\w-])-o\s
        | (?<![\w-])--output[\s=]
    )
    \s*
    (?P<t> "[^"]+" | '[^']+' | [^\s;&|<>()]+ )
    """,
    re.VERBOSE,
)


def within(path, root):
    p = os.path.abspath(path).replace("\\", "/")
    r = os.path.abspath(root).replace("\\", "/").rstrip("/")
    return p == r or p.startswith(r + "/")


def uncovered_targets(command, cwd, roots):
    """Stated write targets that land outside every scanned root."""
    base = cwd if cwd and os.path.isdir(cwd) else (roots[0] if roots else os.getcwd())
    out = []
    for match in _WRITE_TARGET.finditer(command or ""):
        raw = match.group("t").strip().strip("\"'")
        if not raw or raw.startswith("&"):
            continue
        # A value the shell will expand or glob cannot be resolved from the
        # text, and guessing produces a confident wrong path.
        if any(ch in raw for ch in ("$", "`", "*", "?")):
            continue
        if raw.startswith("/dev/") or raw.startswith("/proc/"):
            continue
        abs_t = os.path.abspath(os.path.join(base, raw))
        if any(within(abs_t, root) for root in roots):
            continue
        if abs_t not in out:
            out.append(abs_t)
    return out


def advisories(capped, partial, uncovered):
    """The lines naming everything the scan did not fully cover."""
    notes = []
    for path in capped[:MAX_LISTED]:
        notes.append(
            f"NOT SCANNED (cap: {MAX_FILES} files / "
            f"{MAX_TOTAL_BYTES // (1024 * 1024)} MiB per command): {path}"
        )
    if len(capped) > MAX_LISTED:
        notes.append(f"NOT SCANNED: {len(capped) - MAX_LISTED} further changed file(s)")
    for path in partial[:MAX_LISTED]:
        notes.append(
            f"SCANNED ONLY THE FIRST {MAX_FILE_BYTES // (1024 * 1024)} MiB of: {path}"
        )
    if len(partial) > MAX_LISTED:
        notes.append(f"SCANNED ONLY IN PART: {len(partial) - MAX_LISTED} further file(s)")
    for path in uncovered[:MAX_LISTED]:
        notes.append(
            f"NOT COVERED (outside every repository this hook scans): {path}"
        )
    if len(uncovered) > MAX_LISTED:
        notes.append(f"NOT COVERED: {len(uncovered) - MAX_LISTED} further target(s)")
    return notes


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        sys.exit(0)

    # Wired with the `Bash` matcher, so this is belt-and-braces: a settings file
    # that widened the matcher should not have this hook scanning after an Edit,
    # which `post_edit.py` already covers precisely.
    tool = data.get("tool_name") or ""
    if tool and tool != "Bash":
        sys.exit(0)

    cwd = data.get("cwd") or ""
    command = (data.get("tool_input") or {}).get("command") or ""
    roots = scan_roots(cwd)
    if not roots:
        sys.exit(0)

    state = state_file(data.get("session_id"))
    watermark, noted = load_state(state)
    started = time.time()

    findings = []
    capped = []
    partial = []
    files_scanned = 0
    bytes_read = 0

    for root in roots:
        for rel in fresh(root, changed_paths(root), watermark):
            display = os.path.join(root, rel).replace("\\", "/")
            if files_scanned >= MAX_FILES or bytes_read >= MAX_TOTAL_BYTES:
                capped.append(display)
                continue
            hits, read, note = scan_file(os.path.join(root, rel))
            files_scanned += 1
            bytes_read += read
            if note == READ_PARTIAL:
                partial.append(display)
            if hits:
                findings.append((display, hits))

    uncovered = [p for p in uncovered_targets(command, cwd, roots) if p not in noted]
    save_state(state, started - WATERMARK_SLACK_SECONDS, noted + uncovered)

    notes = advisories(capped, partial, uncovered)

    if findings:
        lines = [
            "SECURITY: a file changed by this command appears to contain a secret. "
            "Remove it now and rotate the credential if it was real. "
            "See .github/agents/AI_AGENT_INSTRUCTIONS.md."
        ]
        for path, hits in findings[:MAX_LISTED * 2]:
            lines.append(f"  {path}: {', '.join(hits)}")
        if len(findings) > MAX_LISTED * 2:
            lines.append(f"  ... and {len(findings) - MAX_LISTED * 2} further file(s)")
        # The scope limits belong IN the finding: an agent told about one file
        # must not read that as "and nothing else was written".
        lines.extend("  " + note for note in notes)
        sys.stderr.write("\n".join(lines) + "\n")
        sys.exit(2)

    for note in notes:
        print(f"[security] {note}")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
