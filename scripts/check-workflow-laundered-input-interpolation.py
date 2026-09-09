#!/usr/bin/env python3
r"""Guard: a free-text dispatch input must not reach a script body through a
step or job OUTPUT either (#1233).

`scripts/check-workflow-input-interpolation.py` is defined over `inputs.X`.
That is the right definition for what it freezes, and it is why the same
dispatcher-supplied value can still arrive as source code one hop later:

    # job whmcs_preflight, step `meta` — remedied the house way, so the
    # sibling guard sees nothing here and is CORRECT not to
    env:
      IN_DOMAIN: ${{ inputs.domain }}
    run: |
      $d = $env:IN_DOMAIN.Trim().ToLowerInvariant().Trim('.')
      "domain=$d" | Out-File -FilePath $env:GITHUB_OUTPUT -Append -Encoding utf8

    # a later step, as 102 shipped — the value is source code again
    run: |
      $domain = "${{ steps.meta.outputs.domain }}"

Measured on the real bodies before this guard was written (#1233): with
`Metadata` fixed and the downstream site untouched, the `$( )` payload arrives
at `Metadata` as inert data, is written VERBATIM into `GITHUB_OUTPUT`, and
EXECUTES at the downstream double-quoted site — the WHMCS API secret reached a
sentinel while `-Domain` still bound a legal `example.org` and the step exited 0.

WHY THIS IS ITS OWN GUARD RATHER THAN A WIDER PATTERN IN THE SIBLING
    The sibling is a pattern over one step's text: does this body mention
    `inputs.X`. This is DATAFLOW — a write in one step, a read in another, often
    in another job through `needs.*.outputs.*`, and the two are connected only
    by a name. Folding it in would put two populations behind one freeze, and
    #1210 already records what a single shared freeze costs: it serialises the
    burn-down, because two lanes cannot both edit the same pinned list. Two
    guards, two freezes, two lanes.

    They are disjoint by construction. This one NEVER reports a reference to
    `inputs.` — that is the sibling's finding, and a body that carries both is
    reported once by each for the thing that is actually its own.

WHY THE FAILURE POINTS THE FLATTERING WAY
    A burn-down lane that fixes exactly what the sibling names deletes its
    freeze entry, goes green on every check in the repo, and reports a completed
    lane that left a working injection under a production credential. Nothing in
    the tree contradicts it. That is the whole reason this exists: the remedy
    for the first hop (`env:`) is invisible to a guard defined over `inputs.`,
    so applying the remedy REMOVES the only signal anybody was reading.

WHAT COUNTS AS A SOURCE
    A step "carries" the dispatch value if a free-text input reaches it by any
    surface that ends up in that step's process — its own body, its step-level
    `env:`, or the job-/workflow-level `env:` it inherits. The `env:` case is
    the important one and the easy one to leave out: it is the REMEDIED form,
    which is exactly where a laundering hop is now most likely to start.

WHAT COUNTS AS A SINK
    An expression in a `run:` body or a `github-script` `script:` that reads a
    tainted `steps.<id>.outputs.<name>`, `needs.<job>.outputs.<name>` or
    `env.<NAME>`. Reading the same value through `$VAR` / `$env:VAR` is NOT a
    finding — that is the remedy, and a guard that flagged it would have to be
    switched off to land the fix for the thing it guards. Expressions in `env:`,
    `with:`, `if:` and `name:` are untouched for the same reason.

PRECISION, AND WHICH DIRECTION IT ERRS IN
    Taint is tracked per STEP, not per shell variable: every name a carrying
    step writes to `GITHUB_OUTPUT` is treated as carrying the value. So a step
    that holds the domain and also publishes an unrelated `out_dir` has both
    outputs marked. That over-reports, deliberately, and for the reason the
    sibling gives for its own pessimistic choices: the cost of over-reporting is
    a reviewer reading one extra name and a freeze entry saying why, and the
    cost of under-reporting is the blind spot this exists to close. A freeze
    entry is the place to record "this one is inert", not the analysis.

    Step-level `env:` taint is likewise widened to the whole job rather than
    tracked to the one step, and a `GITHUB_ENV` write to all of the job rather
    than to the steps after it. Same direction, same reason.

FAIL CLOSED
    A workflow whose YAML will not parse is a finding, never a silent skip.

THE FREEZE
    `KNOWN_LAUNDERED` pins the hops that already exist, asserted EXACTLY in both
    directions, like the sibling's `KNOWN_UNGUARDED`: a new hop fails the run,
    and an entry that no longer matches anything ALSO fails it, so a burn-down
    cannot leave the list lying.

Exit codes: 0 = the laundering set is exactly the frozen one, 1 = a new hop, a
stale entry, or a file that could not be read.
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _load_sibling():
    """Import `check-workflow-input-interpolation.py` by path.

    Its filename is not a Python identifier, so a plain `import` cannot reach
    it. Loading it rather than copying it is the point: the two guards must
    agree on what a free-text input is, which types are constrained, which
    environments are write lanes and how a `GITHUB_OUTPUT` write is spelled. Two
    copies would drift, and the copy that fell behind would be the one that
    reads clean.
    """
    path = REPO_ROOT / "scripts" / "check-workflow-input-interpolation.py"
    spec = importlib.util.spec_from_file_location("_ffc_input_interpolation", path)
    if spec is None or spec.loader is None:  # pragma: no cover - unreachable in tree
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sibling = _load_sibling()

WorkflowUnreadable = sibling.WorkflowUnreadable
free_text_inputs = sibling.free_text_inputs
environments = sibling.environments
is_write_environment = sibling.is_write_environment
exported_names = sibling.exported_names
workflow_paths = sibling.workflow_paths
_EXPRESSION = sibling._EXPRESSION
_INPUT_REF = sibling._INPUT_REF

# The three ways a laundered value comes back as substituted TEXT. Whitespace is
# tolerated around the dots for the same reason the sibling tolerates it: the
# expression language allows it, and a guard evadable with a space is
# decoration.
_STEP_OUTPUT_REF = re.compile(
    r"\bsteps\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\.\s*outputs\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)"
)
_NEEDS_OUTPUT_REF = re.compile(
    r"\bneeds\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)\s*\.\s*outputs\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)"
)
_ENV_REF = re.compile(r"\benv\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)")

# `core.setOutput('name', value)` — the `github-script` spelling of a
# GITHUB_OUTPUT write, which the shared `exported_names` cannot see because it
# is not a run-file protocol at all. 105 and 113 both use it.
_SET_OUTPUT = re.compile(
    r"\bcore\s*\.\s*setOutput\s*\(\s*[\"']([A-Za-z_][A-Za-z0-9_-]*)[\"']"
)


class Hop:
    """One laundered reference reaching one script body."""

    def __init__(self, workflow: str, job: str, step: str, kind: str,
                 reference: str, expression: str, line: int | None) -> None:
        self.workflow = workflow
        self.job = job
        self.step = step
        self.kind = kind
        # The dotted reference as the freeze names it, e.g.
        # `steps.meta.outputs.domain`. Stable across a step being renamed, which
        # a `path:LINE` citation is not (ledger L196).
        self.reference = reference
        self.expression = expression
        self.line = line

    def __str__(self) -> str:
        where = f"{self.workflow}:{self.line}" if self.line else self.workflow
        return (
            f"{where} job '{self.job}' step '{self.step}' ({self.kind}) "
            f"interpolates `{self.reference}`, which carries a free-text "
            f"dispatch input, via `${{{{{self.expression}}}}}`"
        )


def _steps(job: dict) -> list[dict]:
    steps = job.get("steps")
    return [s for s in steps if isinstance(s, dict)] if isinstance(steps, list) else []


def _step_bodies(step: dict) -> list[tuple[str, str]]:
    """(kind, body) for every script this step substitutes text into."""
    bodies: list[tuple[str, str]] = []
    if isinstance(step.get("run"), str):
        bodies.append(("run", step["run"]))
    if "github-script" in str(step.get("uses") or ""):
        script = (step.get("with") or {}).get("script")
        if isinstance(script, str):
            bodies.append(("github-script", script))
    return bodies


def _env_values(mapping) -> list[str]:
    if not isinstance(mapping, dict):
        return []
    return [str(v) for v in mapping.values()]


def _expressions(text: str) -> list[str]:
    return [m.group(1) for m in _EXPRESSION.finditer(text)]


def _line_of(raw: str, needle: str) -> int | None:
    """1-based line of the first raw line containing `needle`, if any."""
    for number, line in enumerate(raw.splitlines(), start=1):
        if needle and needle in line:
            return number
    return None


def _line_of_body_anchor(raw: str, body: str, anchor: str) -> int | None:
    """1-based file line of `anchor` AS IT OCCURS IN THIS STEP'S BODY.

    The naive "first raw line containing the text" is wrong here often enough to
    matter, and it is wrong in the direction that gets a real finding dismissed.
    `706` reads `${{ needs.resolve.outputs.domain }}` in a `run:` body, but the
    FIRST occurrence of that text in the file is the `convert` job's
    `concurrency: group:` — not a script body and not a finding. A reviewer who
    opens the cited line sees a `concurrency:` key, concludes false positive,
    and the real hop a few hundred lines further down is never read.

    So the offset is computed inside the body (where the match actually is) and
    projected onto the file by anchoring the body's first line, then confirmed
    by re-reading the projected line. A miss falls back to the naive search and
    only ever degrades the message; neither path changes the verdict.
    """
    body_lines = body.splitlines()
    offsets = [index for index, line in enumerate(body_lines) if anchor in line]
    if not offsets:
        return _line_of(raw, anchor)
    lead = next(
        (index for index, line in enumerate(body_lines) if line.strip()), None
    )
    if lead is None:
        return _line_of(raw, anchor)
    first = body_lines[lead].strip()
    raw_lines = raw.splitlines()
    for number, line in enumerate(raw_lines, start=1):
        if line.strip() != first:
            continue
        start = number - lead
        for offset in offsets:
            candidate = start + offset
            if 1 <= candidate <= len(raw_lines) and anchor in raw_lines[candidate - 1]:
                return candidate
    return _line_of(raw, anchor)


class Analysis:
    """The taint state of one workflow, computed to a fixpoint."""

    def __init__(self, workflow: dict, free_text: set[str]) -> None:
        self.workflow = workflow
        self.free_text = free_text
        self.jobs = {
            job_id: job
            for job_id, job in (workflow.get("jobs") or {}).items()
            if isinstance(job, dict)
        }
        self.tainted_step_outputs: set[tuple[str, str, str]] = set()
        self.tainted_job_outputs: set[tuple[str, str]] = set()
        self.tainted_env: set[tuple[str, str]] = set()
        self._workflow_env = _env_values(workflow.get("env"))
        self._resolve()

    # -- taint queries ----------------------------------------------------

    def _references_input(self, expression: str) -> bool:
        return any(name in self.free_text for name in _INPUT_REF.findall(expression))

    def taint_reference(self, expression: str, job_id: str) -> str | None:
        """The dotted reference by which this expression reads a tainted value.

        `None` when it reads nothing tainted. A reference to `inputs.` is NOT
        reported here — that population belongs to the sibling guard.
        """
        for step_id, name in _STEP_OUTPUT_REF.findall(expression):
            if (job_id, step_id, name) in self.tainted_step_outputs:
                return f"steps.{step_id}.outputs.{name}"
        for needed, name in _NEEDS_OUTPUT_REF.findall(expression):
            if (needed, name) in self.tainted_job_outputs:
                return f"needs.{needed}.outputs.{name}"
        for name in _ENV_REF.findall(expression):
            if (job_id, name) in self.tainted_env:
                return f"env.{name}"
        return None

    def _carries(self, expression: str, job_id: str) -> bool:
        """Does this expression bring a dispatch-controlled value into a step?"""
        if self._references_input(expression):
            return True
        return self.taint_reference(expression, job_id) is not None

    def _step_carries(self, job_id: str, job: dict, step: dict) -> bool:
        surfaces = list(self._workflow_env)
        surfaces += _env_values(job.get("env"))
        surfaces += _env_values(step.get("env"))
        surfaces += [body for _, body in _step_bodies(step)]
        # `with:` is included for a LOCAL composite action only, whose steps run
        # in this job: a value handed to it can come back out through the
        # action's own GITHUB_OUTPUT write. A third-party action's source is not
        # in this tree, so it is reported as not judged rather than guessed at.
        if str(step.get("uses") or "").strip().startswith("./"):
            surfaces += _env_values(step.get("with"))
        for surface in surfaces:
            for expression in _expressions(surface):
                if self._carries(expression, job_id):
                    return True
        return False

    # -- fixpoint ---------------------------------------------------------

    def _pass_limit(self) -> int:
        """How many passes a correct solver may need on THIS workflow.

        Its own method so a test can shrink it and drive the real loop into the
        non-convergence branch. Verifying that branch by faking the exception
        would assert on the plumbing and leave the branch itself unexecuted —
        the same "reading the guard proves it is wired, never that it detects"
        problem the module docstring is about.
        """
        return 10 + len(self.jobs) + sum(len(_steps(j)) for j in self.jobs.values())

    def _resolve(self) -> None:
        # A bound rather than `while True`, because a solver that cannot
        # terminate must not hang CI. What the bound must NOT do is return the
        # partial result it has: every element still missing is a hop that goes
        # unreported, and under-reporting is the precise direction this guard
        # exists to close — a truncated pass would print the reassuring
        # "laundered dispatch-input interpolation OK" line while the payload
        # still executes. So exhausting the bound is a FINDING, not a fallback
        # (Copilot on #1240; the same fail-closed rule the parser already
        # follows for a file it cannot read).
        #
        # The bound is generous rather than tight because the passes are not
        # equivalent: taint spreads through as many hops per pass as the job
        # iteration order happens to allow, so an unlucky order advances one
        # hop per pass and a legitimately deep workflow needs one pass per job.
        # Scaling it with the workflow's own size keeps "bound exhausted"
        # meaning "the analysis is wrong", never "this workflow is big".
        # Every non-final pass adds at least one element to a lattice bounded
        # by the workflow's finite name space, so a correct solver converges
        # far inside this.
        limit = self._pass_limit()
        for _ in range(limit):
            before = (
                len(self.tainted_step_outputs)
                + len(self.tainted_job_outputs)
                + len(self.tainted_env)
            )
            for job_id, job in self.jobs.items():
                for step in _steps(job):
                    if not self._step_carries(job_id, job, step):
                        continue
                    step_id = step.get("id")
                    for kind, body in _step_bodies(step):
                        for name in exported_names(body, "GITHUB_ENV"):
                            self.tainted_env.add((job_id, name))
                        if not step_id:
                            continue
                        for name in exported_names(body, "GITHUB_OUTPUT"):
                            self.tainted_step_outputs.add((job_id, str(step_id), name))
                        if kind == "github-script":
                            for name in _SET_OUTPUT.findall(body):
                                self.tainted_step_outputs.add(
                                    (job_id, str(step_id), name)
                                )
                    # A step-level `env:` name is itself readable as `env.NAME`
                    # in a later expression, so a carrying step's env names are
                    # tainted too. Widened to the job (see PRECISION).
                    for name, value in (step.get("env") or {}).items():
                        for expression in _expressions(str(value)):
                            if self._carries(expression, job_id):
                                self.tainted_env.add((job_id, str(name)))
                for name, value in (job.get("outputs") or {}).items():
                    for expression in _expressions(str(value)):
                        if self._carries(expression, job_id):
                            self.tainted_job_outputs.add((job_id, str(name)))
            after = (
                len(self.tainted_step_outputs)
                + len(self.tainted_job_outputs)
                + len(self.tainted_env)
            )
            if after == before:
                return
        raise WorkflowUnreadable(
            f"taint analysis did not converge in {limit} passes "
            f"({len(self.jobs)} jobs); refusing to report a partial result, "
            "because every hop still missing would be reported as absent"
        )


def scan_workflow(path: pathlib.Path) -> list[Hop]:
    """Hops in one workflow. Raises WorkflowUnreadable on bad input."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkflowUnreadable(f"{path.name}: cannot be read ({error})") from error
    try:
        workflow = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise WorkflowUnreadable(
            f"{path.name}: YAML does not parse ({error})"
        ) from error
    if not isinstance(workflow, dict):
        raise WorkflowUnreadable(
            f"{path.name}: top level is {type(workflow).__name__}, not a mapping"
        )

    free_text = free_text_inputs(workflow)
    if not free_text:
        return []

    # Re-raised with the filename. `Analysis` cannot know which file it was
    # built from, so an unqualified message lands in `scan_all`'s unreadable
    # list naming no workflow — a fail-closed report nobody can act on is only
    # half a guard (Copilot on #1240). Every other raise on this path is
    # already prefixed the same way.
    try:
        analysis = Analysis(workflow, free_text)
    except WorkflowUnreadable as error:
        raise WorkflowUnreadable(f"{path.name}: {error}") from error

    hops: list[Hop] = []
    for job_id, job in analysis.jobs.items():
        for index, step in enumerate(_steps(job)):
            label = str(step.get("name") or f"step {index}")
            for kind, body in _step_bodies(step):
                for match in _EXPRESSION.finditer(body):
                    expression = match.group(1)
                    reference = analysis.taint_reference(expression, job_id)
                    if reference is None:
                        continue
                    anchor = match.group(0).splitlines()[0]
                    hops.append(
                        Hop(path.name, job_id, label, kind, reference, expression,
                            _line_of_body_anchor(raw, body, anchor))
                    )
    return hops


