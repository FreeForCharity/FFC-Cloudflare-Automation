#!/usr/bin/env python3
"""Every `working-directory:` must name a directory that will exist (#972).

PR #858 added a dry-run report step to `702-ffc-ex-clone-deploy.yml` declaring
`working-directory: target-repo`. There is no `target-repo` directory in that
job — the target repo is checked out with `path: ffc-ex`, and `target` is the
*step id* that produces the repo **name**, so the wrong noun got used as a path.
The step would have failed with

    An error occurred trying to start process '/usr/bin/bash' with working
    directory '/home/runner/work/.../target-repo'. No such file or directory

on **every** dry run — the one path the PR existed to add.

WHY THIS IS STATIC AND NOT A TEST
    Nothing in CI could have caught it. The YAML is valid, `actionlint` is
    silent (it does not model which directories a job creates), the catalog
    regenerates, and the doc-consistency and cross-reference guards all pass. It
    fails only at runtime, and only on a gated dry-run branch that by definition
    nobody had executed — the same "green here, dead there" asymmetry as #945
    and #929. A defect that never turns CI red has to be read out of the source.

WHAT COUNTS AS A VIOLATION
    A job's directories come from a small, enumerable set:

      * the workspace root (and `${{ github.workspace }}`, which names it);
      * a directory committed in THIS repository (`scripts/`, `automation/`, …),
        which the default `actions/checkout` puts at the workspace root;
      * an `actions/checkout` step with a literal `path:` input;
      * a `mkdir` / `New-Item -ItemType Directory` in an EARLIER step's `run`.

    Any `working-directory:` matching none of those is a typo or a stale name.
    Order is part of the rule: a directory created by a *later* step does not
    exist yet when the step runs, so it is still a finding. A step that cannot
    itself run contributes nothing either — its `mkdir` never executes.

    `defaults.run.working-directory` (workflow or job scope) is judged the same
    way, against what is proven at the FIRST `run:` step that inherits it, and
    reported once per job. The circular case is the one that matters: a `mkdir`
    in a `run:` step can never prove the default, because that step itself
    starts in the unproven directory and fails before the `mkdir` executes.
    An `actions/checkout` can, since `defaults.run` does not apply to `uses:`
    steps.

    A path *inside* a checked-out or freshly-made directory (`ffc-ex/apps/web`)
    is accepted on the prefix. The contents of a foreign checkout are not in
    this tree, so the guard cannot say whether the subdirectory exists — and
    inventing a finding it cannot support would be the kind of noise that gets a
    guard switched off. This is the one place it is deliberately permissive, and
    it is bounded: the parent must still be proven.

FAIL CLOSED — the rule this repo keeps rediscovering
    A guard that goes quiet on the inputs it does not understand is worse than
    no guard, because it reads as a pass. So each of these is REPORTED, not
    skipped:

      * a workflow that cannot be parsed at all;
      * a `working-directory:` built from an expression the guard cannot
        resolve (`${{ inputs.dir }}`) — the one exception is
        `${{ github.workspace }}`, which is exactly the root;
      * an `actions/checkout` whose `path:` is not a literal, which makes the
        job's directory set unreadable and every unmatched `working-directory:`
        in that job unprovable.

WHAT IT CANNOT SEE — stated rather than papered over
    * Composite actions (`.github/actions/*/action.yml`) run in the caller's
      workspace, so their directory set is not knowable from the action file.
      Out of scope; no action in this repo declares a `working-directory:`.
    * A directory created by something other than `mkdir`/`New-Item` — `git
      clone <url> <dir>`, `httrack -O <dir>`, an unpacked archive. If one is
      ever used as a `working-directory:`, teach `_dirs_created_by()` about it
      rather than deleting the finding.
    * Whether a subdirectory of a foreign checkout exists (see above).

Exit code 0 when clean, 1 when any finding is reported. Import
`find_findings()` to test it against a workflow source string without touching
disk.
"""

from __future__ import annotations

import os
import pathlib
import re
import shlex
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# Directories that exist on a runner without anybody making them.
WORKSPACE_ALIASES = frozenset({"", ".", "./", "${{ github.workspace }}"})

# Repo directories never worth offering as a checkout target.
IGNORED_TREE_DIRS = frozenset({".git", "node_modules", ".next", "out", "__pycache__"})

