#!/usr/bin/env python3
"""Verify that the Conductor's session actually loads this repo's hooks (#1042).

THE DEFECT THIS EXISTS FOR
    Claude Code loads hooks from the **session's project root**. Every sandboxed
    agent clones this repo and opens it as its project root, so `.claude/settings.json`
    applies and `.claude/hooks/` runs. The Conductor does not: its project root is
    its own workspace, and it merely `cd`s into a clone to work. The hub's settings
    spell every hook path as `$CLAUDE_PROJECT_DIR/.claude/hooks/...`, and
    `$CLAUDE_PROJECT_DIR` for that session is the *workspace* -- so the block never
    resolves and the single most privileged actor in the system runs unguarded.

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

    `--workspace` defaults to $CLAUDE_PROJECT_DIR, then to the current directory.
    `--hub-clone` defaults to this script's own repository root.

EXIT CODES
    0  wired -- settings resolve and the guard demonstrably blocks and allows
    1  not wired, or the probe did not land both verdicts
    2  usage error (missing template, unreadable workspace)

    Non-zero is deliberately the same for "no config" and "config present but the
    guard does not work". Both mean the same thing to the run about to start: you
    are unguarded. Distinguishing them is the report's job, not the exit code's.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE_REL = pathlib.Path(".claude") / "conductor" / "settings.template.json"
PLACEHOLDER = "__HUB_CLONE__"

# Claude Code reads both, `settings.local.json` last (it overrides).
SETTINGS_NAMES = ("settings.json", "settings.local.json")

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


def verify(workspace: pathlib.Path) -> dict:
    report: dict = {
        "workspace": str(workspace),
        "settings_files": [],
        "hooks_carriers": [],
        "missing_paths": [],
        "probes": [],
        "problems": [],
        "wired": False,
    }
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
        default=os.environ.get("CLAUDE_PROJECT_DIR") or ".",
        help="the Conductor's session project root (default: $CLAUDE_PROJECT_DIR, then cwd)",
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

    workspace = pathlib.Path(from_msys_path(args.workspace)).expanduser().resolve()
    hub_clone = pathlib.Path(from_msys_path(args.hub_clone)).expanduser().resolve()

    if args.render:
        rc = render(workspace, hub_clone, args.force)
        if rc:
            return rc

    report = verify(workspace)
    if args.as_json:
        report["start_line"] = start_line(report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["wired"] else 1

    print(start_line(report))
    if not report["wired"]:
        print()
        print("Remedy:")
        print(f"  python3 {REPO_ROOT / 'scripts' / 'verify-conductor-hooks.py'} \\")
        print(f"    --render --workspace {workspace} --hub-clone {hub_clone}")
        print("  (docs/runbooks/conductor-hook-wiring.md explains why the copy is a build product)")
    return 0 if report["wired"] else 1


if __name__ == "__main__":
    sys.exit(main())
