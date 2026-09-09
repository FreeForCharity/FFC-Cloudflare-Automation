#!/usr/bin/env python3
"""Verify that a privileged session actually loads this repo's hooks (#1042, #1237).

THE DEFECT THIS EXISTS FOR
    Claude Code loads hooks from the **session's project root**. A sandboxed agent
    that clones this repo and opens *it* as its project root gets `.claude/settings.json`
    applied and `.claude/hooks/` run. Two populations do not, and they are the ones
    with authority:

      - the **Conductor**, whose project root is its own workspace and which merely
        `cd`s into a clone to work (#1042, ledger L218); and
      - a **multi-repo cloud worker**, which clones five FFC repos side by side and
        runs with its project root set to their *parent* (#1237). No repo's
        `.claude/` is the session config, so the hub's hooks are present, correct
        and inert -- for the very agents L218 called the protected class.

    The hub's settings spell every hook path as `$CLAUDE_PROJECT_DIR/.claude/hooks/...`,
    and `$CLAUDE_PROJECT_DIR` for either session is *not* the clone -- so the block
    never resolves and the session runs unguarded.

    The population is therefore **any session whose project root is not the repo**,
    not the Conductor alone. That correction is the whole of #1237: a finding closed
    with "a hook now holds this" is still open for whoever does the issue->PR work.

    Measured, not inferred (ledger L218, run 134): the Conductor piped a board audit
    into `tail` and read `$?` after the pipe, reported `rc=0` from `tail` while the
    audit had refused to run unauthenticated. Replaying that exact string into
    `guard_bash.py` blocks it, exit 2, naming L50 verbatim. The rule was there, was
    correct, and could not fire.

WHY A SCRIPT AND NOT A CHECKLIST LINE
    Because the failure is *silence*. An absent hook produces no error, no banner and
    no log line -- it produces a session that behaves exactly like one whose commands
    all happened to be allowed. Nothing distinguishes "guarded and clean" from
    "unguarded" without asking, which is the whole content of #1042 AC2.

WHAT IT CHECKS, AND WHY THE LAST ONE IS THE POINT
    1. The workspace has a settings file at all (`settings.json` /
       `settings.local.json` under `<workspace>/.claude/`).
    2. One of them carries a `hooks` block.
    3. Every hook command names a file that EXISTS once `$CLAUDE_PROJECT_DIR` is
       expanded the way the session would expand it -- against the workspace. This is
       the check that catches the actual #1042 defect: the hub's own settings.json
       copied verbatim into the workspace passes (1) and (2) and fails here.
    4. **A live two-sided probe of the guard the config points at.** The configured
       `PreToolUse`/`Bash` script is fed one command that MUST be blocked and one that
       MUST be allowed, and both verdicts must land.
    5. **That the workspace was stated rather than guessed** (#1237). Checks 1-4 are
       all relative to a directory, so they answer "is THIS tree wired" and never
       "is the running session wired". When the directory is merely the cwd, and the
       cwd happens to be a clone that ships `.claude/hooks/`, every check above passes
       *by construction* -- the verifier grades the config against itself.

    (5) is not pedantry about arguments; it is the #1237 false green, measured. Run
    from inside the hub clone with no arguments, this script used to print
    `HOOKS: wired ... exit 0` inside a five-repo cloud-worker session whose project
    root was `/home/user`, which has no `.claude` at all and loads nothing. The
    guard it probed was real and the probe was honest -- it was simply the guard of
    a session that was not running. A verifier written to make L218's false green
    impossible had its own, reached through its default argument. So an inferred
    workspace that ships the hooks under test is reported UNVERIFIED, never wired.

    (4) is not belt-and-braces. Config presence is not proof a hook works -- that is
    this repo's most-repeated lesson, and a verifier that stopped at (3) would be a
    fresh instance of it. The probe is deliberately **two-sided** (#1027): a guard
    stubbed to `sys.exit(0)` passes any check that only looks for a block it never
    sees, and a guard stubbed to `sys.exit(2)` "blocks" everything including work the
    routine needs. Only asserting both directions distinguishes a working guard from
    either stub.

WHAT IT DOES NOT CHECK
    It runs the resolved hook FILE with this interpreter, not the configured command
    string verbatim -- executing an arbitrary string out of a config file is not a
    thing a verifier should do. So a command that names a real guard but neutralises
    it by other means (a wrong interpreter, an added flag) resolves and probes clean.
    The gap is narrow and the alternative is worse.

USAGE
    # verify (read-only; this is what bootstrap runs)
    python3 scripts/verify-conductor-hooks.py --workspace /path/to/workspace
    python3 scripts/verify-conductor-hooks.py --workspace /path/to/ws --json

    # render the tracked template into the workspace, then verify
    python3 scripts/verify-conductor-hooks.py --render --workspace /path/to/workspace

    # a multi-repo cloud worker: the session root is the PARENT of the clones
    python3 FFC-Cloudflare-Automation/scripts/verify-conductor-hooks.py \
        --render --workspace /home/user --hub-clone /home/user/FFC-Cloudflare-Automation

    `--workspace` defaults to $CLAUDE_PROJECT_DIR, then to the current directory.
    Only the first two count as *stated*; the cwd fallback is a guess, and a guess
    that lands on a hook-shipping clone is refused rather than certified (see 5).
    `--hub-clone` defaults to this script's own repository root.

EXIT CODES
    0  wired -- settings resolve and the guard demonstrably blocks and allows
    1  not wired, the probe did not land both verdicts, or the workspace was
       inferred and could only certify itself (#1237)
    2  usage error (missing template, unreadable workspace)

    Non-zero is deliberately the same for "no config", "config present but the
    guard does not work" and "cannot tell". All three mean the same thing to the
    run about to start: you are not known to be guarded. Distinguishing them is
    the report's job, not the exit code's.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE_REL = pathlib.Path(".claude") / "conductor" / "settings.template.json"
PLACEHOLDER = "__HUB_CLONE__"

# Claude Code reads both, `settings.local.json` last (it overrides).
SETTINGS_NAMES = ("settings.json", "settings.local.json")

# How the workspace under test was chosen. Only the last is a guess, and only the
# last can be refused as self-certifying (#1237). Named constants rather than bare
# strings because these gate a safeguard: a typo'd literal at the comparison site
# reads as "not inferred" and silently restores the false green.
SOURCE_EXPLICIT = "explicit"  # --workspace was given
SOURCE_ENV = "env"  # $CLAUDE_PROJECT_DIR, which the SESSION sets
SOURCE_INFERRED = "inferred"  # the cwd fallback -- a guess
SOURCES = frozenset({SOURCE_EXPLICIT, SOURCE_ENV, SOURCE_INFERRED})

# One command that MUST be refused and one that MUST be permitted. The blocking
# case is L50 -- the exact shape that went unguarded in run 134 and produced
# L218 -- so this probe fails if the Conductor is ever re-wired to a guard that
# has lost the rule its own incident is named for.
PROBE_BLOCK = 'python3 scripts/audit-agentic-os-board.py | tail -45; echo "EXIT=$?"'
# Deliberately a command the routine issues constantly: a guard that refuses this
# blocks step 0 of every run, which is a failure mode as real as an absent guard.
PROBE_ALLOW = "git status --porcelain"

# git-bash hands out MSYS paths (`/c/Users/...`). Native Windows Python does not
# know the MSYS mount table, so it reads a leading `/c/` as DRIVE-RELATIVE and
# resolves `/c/Users/x` to `C:\\c\\Users\\x` -- a directory that does not exist.
# CLAUDE.md records this as its own trap ("Python on this host cannot open a
# git-bash /c/... path"), and the Conductor's shell is git-bash, so `$PWD` is
# exactly the value this script is most likely to be handed.
_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])(/.*)?$")


def from_msys_path(raw: str, *, windows: bool | None = None) -> str:
    """Translate a git-bash `/c/...` path to `C:/...`, on Windows only.

    Gated on the platform because the same string means two different things:
    `/c/data` is an ordinary absolute directory on Linux and an MSYS drive
    reference under git-bash. Rewriting it everywhere would corrupt a legitimate
    POSIX path, so the Linux branch is a real behaviour and has its own test, not
    an untested default.

    `windows` is a parameter rather than a read of `sys.platform` at the call
    site so both branches are exercisable from either host. This matters here
    more than usual: the defect is invisible on the platform CI runs on, and a
    Linux sandbox cannot otherwise test the branch that only ever executes on
    the Conductor's box -- the #944 asymmetry.

    Not verified end to end on Windows from this sandbox: the mapping below is
    unit-tested as a pure function, but the `pathlib` resolution it feeds has
    only been reasoned about, not measured, on win32.
    """
    if windows is None:
        windows = sys.platform == "win32"
    if not windows:
        return raw
    match = _MSYS_DRIVE_RE.match(raw)
    if not match:
        return raw
    drive, rest = match.group(1), match.group(2) or "/"
    return f"{drive.upper()}:{rest}"


_QUOTED_PY = re.compile(r"""["']([^"']+?\.py)["']""")
_BARE_PY = re.compile(r"""(?:[A-Za-z]:)?[^\s"';|&]+\.py""")