def scan_all(paths=None):
    """(hops, unreadable, scanned) across every workflow file."""
    hops: list[Hop] = []
    unreadable: list[str] = []
    paths = workflow_paths() if paths is None else list(paths)
    for path in paths:
        try:
            hops.extend(scan_workflow(path))
        except WorkflowUnreadable as error:
            unreadable.append(str(error))
    return hops, unreadable, len(paths)


def current_map(hops: list[Hop]) -> dict[str, tuple[str, ...]]:
    """workflow file -> the sorted laundered references it interpolates."""
    grouped: dict[str, set[str]] = {}
    for hop in hops:
        grouped.setdefault(hop.workflow, set()).add(hop.reference)
    return {name: tuple(sorted(v)) for name, v in sorted(grouped.items())}


# The hops that exist today, each with the reason it is frozen rather than
# fixed. A reason per ENTRY, not per file, which is the one place this freeze
# deliberately differs from the sibling's `KNOWN_UNGUARDED`: the analysis is
# pessimistic by design (see PRECISION), so a reader has to be able to tell
# `needs.resolve.outputs.domain` — the dispatch input itself, one hop later —
# from `steps.enforce.outputs.out_dir`, which is a `$RUNNER_TEMP` constant that
# is listed only because the step that writes it also holds the domain. Without
# the reason column those two render identically and the list reads as noise,
# which is how a freeze stops being read at all.
#
# Measured 2026-09-05 on `main` `e787cd5`: 5 workflows, 28 call sites, 21
# distinct references. FOUR of these five — 101, 102, 103, 706 — are absent
# from the sibling's freeze entirely, i.e. it reads them as clean today. That is
# #1233's claim, and it is now a fleet measurement rather than one lane's
# anecdote: applying the `env:` remedy removes the `inputs.` reference the
# sibling is defined over while the value goes on being substituted into script
# text at the far end.
#
# Burn-down since that measurement, kept here so the dated line above stays a
# measurement rather than turning into a wrong description of the dict below it:
# 702 fixed and removed 2026-09-07 (#1241 lane 1), leaving 4 workflows / 23 call
# sites / 16 distinct references; 706 fixed and removed 2026-09-07 (#1241 lane
# 2), leaving 3 workflows / 19 call sites / 13 distinct references — and with it
# the last LIVE hop, so what remains is the three over-approximated files. Do
# not restate the original figures as current — the guard prints both counts on
# every invocation.
#
# Burn-down is tracked separately, ordered by environment, the way #1080 orders
# its own. An entry here is not an endorsement.
KNOWN_LAUNDERED: dict[str, dict[str, str]] = {
    "101-domain-status.yml": {
        "steps.run.outputs.out_dir":
            "a $RUNNER_TEMP path, constant; tainted because the writing step "
            "also holds the domain (PRECISION). Sink is a bash double-quoted "
            "assignment, so the shape would execute if the value ever derived "
            "from the input.",
        "steps.comment.outputs.comment_path":
            "a $RUNNER_TEMP path, constant; same over-approximation. Sink is a "
            "SINGLE-quoted JS literal in github-script, where an apostrophe "
            "would end the literal (the 101 shape #1080 already calls out).",
        "needs.cloudflare.outputs.issues_count":
            "a count computed from the Cloudflare audit, not from the input; "
            "same over-approximation.",
        "needs.cloudflare.outputs.severe_issues_count":
            "a count computed from the Cloudflare audit, not from the input; "
            "same over-approximation.",
        "needs.cloudflare.outputs.changes_count":
            "a count computed from the Cloudflare audit, not from the input; "
            "same over-approximation.",
        "needs.m365.outputs.domain_exists":
            "a boolean from a Graph lookup, not the input; same "
            "over-approximation.",
        "needs.m365.outputs.is_verified":
            "a boolean from a Graph lookup, not the input; same "
            "over-approximation.",
        "needs.m365.outputs.supports_email":
            "a boolean from a Graph lookup, not the input; same "
            "over-approximation.",
    },
    "102-domain-add-ffc-cloudflare-and-whmcs.yml": {
        "steps.enforce.outputs.out_dir":
            "a $RUNNER_TEMP path, constant; tainted because the enforce step "
            "holds the resolved domain. #1234 closed 102's real hop — this is "
            "the residue of the pessimistic rule, not a live one.",
        "needs.whmcs_preflight.outputs.found":
            "'true'/'false' from a WHMCS lookup, not the input. Frozen rather "
            "than waived because the SINK is a pwsh double-quoted "
            "interpolation on `whmcs-prod`, so the shape is one value-change "
            "away from live.",
        "needs.cloudflare_enforce_standard.outputs.issues_count":
            "a count from the audit, not the input; sink is a single-quoted JS "
            "literal in github-script.",
    },
    "103-enforce-domain-standard.yml": {
        "steps.enforce.outputs.out_dir":
            "a $RUNNER_TEMP path, constant; same over-approximation as 102's.",
        "needs.cloudflare_enforce.outputs.issues_count":
            "a count from the audit, not the input; sink is a single-quoted JS "
            "literal in github-script.",
    },
    # 702-ffc-ex-clone-deploy.yml was here — burned down in #1241 lane 1. All
    # three references (`needs.preflight.outputs.repo_name`,
    # `steps.target.outputs.repo`, `steps.target.outputs.branch`) now reach
    # their bodies through step-level `env:` with a fail-closed emptiness check,
    # so no `${{ … }}` for them is substituted into script text. The job entered
    # `github-prod` with the Key Vault PAT already exported to `GITHUB_ENV`,
    # which is the credential an injected body would have held.
    # 706-website-wordpress-to-pages.yml was here — burned down in #1241 lane
    # 2. All three references (`needs.resolve.outputs.{domain,repo,mode}`) now
    # reach the two `convert` bodies through step-level `env:` with a
    # fail-closed emptiness check, so no `${{ … }}` for them is substituted
    # into script text. `convert` declares no environment and holds only the
    # workflow's `contents: read` token — but it is the job that captures the
    # live charity site and produces the artifact `deliver` publishes on
    # `github-prod`, so a body injected here writes the tree a human is later
    # asked to approve.
}