# `uses: actions/checkout@...` in any pinned spelling.
CHECKOUT_RE = re.compile(r"^actions/checkout(?:@|$)")

# `mkdir foo`, `mkdir -p a/b c`, `mkdir --parents x`. Options are skipped so the
# operands are read, and quotes are stripped so `mkdir -p "build dir"` is seen.
MKDIR_RE = re.compile(r"(?:^|[;&|]|\bthen\s|\bdo\s)\s*mkdir\b([^\n;&|]*)", re.MULTILINE)

# `New-Item -ItemType Directory -Path foo` / `-Force foo` (PowerShell). The path
# may be positional or named, so every non-flag operand is taken.
NEW_ITEM_RE = re.compile(
    r"New-Item\b(?=[^\n;|]*-ItemType\s+Directory)([^\n;|]*)", re.IGNORECASE
)

# Any `${{ ... }}` expression.
EXPRESSION_RE = re.compile(r"\$\{\{.*?\}\}", re.DOTALL)

# What an `actions/checkout` step contributes to a job's directory set.
CHECKOUT_ROOT = "root"
CHECKOUT_DIR = "dir"
CHECKOUT_UNREADABLE = "unreadable"

# Finding kinds, so `main()` can group the report.
BAD_DIR = "bad-dir"
UNRESOLVABLE = "unresolvable"
UNPARSEABLE = "unparseable"

_LINE_KEY = "__line__"


class Finding:
    """One `working-directory:` the guard cannot vouch for."""

    def __init__(
        self, path: str, line: int, job: str, step: str, value: str, reason: str, kind: str
    ) -> None:
        self.path = path
        self.line = line
        self.job = job
        self.step = step
        self.value = value
        self.reason = reason
        self.kind = kind

    def __str__(self) -> str:
        where = f"job `{self.job}`" if self.job else "workflow"
        if self.step:
            where += f", step `{self.step}`"
        return f"{self.path}:{self.line}: {where}: {self.reason}"


class _LineLoader(yaml.SafeLoader):
    """SafeLoader that records each mapping's line, so findings can be located."""


def _construct_mapping(loader: _LineLoader, node: yaml.MappingNode) -> dict:
    mapping = yaml.SafeLoader.construct_mapping(loader, node, deep=False)
    mapping[_LINE_KEY] = node.start_mark.line + 1
    return mapping


_LineLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _normalize(value: str) -> str:
    """`./ffc-ex/` and `ffc-ex` are the same directory."""
    text = value.strip().strip("'\"").replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def _wd_line(lines: list[str], start: int, value: str) -> int:
    """The line of the `working-directory:` inside the block starting at `start`.

    Falls back to the block's own first line, which is always a real location in
    the file even when the value was folded across lines.
    """
    for offset in range(start - 1, min(start + 60, len(lines))):
        stripped = lines[offset].strip()
        if stripped.startswith("working-directory:") and _normalize(
            stripped.split(":", 1)[1]
        ) == _normalize(value):
            return offset + 1
    return start


def _operands(text: str) -> list[str]:
    """Non-flag operands of a shell/PowerShell command fragment.

    Tokenized with `shlex` so a quoted path survives as ONE operand: a plain
    `.split()` turns `mkdir -p "build dir"` into `build` and `dir`, which both
    invents two directories nobody made and loses the one that was — turning a
    correct `working-directory: build dir` into a false finding. An unbalanced
    quote is not tokenizable at all; fall back to whitespace there rather than
    raise, since the operands are still the best guess available.
    """
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()

    out: list[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            # `-ItemType Directory`, `-Path`, `-Name` each consume their value.
            if token.lower() in {"-itemtype", "-path", "-name"}:
                skip_next = token.lower() == "-itemtype"
            continue
        out.append(token.strip("'\""))
    return out


def _dirs_created_by(run_script: str) -> set[str]:
    """Directories an earlier step's `run:` provably creates."""
    made: set[str] = set()
    for match in MKDIR_RE.finditer(run_script):
        made.update(_normalize(op) for op in _operands(match.group(1)))
    for match in NEW_ITEM_RE.finditer(run_script):
        made.update(_normalize(op) for op in _operands(match.group(1)))
    return {d for d in made if d and "$" not in d}


def _repo_dirs(root: pathlib.Path) -> set[str]:
    """Every directory committed in this repository, workspace-relative.

    Walks with `os.walk` and PRUNES the ignored names in place, rather than
    listing everything and filtering afterwards. The filtering version returned
    the same set while still descending into `.git` — thousands of loose-object
    directories on a full clone, stat-ed on every CI run for a result that was
    thrown away.
    """
    dirs: set[str] = set()
    for current, subdirs, _files in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in IGNORED_TREE_DIRS]
        here = pathlib.Path(current)
        for name in subdirs:
            dirs.add((here / name).relative_to(root).as_posix())
    return dirs