def ships_hooks(directory: pathlib.Path) -> bool:
    """True when `directory` carries hook SCRIPTS a settings block could resolve to.

    This is the self-certification test (#1237). A workspace that ships its own
    `.claude/hooks/` satisfies every path-resolution check no matter what the
    session's real project root is, so inferring such a directory and then
    reporting it wired proves only that the directory exists.

    The presence of at least one `.py` is the condition, not the presence of the
    directory. An EMPTY `.claude/hooks/` cannot certify anything -- nothing
    resolves to it -- so refusing on it was both inaccurate (the refusal says the
    workspace "ships the very `.claude/hooks/`" under test) and actively worse for
    the reader, because it suppressed the missing-path diagnostics that name
    exactly which hooks failed to resolve. Measured before this narrowing: an
    empty hooks dir reported `HOOKS: UNVERIFIED` instead of `NOT WIRED` with the
    unresolved paths listed.

    One `.py` is enough, deliberately. A partially-populated directory can still
    self-certify a settings block that happens to name the hook it does have, and
    "refuse" is the safe side of that line.
    """
    try:
        hooks = directory / ".claude" / "hooks"
        return hooks.is_dir() and any(hooks.glob("*.py"))
    except OSError:
        return False


def sibling_clones(workspace: pathlib.Path) -> list[str]:
    """Names of the git checkouts sitting beside `workspace`, sorted.

    Used only to make the #1237 refusal actionable: when a clone has siblings, the
    parent is almost certainly the session's project root, and naming it saves the
    reader working out what to pass to `--workspace`. Errors are swallowed to an
    empty list on purpose -- a hint that cannot be computed must not turn a
    verification into a crash.
    """
    try:
        parent = workspace.parent
        return sorted(
            d.name for d in parent.iterdir() if d.is_dir() and (d / ".git").exists()
        )
    except OSError:
        return []