REMEDY = """\
Remedy — the same one the sibling guard prescribes, applied at the READ:

    - name: Use the resolved value
      env:
        IN_DOMAIN_RESOLVED: ${{ steps.meta.outputs.domain }}   # not in the body
      run: |
        if ([string]::IsNullOrWhiteSpace($env:IN_DOMAIN_RESOLVED)) {
          Write-Output '::error::IN_DOMAIN_RESOLVED is empty'; exit 1
        }
        $domain = $env:IN_DOMAIN_RESOLVED

The `env:` mapping is evaluated by the expression engine and arrives as an
environment variable, so the value is data rather than source code. Add the
empty check with it: an env-mapped value that is empty is not an empty argument
to a NATIVE command, it is NO argument, and every later argument binds one
position to the left (ledger L214, and `scripts/check-workflow-empty-input-guard.py`).

Fixing the WRITE end instead — sanitising before `GITHUB_OUTPUT` — is not a
remedy. `.Trim().ToLowerInvariant().Trim('.')` removes nothing that matters, and
on a `windows-latest` job `$env:` lookup is case-insensitive, so a lowercase
payload costs the dispatcher nothing (#1233)."""


def compare(current: dict[str, tuple[str, ...]]):
    """(new, stale) — the freeze asserted in BOTH directions.

    Both halves are per-REFERENCE, not per-file. A file-level check would let a
    workflow that is already in the freeze acquire a brand new hop silently,
    which is the same hole one level up as the one this guard exists to close.
    """
    new: list[str] = []
    stale: list[str] = []
    for workflow, references in current.items():
        frozen = KNOWN_LAUNDERED.get(workflow, {})
        for reference in references:
            if reference not in frozen:
                new.append(f"{workflow}: {reference}")
    for workflow, frozen in KNOWN_LAUNDERED.items():
        references = current.get(workflow, ())
        for reference in frozen:
            if reference not in references:
                stale.append(
                    f"{workflow}: {reference} no longer reaches a script body"
                )
    return sorted(new), sorted(stale)


