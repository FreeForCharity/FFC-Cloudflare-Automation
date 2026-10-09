#!/usr/bin/env python3
"""Verify that a Conductor workspace's clones are on their origin default branch.

THE DEFECT THIS EXISTS FOR
    Conductor run 231 (2026-10-09) opened with the hub clone parked on
    `fix/rule6-segment-scope`, not `main`. Every step-0 read of `AGENTS.md`,
    `docs/workflow-safety-and-approvals.md` and `docs/lessons-ledger.md` would
    have been served from that branch, and the routine's own instruction --
    "git fetch/pull every core clone to the origin default branch" -- does not
    catch it, because `git pull` on a parked feature branch succeeds:

        $ git pull
        Already up to date.

    That is the whole trap. The command exits 0, says the reassuring thing, and
    says it about the wrong branch. Six earlier runs believed it (ledger
    `conductor-assert-clone-on-default-branch` in the Conductor's own memory),
    and a prose lesson had already been written and did not prevent run 231's
    recurrence -- which is why this is a script.

WHY THIS IS THE SAME FAMILY AS verify-conductor-hooks.py
    Both failures are *silence*. A parked clone produces no error, no banner and
    no log line: it produces a run that reads real files, from a real checkout,
    at a real commit, and reports what it found. Nothing inside any of those
    reads can falsify it. Same shape as CLAUDE.md's "a background verification is
    bound to the working tree it started in" and as "green on `main`, red on the
    branch is only a control if the branch is the only thing that moved" -- the
    measurement is sound and its *referent* is wrong, so every usual tell (a
    crash, a non-zero exit, an error message) is absent.

    So this script follows that one's design deliberately:

    1. **The workspace is stated, never guessed.** `--workspace` is required.
       `verify-conductor-hooks.py` shipped a cwd fallback and it produced a
       documented false green (#1237); a verifier whose subject is "which tree
       is this run reading" must not infer the tree from the cwd it happens to
       have, because the cwd *is* the thing under suspicion.
    2. **It prints in both directions.** A verifier that only speaks on failure
       leaves "clean" and "the check never ran" identical in the record, so the
       `CLONES:` line is emitted on success too, for the run's START comment.
    3. **Three wordings, two exit codes.** `clean` / `PARKED` / `UNVERIFIED`.
       "I could not tell" and "it is parked" are the same instruction to the run
       about to start -- stop and fix it -- and only the record needs them apart.

WHAT IT CHECKS, PER CLONE
    1. `<workspace>/repos/<name>` exists and is a git work tree.
    2. `origin` exists and `refs/remotes/origin/HEAD` resolves, so the default
       branch is *read* rather than assumed to be `main`. A clone made with
       `--single-branch`, or one whose `origin/HEAD` was never set, reports
       UNVERIFIED -- not "ok", and not "parked".
    3. HEAD is on that branch by name. A detached HEAD at the same commit is
       reported, because the next `git checkout -b` from it silently branches
       from a commit rather than from the branch.
    4. The checkout is not behind its remote-tracking ref (a stale clone reads
       old docs just as confidently as a parked one), and
    5. the work tree is clean, since uncommitted edits are how a previous run's
       scratch work gets read as repo state.

    (3) is the one run 231 needed. (4) and (5) are here because they are free
    once the repo is open and they fail the same silent way.

NOT A NETWORK CHECK
    Everything above is local: `origin/HEAD` and `origin/<branch>` are
    remote-tracking refs that a fetch updates. This script does not fetch, so it
    reports the state the *last* fetch left -- run it after the bootstrap fetch,
    which is where the routine already does its pulling. Making it fetch would
    couple a read-only assertion to the network and give it a third failure mode
    nobody wants in step 0.

USAGE
    python3 scripts/verify-conductor-clones.py --workspace C:/ClaudeCodeDesktop/Claude_AI_OS_Routine
    python3 scripts/verify-conductor-clones.py --workspace <ws> --json
    python3 scripts/verify-conductor-clones.py --workspace <ws> --clone FFC-EX-canary

    Exit 0 when every checked clone is on its origin default branch, up to date
    with it and clean. Exit 1 on any parked, stale, dirty or unverifiable clone.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

# The routine's core clones -- the five that exist every run and that step 0
# reads its source of truth from. Anything else under `repos/` is cloned on
# demand and is checked only when named with `--clone`, because an on-demand
# clone is legitimately parked on a PR branch while work is in flight.
CORE_CLONES = (
    "FFC-Cloudflare-Automation",
    "FFC-IN-freeforcharity.org",
    "FFC-IN-ffcadmin.org",
    "FFC-IN-FFC_Single_Page_Template",
    "FFC-IN-Footer_Only_Template",
)

OK = "ok"
PARKED = "parked"
UNVERIFIED = "unverified"


def translate_msys_prefix(path: str) -> str:
    """Rewrite a git-bash `/c/...` path to `C:/...` on Windows only.

    Native Windows Python reads a leading `/c/` as *drive-relative* and resolves
    `/c/Users/x` to `C:\\c\\Users\\x`, so without this a correctly-stocked
    workspace passed as `$PWD` from git-bash reports every clone missing --
    `verify-conductor-hooks.py` carries the same translation for the same reason
    (CLAUDE.md: "Python on this host cannot open a git-bash `/c/...` path").
    """
    if sys.platform != "win32":
        return path
    parts = path.replace("\\", "/").split("/")
    if len(parts) > 2 and parts[0] == "" and len(parts[1]) == 1 and parts[1].isalpha():
        return f"{parts[1].upper()}:/" + "/".join(parts[2:])
    return path


def git(
    repo: pathlib.Path, *args: str
) -> tuple[int, str]:
    """Run git in `repo` and return (returncode, stripped stdout).

    stderr is folded into stdout on failure so a caller reporting UNVERIFIED can
    quote the reason. The full environment is inherited: CLAUDE.md records that a
    scrubbed `env=` dict breaks `bash`/`node` on the Conductor's Windows host and
    that the resulting exit 1 is indistinguishable from the real failure the
    caller is testing for.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = proc.stdout.strip() or proc.stderr.strip()
    return proc.returncode, out