def candidate_session_root(workspace: pathlib.Path) -> str | None:
    """The directory a multi-repo worker's project root probably is, or None.

    Fires on the shape #1237 measured: two or more sibling checkouts under a
    common parent. One checkout says nothing about where a session is rooted, so
    a lone clone yields None rather than a guess -- a wrong hint is worse than no
    hint, because it sends the reader to re-measure the wrong directory and get a
    confident answer about it.

    A parent that already carries `.claude` is still named, and an earlier draft
    of this had it backwards. That draft excluded such parents on the grounds that
    naming one was "advice to overwrite something" -- true of a `--render`
    suggestion, and this hint no longer feeds one. The refusal's remedy is a
    read-only re-measure (`--workspace <root>`), for which an already-configured
    parent is the *most* likely session root, not a directory to steer away from.
    Measured on this box: once `/home/user/.claude` existed, the exclusion made
    the remedy degrade to a `<session project root>` placeholder at exactly the
    moment it could have named the answer.
    """
    if len(sibling_clones(workspace)) < 2:
        return None
    return str(workspace.parent)


def _expand(raw: str, workspace: pathlib.Path) -> str:
    """Expand a hook command's path the way the SESSION would expand it.

    `$CLAUDE_PROJECT_DIR` is the session's project root, and for the Conductor
    that is the workspace -- never the clone. Expanding it against the clone here
    would make the hub's own unmodified settings.json look correctly wired, i.e.
    it would hide precisely the bug this script exists to find.
    """
    ws = str(workspace)
    for form in ("${CLAUDE_PROJECT_DIR}", "$CLAUDE_PROJECT_DIR"):
        raw = raw.replace(form, ws)
    return os.path.expanduser(raw)