def _write_workflows(current: dict[str, tuple[str, ...]]) -> list[str]:
    names = []
    for workflow in current:
        try:
            parsed = yaml.safe_load(
                (WORKFLOWS / workflow).read_text(encoding="utf-8")
            )
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(parsed, dict) and any(
            is_write_environment(e) for e in environments(parsed)
        ):
            names.append(workflow)
    return names


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("usage: check-workflow-laundered-input-interpolation.py")
        return 2

    hops, unreadable, scanned = scan_all()

    if unreadable:
        print(
            f"laundered dispatch-input guard: {len(unreadable)} file(s) unreadable\n"
        )
        for problem in unreadable:
            print(f"  {problem}")
        print(
            "\nThis guard fails closed: a workflow it cannot parse is a finding, "
            "because an unreadable file is exactly the blind spot it exists to close."
        )
        return 1

    current = current_map(hops)
    new, stale = compare(current)

    if new or stale:
        if new:
            print(
                f"free-text dispatch inputs newly reaching a script body through a "
                f"step or job output: {len(new)}\n"
            )
            for item in new:
                print(f"  {item}")
            print()
            print(REMEDY)
        if stale:
            if new:
                print()
            print(f"stale KNOWN_LAUNDERED entries: {len(stale)}\n")
            for item in stale:
                print(f"  {item}")
            print(
                "\nA hop was closed without deleting its freeze entry. Remove it in "
                "the same PR, or the list stops describing the tree and the next "
                "reader cannot tell what is left to burn down."
            )
        return 1

    write = _write_workflows(current)
    # The denominator is printed in both directions on purpose. A survey that
    # returns an empty set needs its denominator visible to be worth anything —
    # "0 laundering workflows" and "the scan saw nothing" render identically
    # otherwise (ledger L62/L92, and #966's expected=N board=M).
    print(
        f"laundered dispatch-input interpolation OK: {scanned} workflow files "
        f"scanned; {len(current)} launder a free-text dispatch input through a step "
        f"or job output into a script body ({len(hops)} call sites), of which "
        f"{len(write)} enter a write environment. All are in the KNOWN_LAUNDERED "
        f"freeze and no entry is stale.\n"
        f"This is the hop `check-workflow-input-interpolation.py` cannot see: it is "
        f"defined over `inputs.X`, and moving the input into `env:` — the remedy — "
        f"is what removes the reference it reads. The value is still substituted "
        f"into script text at the far end (#1233).\n"
        f"Not judged: composite actions under .github/actions/ (their own outputs "
        f"are a different context), reusable-workflow `workflow_call` inputs, and a "
        f"third-party `uses:` whose source is not in this tree."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