def _steps(container: dict) -> list[dict]:
    steps = container.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _default_wd(container: dict) -> str | None:
    defaults = container.get("defaults")
    if not isinstance(defaults, dict):
        return None
    run = defaults.get("run")
    if not isinstance(run, dict):
        return None
    value = run.get("working-directory")
    return value if isinstance(value, str) else None


def _step_label(step: dict, index: int) -> str:
    for key in ("name", "id", "uses"):
        value = step.get(key)
        if isinstance(value, str) and value:
            return value
    return f"step #{index + 1}"


def _checkout_path(step: dict) -> tuple[str, str] | None:
    """What an `actions/checkout` step puts on disk, or None if it is not one.

    Returns `(CHECKOUT_ROOT, "")` when it checks out at the workspace root,
    `(CHECKOUT_DIR, path)` for a literal path, and `(CHECKOUT_UNREADABLE, repr)`
    for anything else — an expression, or a non-string scalar.

    The non-string case is why this does not just `str(path)`. Coercing turns an
    unreadable value into something that *looks* like a proven literal, which is
    the fail-open shape the whole guard exists to avoid: the coerced text would
    not match `EXPRESSION_RE`, so it would be silently accepted as a directory
    and would suppress the very finding it should raise.
    """
    uses = step.get("uses")
    if not isinstance(uses, str) or not CHECKOUT_RE.match(uses.strip()):
        return None
    with_block = step.get("with")
    if not isinstance(with_block, dict):
        return CHECKOUT_ROOT, ""
    path = with_block.get("path")
    if path is None:
        return CHECKOUT_ROOT, ""
    if not isinstance(path, str):
        return CHECKOUT_UNREADABLE, repr(path)
    if EXPRESSION_RE.search(path):
        return CHECKOUT_UNREADABLE, path
    return CHECKOUT_DIR, path


def _accepted(value: str, known: set[str]) -> bool:
    """Whether `value` is the root, a known directory, or inside one."""
    if value in WORKSPACE_ALIASES or _normalize(value) == "":
        return True
    norm = _normalize(value)
    return any(norm == d or norm.startswith(d + "/") for d in known if d)