def default_branch(repo: pathlib.Path) -> tuple[str | None, str]:
    """Read origin's default branch from `refs/remotes/origin/HEAD`.

    Returns (branch, detail). `branch is None` means UNVERIFIED -- never a
    fallback to "main". Assuming `main` is how this check would acquire its own
    false green: a clone whose `origin/HEAD` is unset is exactly a clone whose
    branch nobody has established, and answering "main" for it reports a
    verification that did not happen.
    """
    rc, out = git(repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if rc != 0 or not out:
        return None, f"origin/HEAD does not resolve ({out or 'no output'})"
    prefix = "origin/"
    if not out.startswith(prefix):
        return None, f"origin/HEAD resolved to an unexpected ref: {out}"
    return out[len(prefix) :], out


def check_clone(workspace: pathlib.Path, name: str) -> dict:
    """Check one clone. Returns a row; `status` is OK / PARKED / UNVERIFIED."""
    path = workspace / "repos" / name
    row: dict = {"clone": name, "path": str(path)}

    if not path.is_dir():
        row.update(status=UNVERIFIED, detail="no such directory")
        return row

    rc, top = git(path, "rev-parse", "--show-toplevel")
    if rc != 0:
        row.update(status=UNVERIFIED, detail=f"not a git work tree ({top})")
        return row

    rc, remotes = git(path, "remote")
    if rc != 0 or "origin" not in remotes.split():
        row.update(status=UNVERIFIED, detail="no `origin` remote")
        return row

    branch, detail = default_branch(path)
    if branch is None:
        row.update(status=UNVERIFIED, detail=detail)
        return row
    row["default_branch"] = branch

    # `--abbrev-ref HEAD` prints the literal "HEAD" on a detached checkout, which
    # is why this compares against that string rather than testing for emptiness.
    rc, head = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        row.update(status=UNVERIFIED, detail=f"cannot read HEAD ({head})")
        return row
    row["head"] = head

    if head == "HEAD":
        rc, sha = git(path, "rev-parse", "--short", "HEAD")
        row.update(
            status=PARKED,
            detail=(
                f"detached HEAD at {sha if rc == 0 else '?'}; "
                f"a branch cut from here is cut from a commit, not from {branch}"
            ),
        )
        return row

    if head != branch:
        row.update(
            status=PARKED,
            detail=(
                f"on `{head}`, not origin's default `{branch}` -- "
                "`git pull` here reports `Already up to date` about the wrong branch"
            ),
        )
        return row

    # On the right branch. Now: is it the right *commit*, and is the tree clean?
    rc, counts = git(
        path, "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"
    )
    if rc != 0:
        row.update(
            status=UNVERIFIED,
            detail=f"cannot compare HEAD with origin/{branch} ({counts})",
        )
        return row
    try:
        ahead_s, behind_s = counts.split()
        ahead, behind = int(ahead_s), int(behind_s)
    except ValueError:
        row.update(status=UNVERIFIED, detail=f"unreadable rev-list output: {counts!r}")
        return row
    row.update(ahead=ahead, behind=behind)

    rc, porcelain = git(path, "status", "--porcelain")
    if rc != 0:
        row.update(status=UNVERIFIED, detail=f"cannot read status ({porcelain})")
        return row
    dirty = [line for line in porcelain.splitlines() if line.strip()]
    row["dirty"] = len(dirty)

    problems = []
    if behind:
        problems.append(
            f"{behind} commit(s) behind origin/{branch} -- the fetch advanced the "
            f"remote-tracking ref, but this checkout was never fast-forwarded onto it"
        )
    if ahead:
        problems.append(f"{ahead} commit(s) ahead of origin/{branch}")
    if dirty:
        problems.append(f"{len(dirty)} uncommitted path(s)")

    if problems:
        row.update(status=PARKED, detail="; ".join(problems))
        return row

    row.update(status=OK, detail=f"on `{branch}`, level with origin, clean")
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Assert a Conductor workspace's clones are on their origin default branch. "
            "Reads remote-tracking refs only; does not fetch."
        )
    )
    parser.add_argument(
        "--workspace",
        required=True,
        help=(
            "The workspace root holding `repos/` -- STATE it, do not pass a cwd "
            "stand-in. The cwd is the thing under suspicion (#1237)."
        ),
    )
    parser.add_argument(
        "--clone",
        action="append",
        default=None,
        metavar="NAME",
        help="Check this clone instead of the core five. Repeatable.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON, not prose.")
    args = parser.parse_args(argv)

    workspace = pathlib.Path(translate_msys_prefix(args.workspace))
    names = tuple(args.clone) if args.clone else CORE_CLONES

    if not (workspace / "repos").is_dir():
        line = (
            f"CLONES: UNVERIFIED -- {workspace}/repos does not exist, so no clone was "
            "checked (verify-conductor-clones.py, exit 1)"
        )
        print(json.dumps({"status": UNVERIFIED, "workspace": str(workspace), "rows": []}) if args.json else line)
        return 1

    rows = [check_clone(workspace, name) for name in names]
    parked = [r for r in rows if r["status"] == PARKED]
    unverified = [r for r in rows if r["status"] == UNVERIFIED]

    if args.json:
        print(
            json.dumps(
                {
                    "status": PARKED if parked else (UNVERIFIED if unverified else OK),
                    "workspace": str(workspace),
                    "rows": rows,
                },
                indent=2,
            )
        )
    else:
        for row in rows:
            print(f"  [{row['status']:<10}] {row['clone']}: {row['detail']}")
        if parked:
            worst = ", ".join(f"{r['clone']} ({r['detail']})" for r in parked)
            print(
                f"CLONES: PARKED -- {worst} "
                "(verify-conductor-clones.py, exit 1)"
            )
        elif unverified:
            worst = ", ".join(f"{r['clone']} ({r['detail']})" for r in unverified)
            print(
                f"CLONES: UNVERIFIED -- {worst} "
                "(verify-conductor-clones.py, exit 1)"
            )
        else:
            print(
                f"CLONES: clean -- all {len(rows)} on their origin default branch, "
                "level with origin, no uncommitted paths "
                "(verify-conductor-clones.py, exit 0)"
            )

    return 1 if (parked or unverified) else 0


if __name__ == "__main__":
    sys.exit(main())