def script_paths(command: str) -> list[str]:
    """Every `.py` path named by a hook command string.

    Quoted forms win outright: a Windows path with a space in it (`C:\\Program
    Files\\...`) is one argument when quoted and several tokens when not, and the
    bare fallback would report a fragment that does not exist -- a false finding
    against a correct config.
    """
    quoted = _QUOTED_PY.findall(command)
    if quoted:
        return quoted
    return _BARE_PY.findall(command)


def iter_hook_commands(settings: dict):
    """Yield (event, matcher, command) for every hook entry in a settings dict.

    Tolerant of shape on purpose: an unexpected fragment should be skipped, never
    crash the check. A verifier that dies on a malformed config tells the run
    nothing, and "the verifier crashed" is far too easy to read as "ran fine".
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            matcher = group.get("matcher", "")
            for hook in group.get("hooks") or []:
                if isinstance(hook, dict) and isinstance(hook.get("command"), str):
                    yield event, matcher, hook["command"]


def load_settings(workspace: pathlib.Path) -> tuple[dict, list[str]]:
    """Merged view of the workspace settings, plus the files that carried hooks.

    Merge is shallow and last-wins on `hooks`, which is how Claude Code layers
    `settings.local.json` over `settings.json`. Anything unreadable is reported
    as absent rather than raising -- see `iter_hook_commands`.
    """
    merged: dict = {}
    carriers: list[str] = []
    for name in SETTINGS_NAMES:
        path = workspace / ".claude" / name
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        merged.update(data)
        if isinstance(data.get("hooks"), dict) and data["hooks"]:
            carriers.append(name)
    return merged, carriers


def probe_guard(guard: pathlib.Path) -> list[dict]:
    """Run the configured Bash guard on one must-block and one must-allow command.

    `env=dict(os.environ)` and never a scrubbed dict: a minimal env breaks the
    child outright on Windows (CLAUDE.md), and a guard that cannot START exits
    non-zero exactly like a guard that caught you -- which would score the block
    case as a pass for the wrong reason. `encoding="utf-8"` plus a pinned
    `PYTHONIOENCODING` covers both halves of #945/#962.
    """
    results = []
    for command, want_block in ((PROBE_BLOCK, True), (PROBE_ALLOW, False)):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        try:
            proc = subprocess.run(
                [sys.executable, str(guard)],
                input=payload,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                timeout=60,
            )
            rc = proc.returncode
            # A guard reports "blocked" as exit 2. Any OTHER non-zero code is the
            # guard failing to run, and must not be counted as a detection -- that
            # is the false positive L203 names, and it points the flattering way.
            crashed = rc not in (0, 2)
            detail = "" if not crashed else (proc.stderr or proc.stdout or "").strip()[:300]
        except (OSError, subprocess.SubprocessError) as exc:
            rc, crashed, detail = None, True, f"{type(exc).__name__}: {exc}"
        blocked = rc == 2 and not crashed
        results.append(
            {
                "command": command,
                "expected": "block" if want_block else "allow",
                "returncode": rc,
                "ok": (not crashed) and (blocked == want_block),
                "crashed": crashed,
                "detail": detail,
            }
        )
    return results


def verify(workspace: pathlib.Path, *, source: str) -> dict:
    """Report on `workspace`. `source` says how that path was chosen (#1237).

    Required and keyword-only, with no default. A default of `SOURCE_EXPLICIT`
    would mean a caller who simply forgets the argument gets the most-trusting
    provenance and skips the refusal -- failing open, silently, which is the
    defect this function exists to prevent. Keyword-only additionally stops a
    positional call from binding the wrong string as provenance.

    `source` is a parameter and not a read of `sys.argv` because it changes the
    VERDICT, not just the wording: an inferred workspace that ships the hooks
    under test cannot be certified. Passing it in keeps both branches reachable
    from a test without reconstructing a command line.

    It is validated rather than trusted. A free-form string that gates a
    safeguard fails open on a typo -- `"inferrred"` would compare unequal to
    `SOURCE_INFERRED`, skip the refusal, and certify the very case this exists to
    catch, silently and in the reassuring direction. Raising is the fail-closed
    choice: an unknown provenance is not a provenance.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown workspace source {source!r}; expected one of {sorted(SOURCES)}")

    report: dict = {
        "workspace": str(workspace),
        "workspace_source": source,
        "self_certifying": False,
        # Filled in only on the refusal path below. It exists to make that one
        # message actionable, and computing it eagerly would charge every run a
        # directory scan of the parent for a hint nobody reads -- so `null` here
        # means "no hint was needed", not "no sibling clones exist".
        "candidate_session_root": None,
        "settings_files": [],
        "hooks_carriers": [],
        "missing_paths": [],
        "probes": [],
        "problems": [],
        "wired": False,
    }

    # Checked BEFORE reading any settings: the whole point is that the checks
    # below would all pass. Running them first and then withholding the verdict
    # would print a report indistinguishable from a real pass to anyone skimming.
    if source == SOURCE_INFERRED and ships_hooks(workspace):
        report["self_certifying"] = True
        hint = candidate_session_root(workspace)
        report["candidate_session_root"] = hint
        report["problems"].append(
            f"workspace was inferred from the current directory ({workspace}), and that "
            "directory ships the very `.claude/hooks/` this check would probe -- so it "
            "grades itself and says nothing about the session's real project root (#1237)"
            # Quoted for the same reason the printed remedy is: this path is read
            # to be retyped, and an unquoted spaced path gives no clue where it
            # ends. shlex.quote leaves an ordinary path bare, so this costs
            # nothing in the common case.
            + (
                f"; sibling checkouts suggest the session root is {shlex.quote(hint)}"
                if hint
                else ""
            )
        )
        return report

    report["settings_files"] = [
        n for n in SETTINGS_NAMES if (workspace / ".claude" / n).is_file()
    ]
    if not report["settings_files"]:
        report["problems"].append(
            f"no settings file under {workspace / '.claude'} "
            f"(looked for {', '.join(SETTINGS_NAMES)})"
        )
        return report

    settings, carriers = load_settings(workspace)
    report["hooks_carriers"] = carriers
    commands = list(iter_hook_commands(settings))
    if not commands:
        report["problems"].append(
            "settings present but no `hooks` block -- this is the exact state "
            "L218 describes: a permissions allowlist and no guards"
        )
        return report

    guard_bash = None
    for event, matcher, command in commands:
        for raw in script_paths(command):
            resolved = pathlib.Path(_expand(raw, workspace))
            # A path that is still relative after expansion must resolve against
            # the WORKSPACE, never the cwd the verifier happens to be invoked
            # from. Measured before this line existed: a hook naming a bare
            # `guard_rel.py`, with that file present only in the caller's cwd
            # and absent from the workspace entirely, reported `missing_paths:
            # []` and `wired: True` -- the verifier validated a guard that had
            # nothing to do with the session it was clearing. That is the L218
            # false green this script exists to make impossible.
            if not resolved.is_absolute():
                resolved = workspace / resolved
            if not resolved.is_file():
                report["missing_paths"].append(
                    {"event": event, "matcher": matcher, "path": str(resolved)}
                )
                continue
            if event == "PreToolUse" and "Bash" in (matcher or "") and guard_bash is None:
                guard_bash = resolved

    if report["missing_paths"]:
        report["problems"].append(
            f"{len(report['missing_paths'])} hook path(s) do not exist after expanding "
            "$CLAUDE_PROJECT_DIR against the workspace -- a config copied from the hub "
            "resolves against the wrong root"
        )
        return report

    if guard_bash is None:
        report["problems"].append("no PreToolUse hook matching Bash -- the bash guard is not wired")
        return report

    report["guard_bash"] = str(guard_bash)
    report["probes"] = probe_guard(guard_bash)
    for p in report["probes"]:
        if p["ok"]:
            continue
        if p["crashed"]:
            report["problems"].append(
                f"guard did not run for the {p['expected']} case "
                f"(rc={p['returncode']}): {p['detail']}"
            )
        else:
            report["problems"].append(
                f"guard was expected to {p['expected']} `{p['command']}` "
                f"but returned rc={p['returncode']}"
            )

    report["wired"] = not report["problems"]
    return report


def start_line(report: dict) -> str:
    """The one line a run's START comment carries (#1042 AC2).

    Stated in both directions and always printed. A verifier that only speaks up
    on failure leaves "guarded" and "the check did not run" identical in the
    record, which is L218 one level up.
    """
    if report["wired"]:
        return (
            f"HOOKS: wired -- {report.get('guard_bash')} blocks the L50 shape and "
            f"allows `{PROBE_ALLOW}` (verify-conductor-hooks.py, exit 0)"
        )
    # A third wording, not a third exit code. "I could not tell" and "it is not
    # wired" are the same instruction to the run about to start, but collapsing
    # them in the RECORD is what #1237 is about: the reader needs to know whether
    # a directory was measured or a question was dodged.
    if report.get("self_certifying"):
        return (
            "HOOKS: UNVERIFIED -- workspace was inferred, not stated, and it ships the "
            "hooks under test, so the check could only certify itself (#1237). Treat this "
            "run as unguarded until re-run with an explicit --workspace. "
        ) + "; ".join(report["problems"])
    return "HOOKS: NOT WIRED -- this run is unguarded (#1042). " + "; ".join(report["problems"])


def render(workspace: pathlib.Path, hub_clone: pathlib.Path, force: bool) -> int:
    template = REPO_ROOT / TEMPLATE_REL
    if not template.is_file():
        print(f"error: template not found at {template}", file=sys.stderr)
        return 2
    target = workspace / ".claude" / "settings.json"
    if target.exists() and not force:
        print(
            f"error: {target} already exists; pass --force to overwrite "
            "(check first that nothing hand-written lives in it)",
            file=sys.stderr,
        )
        return 2
    body = template.read_text(encoding="utf-8").replace(PLACEHOLDER, hub_clone.as_posix())
    if PLACEHOLDER in body:
        print(f"error: {PLACEHOLDER} survived substitution", file=sys.stderr)
        return 2
    try:
        json.loads(body)
    except json.JSONDecodeError as exc:
        # A rendered path containing a backslash or a quote would produce invalid
        # JSON, and Claude Code's response to that is to load NO settings at all --
        # silently unguarded again, by the very command meant to fix it.
        print(f"error: rendered settings are not valid JSON ({exc})", file=sys.stderr)
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    # newline="" so the bytes written are exactly the bytes composed: text mode
    # rewrites every \n to \r\n on Windows, which is this repo's L182/CLAUDE.md trap.
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    # stderr, not stdout: `--render --json` would otherwise emit this line ahead
    # of the report and produce output that is not parseable JSON. stdout belongs
    # to the data whenever --json is in play, and a status line is not data.
    print(f"rendered {template} -> {target} (hub clone: {hub_clone})", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--workspace",
        default=None,
        help="the session's project root (default: $CLAUDE_PROJECT_DIR, then cwd)",
    )
    parser.add_argument(
        "--hub-clone",
        default=str(REPO_ROOT),
        help="absolute path of the FFC-Cloudflare-Automation clone (default: this script's repo)",
    )
    parser.add_argument("--render", action="store_true", help="write the workspace settings.json")
    parser.add_argument("--force", action="store_true", help="with --render, overwrite an existing file")
    parser.add_argument("--json", action="store_true", dest="as_json", help="machine-readable report")
    args = parser.parse_args(argv)

    # Three provenances, and only the third is a guess. `$CLAUDE_PROJECT_DIR` is
    # set BY the session, so it is a statement of the real project root and ranks
    # with an explicit flag; the cwd is wherever the operator happened to `cd`.
    if args.workspace is not None:
        # An empty or whitespace `--workspace ""` is refused rather than accepted
        # as a statement. `Path("").resolve()` is the CWD, so the empty string is
        # the cwd fallback wearing the explicit flag's clothes -- it would score
        # as `explicit`, skip the self-certification refusal, and reproduce the
        # #1237 false green through this very fix's own escape hatch. Measured
        # before this guard existed: `--workspace ""` from inside the hub clone
        # printed `HOOKS: wired ... exit 0`. Usage error, not a verdict: nobody
        # means "the empty path", so there is no honest reading to fall back to.
        stated = args.workspace.strip()
        if not stated:
            print(
                "error: --workspace was empty; pass the session's project root "
                "(an empty value resolves to the current directory, which is the "
                "guess this flag exists to replace)",
                file=sys.stderr,
            )
            return 2
        # The STRIPPED value is what gets used, not just what gets tested. A
        # leading space makes the path RELATIVE -- measured, `" /home/user "`
        # resolves to `<cwd>/ /home/user `, which does not exist, so a correctly
        # stated root reports NOT WIRED. It points the safe way but is not
        # harmless: the remedy would then offer `--render` into that junk path,
        # and render creates parents. Caught in review round 2 on #1253.
        raw_workspace, source = stated, SOURCE_EXPLICIT
    elif os.environ.get("CLAUDE_PROJECT_DIR"):
        raw_workspace, source = os.environ["CLAUDE_PROJECT_DIR"], SOURCE_ENV
    else:
        raw_workspace, source = ".", SOURCE_INFERRED

    workspace = pathlib.Path(from_msys_path(raw_workspace)).expanduser().resolve()
    hub_clone = pathlib.Path(from_msys_path(args.hub_clone)).expanduser().resolve()

    if args.render:
        # `source` is left alone across a render. A rendered workspace gains a
        # settings.json but no `.claude/hooks/` of its own, so the self-certifying
        # test does not fire on it -- except in the one place it should: rendering
        # into a hub clone, where `render()` already refuses to clobber the tracked
        # settings.json and exits 2 before any verdict is reached.
        rc = render(workspace, hub_clone, args.force)
        if rc:
            return rc

    report = verify(workspace, source=source)
    if args.as_json:
        report["start_line"] = start_line(report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["wired"] else 1

    print(start_line(report))
    if not report["wired"]:
        # Quoted, because these are printed to be pasted and the two hosts that
        # run this both have spaces in the relevant paths: the Conductor's clone
        # sits under `C:\Program Files\Git`-adjacent trees and its workspace path
        # is operator-chosen. Unquoted, `--workspace C:/My Workspace` arrives as
        # two arguments and the remedy fails in a way that reads as the script
        # being wrong. shlex is POSIX quoting, which is correct here: the
        # Conductor's shell is git-bash, not cmd.
        script = shlex.quote(str(REPO_ROOT / "scripts" / "verify-conductor-hooks.py"))
        print()
        print("Remedy:")
        if report.get("self_certifying"):
            # Never offer `--render --workspace <the clone>` here. The clone is the
            # one directory we have just established says nothing about the session,
            # and rendering into it would overwrite the hub's own tracked settings.
            # Re-measure against the real root first; the answer may be "wired".
            hint = report.get("candidate_session_root")
            # The placeholder stays unquoted: it is a prompt to the reader, and
            # `'<session project root>'` would look like a path to paste verbatim.
            target = shlex.quote(hint) if hint else "<session project root>"
            print(f"  python3 {script} --workspace {target}")
            print("  # state the root instead of inferring it, then render only if that says NOT WIRED")
        else:
            print(f"  python3 {script} \\")
            print(
                f"    --render --workspace {shlex.quote(str(workspace))} "
                f"--hub-clone {shlex.quote(str(hub_clone))}"
            )
        print("  (docs/runbooks/conductor-hook-wiring.md explains why the copy is a build product)")
    return 0 if report["wired"] else 1


if __name__ == "__main__":
    sys.exit(main())