def find_findings(
    source: str, path: str = "<string>", repo_dirs: set[str] | None = None
) -> list[Finding]:
    """Every `working-directory:` in one workflow that the guard cannot vouch for."""
    lines = source.splitlines()
    try:
        document = yaml.load(source, Loader=_LineLoader)  # noqa: S506 — SafeLoader subclass
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        return [
            Finding(
                path,
                (mark.line + 1) if mark else 0,
                "",
                "",
                "",
                f"cannot parse the workflow: {getattr(exc, 'problem', exc)}",
                UNPARSEABLE,
            )
        ]

    if not isinstance(document, dict):
        return [Finding(path, 0, "", "", "", "workflow is not a mapping", UNPARSEABLE)]

    base_dirs = set(repo_dirs if repo_dirs is not None else _repo_dirs(REPO_ROOT))
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return []

    found: list[Finding] = []
    workflow_default = _default_wd(document)

    for job_id, job in jobs.items():
        if job_id == _LINE_KEY or not isinstance(job, dict):
            continue

        unreadable_checkout: str | None = None
        for step in _steps(job):
            checkout = _checkout_path(step)
            if checkout is not None and checkout[0] == CHECKOUT_UNREADABLE:
                unreadable_checkout = checkout[1]

        job_line = job.get(_LINE_KEY, 0)
        if unreadable_checkout is not None:
            found.append(
                Finding(
                    path,
                    job_line,
                    str(job_id),
                    "",
                    unreadable_checkout,
                    f"checks out with a non-literal path `{unreadable_checkout}` — the "
                    f"job's directory set cannot be read, so no working-directory: in "
                    f"this job can be proven",
                    UNRESOLVABLE,
                )
            )

        # A job default falls back to the workflow default. `defaults.run`
        # applies to `run:` steps ONLY — a `uses:` step is unaffected, which is
        # what lets an `actions/checkout` legitimately create the very directory
        # the default names.
        job_default = _default_wd(job)
        effective_default = job_default if job_default is not None else workflow_default

        # One ordered pass: every step is judged against what the steps BEFORE
        # it have proven, and contributes its own effects only afterwards.
        available = set(base_dirs)
        default_reported = False

        for index, step in enumerate(_steps(job)):
            own = step.get("working-directory")
            own = own if isinstance(own, str) else None
            run_script = step.get("run")
            is_run = isinstance(run_script, str)

            step_findings: list[Finding] = []
            if own is not None:
                step_findings = _judge(
                    path,
                    lines,
                    step.get(_LINE_KEY, job_line),
                    str(job_id),
                    _step_label(step, index),
                    own,
                    available,
                )
            elif is_run and effective_default is not None and not default_reported:
                # The default is a property of the job, so one finding per job
                # says it — attributed to the first step that would actually
                # execute under it, which is where it fails.
                scope = "job" if job_default is not None else "workflow"
                step_findings = _judge(
                    path,
                    lines,
                    step.get(_LINE_KEY, job_line),
                    str(job_id),
                    f"{_step_label(step, index)} (via defaults.run, {scope} scope)",
                    effective_default,
                    available,
                )
                default_reported = bool(step_findings)

            found.extend(step_findings)

            # This step's own effects land after it is judged, and only if the
            # step can run at all. A `mkdir` cannot prove the directory its own
            # step executes in: that step starts in the unproven directory and
            # fails before the mkdir ever runs. Counting it was the circular
            # fail-open this pass replaced.
            checkout = _checkout_path(step)
            if checkout is not None and checkout[0] == CHECKOUT_DIR:
                available.add(_normalize(checkout[1]))
            if is_run and not step_findings:
                available |= _dirs_created_by(run_script)

    return found


def _judge(
    path: str,
    lines: list[str],
    line: int,
    job: str,
    step: str,
    value: str,
    known: set[str],
) -> list[Finding]:
    """One `working-directory:` value against the directories proven available."""
    at = _wd_line(lines, line, value)

    if EXPRESSION_RE.search(value):
        if _normalize(EXPRESSION_RE.sub("", value)) == "" and "github.workspace" in value:
            return []  # `${{ github.workspace }}` is exactly the root
        return [
            Finding(
                path,
                at,
                job,
                step,
                value,
                f"working-directory: `{value}` is built from an expression this guard "
                f"cannot resolve — it may or may not name a real directory",
                UNRESOLVABLE,
            )
        ]

    if _accepted(value, known):
        return []

    return [
        Finding(
            path,
            at,
            job,
            step,
            value,
            f"working-directory: `{value}` names no directory this job has — it is not "
            f"in the repository, not an actions/checkout path, and not created by an "
            f"earlier step. The step will fail to start.",
            BAD_DIR,
        )
    ]


def scan_repo(root: pathlib.Path | None = None) -> list[Finding]:
    root = root or REPO_ROOT
    workflows = root / ".github" / "workflows"
    repo_dirs = _repo_dirs(root)
    found: list[Finding] = []
    for wf in sorted(list(workflows.glob("*.yml")) + list(workflows.glob("*.yaml"))):
        text = wf.read_text(encoding="utf-8")
        found.extend(find_findings(text, wf.relative_to(root).as_posix(), repo_dirs))
    return found


def main() -> int:
    findings = scan_repo()
    if findings:
        print(f"working-directory: values that cannot be vouched for: {len(findings)}\n")
        for f in findings:
            print(f"  {f}")
        print(
            "\nEach one either names a directory nothing in the job creates, or is built\n"
            "from something this guard cannot read. A wrong path fails only at runtime,\n"
            "with 'No such file or directory' when the step's shell starts — #858 shipped\n"
            "`working-directory: target-repo` into 702's dry-run branch, where the step id\n"
            "`target` had been used as if it were the checkout path `ffc-ex`.\n"
            "See docs/lessons-ledger.md and issue #972."
        )
        return 1

    workflows = sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))
    print(
        f"working-directory OK: {len(workflows)} workflows, every working-directory: "
        f"resolves to the workspace root, a repository directory, a checkout path, or a "
        f"directory an earlier step creates."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
