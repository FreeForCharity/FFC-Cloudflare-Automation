#!/usr/bin/env python3
r"""Guard: a free-text `workflow_dispatch` input must not be interpolated into a
script body (#1080).

GitHub substitutes `${{ }}` into the script **text** before any shell or
PowerShell or JS parser sees it. So an input value is not data arriving in a
variable — it is source code pasted into the middle of a program. Two live
instances in `720-create-repo.yml`, reproduced locally before #1080 was filed:

    Description = "${{ inputs.Description }}"          # :193, pwsh
    $ Description = "$(Write-Host 'INJECTED-PWSH')"
    INJECTED-PWSH                                      <- executed

    repo_input="${{ inputs.RepoName }}"                # :274, bash
    $ RepoName='$(echo INJECTED-BASH)'
    repo_input=[INJECTED-BASH]

Neither needs a quote to break out: double-quoted `$( )` runs in both shells.

WHY THIS IS A FINDING EVEN THOUGH DISPATCH REQUIRES WRITE ACCESS
    The usual dismissal — "only someone with write access can dispatch, and they
    could edit the workflow anyway" — does not hold for a **gated** workflow, and
    that is the whole point.

    `environment:` approval is a trust boundary between the DISPATCHER and the
    APPROVER. The approver sees the run name, the input values and the workflow's
    committed source, then decides whether to spend a production credential.
    An interpolated input is code that runs AFTER that approval, inside the job,
    holding `cloudflare-prod-write` / `whmcs-prod` / `github-prod` / `m365-prod`
    credentials the dispatcher never had. Editing the workflow instead would show
    up in the diff the approver reads. An injected input never appears in the
    repository at all.

    That is also why findings are ordered by environment, not by file.

RELATIONSHIP TO tests/workflow-logic/test_no_untrusted_expression_in_run.py
    That module bans `${{ }}` in a `run:` of a workflow reachable from an
    UNTRUSTED EVENT (an issue body, a comment, a fork's PR). It deliberately
    carves dispatch-only workflows out — "no outsider can fire it" — and one of
    its clean samples asserts that `${{ inputs.domain }}` in a `run:` is NOT a
    finding there.

    This guard covers exactly the set that one declines to cover, for the
    different reason above. The two do not overlap and neither weakens the other:
    a workflow with an untrusted trigger is that module's business, and the rule
    there is absolute (no expression at all); the rule here is narrower (only
    free-text dispatch inputs), because a dispatch-only workflow interpolating
    `github.run_id` is not an injection risk.

WHICH INPUT TYPES COUNT AS FREE TEXT
    `boolean` and `choice` are excluded: GitHub constrains both to a value it
    generated, so neither can carry a payload. Everything else counts, including
    `number` — and that is a deliberate widening of #1080's own table, which
    counted `type: string` only. It is the fail-safe direction and it is not
    hypothetical here:

        101-domain-status.yml:684   const issueNumber = Number('${{ inputs.issue_number }}');

    `issue_number` is `type: number`, sitting in a SINGLE-quoted JS literal in a
    `github-script` step, in a workflow that enters `m365-prod`. If GitHub does
    not validate the type, an apostrophe ends the literal and the rest is JS with
    the job's credentials. GitHub's own documentation states that `choice`
    "resolves to a string" and says nothing equivalent about `number`, and this
    repo's CLAUDE.md records that the dispatch API requires every input to be
    SENT as a string (`issue_number: 609` is rejected 422; `"609"` is accepted).
    Absent a documented constraint, an unconstrained-by-default reading is the
    only safe one — so the type set is a DENYLIST of the two provably-safe types,
    and any type nobody has classified (a future `environment`, a typo) is
    treated as free text rather than waved through.

WHAT IS NOT A FINDING
    Expressions in `env:`, `with:`, `if:` and `name:` are untouched. The first is
    the REMEDY — `env: FOO: ${{ inputs.foo }}` then `"$FOO"` — and a guard that
    flagged it would have to be switched off to land the fix for the thing it
    guards. `if:` is evaluated by the expression engine and never becomes script
    text.

FAIL CLOSED
    A workflow whose YAML will not parse is a finding, never a silent skip — an
    unreadable file is exactly the blind spot this exists to close.

THE FREEZE
    `KNOWN_UNGUARDED` pins the call sites that already exist, asserted EXACTLY in
    both directions: a workflow (or a newly-interpolated input in an already
    listed workflow) that is not in the list fails the run, and an entry that no
    longer matches anything ALSO fails it, so the burn-down cannot leave the list
    lying. Fixing a call site therefore requires deleting its entry in the same
    PR. The list is a freeze on new instances, not an endorsement of the old ones.

Exit codes: 0 = the interpolation set is exactly the frozen one, 1 = a new
instance, a stale entry, or a file that could not be read.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# The only two `workflow_dispatch` input types GitHub constrains to a value it
# generated itself. A denylist, not an allowlist: see the module docstring —
# an unrecognised type must read as free text, because the failure direction of
# guessing wrong is an unguarded injection.
CONSTRAINED_TYPES = frozenset({"boolean", "choice"})

# `${{ … }}`, non-greedy, DOTALL because an expression may be wrapped across
# lines inside a block scalar.
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)

# A reference to a dispatch input ANYWHERE inside an expression, not only the
# bare `${{ inputs.x }}` spelling. `702-ffc-ex-clone-deploy.yml:271` is why:
#
#     ${{ inputs.exclude && format('--exclude {0}', inputs.exclude) || '' }}
#
# is a free-text input reaching the script text through a `format()`, and a
# pattern anchored on the bare spelling reads it as clean. #1080's own table
# missed this call site for exactly that reason.
#
# Whitespace is tolerated around the dots because the expression language allows
# it and a guard that can be evaded with a space is decoration.
_INPUT_REF = re.compile(
    r"\b(?:github\s*\.\s*event\s*\.\s*)?inputs\s*\.\s*([A-Za-z_][A-Za-z0-9_-]*)"
)


class WorkflowUnreadable(Exception):
    """The file could not be parsed. Always a finding — never a skip."""


def dispatch_inputs(workflow: dict) -> dict[str, str]:
    """Map every `workflow_dispatch` input name to its declared type.

    `on:` is the YAML 1.1 boolean `True` after `yaml.safe_load` (the Norway
    problem). Reading only the string key would silently return {} for every
    workflow and make this guard pass by inspecting nothing, so both spellings
    are checked and `test_the_scan_sees_real_workflows` pins the result.
    """
    on = workflow.get(True, workflow.get("on"))
    if not isinstance(on, dict):
        return {}
    dispatch = on.get("workflow_dispatch")
    if not isinstance(dispatch, dict):
        return {}
    inputs = dispatch.get("inputs")
    if not isinstance(inputs, dict):
        return {}
    types: dict[str, str] = {}
    for name, spec in inputs.items():
        declared = spec.get("type", "string") if isinstance(spec, dict) else "string"
        types[str(name)] = str(declared)
    return types


def free_text_inputs(workflow: dict) -> set[str]:
    """Dispatch inputs whose value the dispatcher chooses freely."""
    return {
        name
        for name, declared in dispatch_inputs(workflow).items()
        if declared not in CONSTRAINED_TYPES
    }


def environments(workflow: dict) -> set[str]:
    """Every `environment:` any job in the workflow enters.

    Accepts the three shapes the schema allows: a bare string, a mapping with a
    `name`, and a list of either.
    """
    found: set[str] = set()

    def add(value) -> None:
        if isinstance(value, str):
            found.add(value)
        elif isinstance(value, dict) and isinstance(value.get("name"), str):
            found.add(value["name"])
        elif isinstance(value, list):
            for item in value:
                add(item)

    for job in (workflow.get("jobs") or {}).values():
        if isinstance(job, dict):
            add(job.get("environment"))
    return found


def is_write_environment(name: str) -> bool:
    """A write lane is any environment not explicitly suffixed `-read`.

    Deliberately the pessimistic reading: a lane nobody marked read-only is
    treated as capable of writing, so a new environment is guarded by default
    rather than on the strength of its name.
    """
    return not name.endswith("-read")


def script_bodies(workflow: dict):
    """Yield (job_id, step_index, step_label, kind, body) for every script.

    The INDEX is what makes credential reachability answerable: "which
    credential is in this process" is a question about the steps BEFORE this one
    in the same job, and a label cannot be counted back from (two steps may
    share a name, and an unnamed one is labelled by its own position).

    Two kinds carry substituted text into a parser: a `run:` block, and the
    `script:` input of `actions/github-script`, whose body is JS. #1080's
    criterion 6 requires both; the `github-script` half is where 101's
    `Number('${{ inputs.issue_number }}')` lives.
    """
    for job_id, job in (workflow.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        for index, step in enumerate(job.get("steps") or []):
            if not isinstance(step, dict):
                continue
            label = str(step.get("name") or f"step {index}")
            if isinstance(step.get("run"), str):
                yield job_id, index, label, "run", step["run"]
            if "github-script" in str(step.get("uses") or ""):
                script = (step.get("with") or {}).get("script")
                if isinstance(script, str):
                    yield job_id, index, label, "github-script", script


def _line_of(raw: str, needle: str) -> int | None:
    """1-based line number of the first raw line containing `needle`, if any.

    Only ever used to make a finding easier to open. A miss degrades the message
    and never changes the verdict.
    """
    for number, line in enumerate(raw.splitlines(), start=1):
        if needle and needle in line:
            return number
    return None


class Finding:
    def __init__(self, workflow: str, job: str, step: str, kind: str,
                 input_name: str, expression: str, line: int | None,
                 step_index: int | None = None) -> None:
        self.workflow = workflow
        self.job = job
        self.step = step
        # Position of the step within its job, for credential reachability
        # (#1188). Optional so a hand-built Finding in a test stays cheap to
        # write; a Finding without it simply reports no reachability.
        self.step_index = step_index
        self.kind = kind
        self.input_name = input_name
        self.expression = expression
        self.line = line

    def __str__(self) -> str:
        where = f"{self.workflow}:{self.line}" if self.line else self.workflow
        return (
            f"{where} job '{self.job}' step '{self.step}' ({self.kind}) "
            f"interpolates free-text input '{self.input_name}' via "
            f"`${{{{{self.expression}}}}}`"
        )


def scan_workflow(path: pathlib.Path) -> list[Finding]:
    """Findings for one workflow file. Raises WorkflowUnreadable on bad input."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise WorkflowUnreadable(f"{path.name}: cannot be read ({error})") from error
    try:
        workflow = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise WorkflowUnreadable(f"{path.name}: YAML does not parse ({error})") from error
    if not isinstance(workflow, dict):
        raise WorkflowUnreadable(
            f"{path.name}: top level is {type(workflow).__name__}, not a mapping"
        )

    free_text = free_text_inputs(workflow)
    if not free_text:
        return []

    findings: list[Finding] = []
    for job_id, step_index, step, kind, body in script_bodies(workflow):
        for match in _EXPRESSION.finditer(body):
            expression = match.group(1)
            for name in _INPUT_REF.findall(expression):
                if name not in free_text:
                    continue
                anchor = match.group(0).splitlines()[0]
                findings.append(
                    Finding(path.name, job_id, step, kind, name, expression,
                            _line_of(raw, anchor), step_index)
                )
    return findings


def workflow_paths() -> list[pathlib.Path]:
    return sorted(
        list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml"))
    )


def scan_all(paths=None):
    """(findings, unreadable, scanned) across every workflow file."""
    findings: list[Finding] = []
    unreadable: list[str] = []
    paths = workflow_paths() if paths is None else list(paths)
    for path in paths:
        try:
            findings.extend(scan_workflow(path))
        except WorkflowUnreadable as error:
            unreadable.append(str(error))
    return findings, unreadable, len(paths)


def current_map(findings: list[Finding]) -> dict[str, tuple[str, ...]]:
    """workflow file -> the sorted free-text inputs it interpolates."""
    grouped: dict[str, set[str]] = {}
    for finding in findings:
        grouped.setdefault(finding.workflow, set()).add(finding.input_name)
    return {name: tuple(sorted(v)) for name, v in sorted(grouped.items())}


# --- credential reachability (#1188) ----------------------------------------
#
# WHY THIS EXISTS
#     Judging a call site means knowing WHICH CREDENTIAL the injected code would
#     hold. The two sweeps this repo recommends for that both read the workflow's
#     DECLARATIVE surface and nothing else:
#
#         1. read the injecting step's own `env:`   — ledger L213
#         2. grep the body for `secrets.`           — the sweep used on #1141
#
#     Neither can see a credential that an EARLIER STEP IN THE SAME JOB exported
#     through `$GITHUB_ENV`. That is not a hypothesis: the #1080 burn-down hit it
#     three times, each as a fresh per-lane surprise — lane 7 (`301`, the Graph
#     token), lane 9 (`103`, the Cloudflare tokens), lane 10 (`306`, #1187).
#
#     `306` is the shape that makes this worth mechanising: the Graph bearer
#     token it mints into `GITHUB_ENV` is the workflow's ONLY credential, so both
#     sweeps answer "this workflow holds nothing" rather than "part of it". Two
#     independent-looking checks agree on a clean answer, and they agree because
#     they read the same surface.
#
#     `docs/workflow-safety-and-approvals.md` has stated the principle since #834
#     / L45-4 — "once a broad org-scoped writer token is in GITHUB_ENV, the blast
#     radius is everything that token can reach". This is its implementation.
#
# WHAT IT DELIBERATELY DOES NOT DO
#     It adds INFORMATION to an existing finding. It never creates one, never
#     clears one, and never touches the exit code: a tree whose reachability is
#     empty exits exactly as it did before this landed (#1188 criterion 4).

# A name-based classifier, applied to env var names. Pessimistic on purpose, and
# for the same reason `is_write_environment` is: the cost of over-reporting is a
# reviewer reading one extra name, and the cost of under-reporting is the blind
# spot this exists to close. `IDENTIFIER` is in the list because the WHMCS API
# identifier is half of a credential PAIR — on its own it reads like metadata.
_CREDENTIAL_NAME = re.compile(
    r"(?:^|_)(?:PAT|TOKEN|SECRET|SECRETS|PASSWORD|PASSWD|CREDENTIAL|CREDENTIALS"
    r"|KEY|APIKEY|IDENTIFIER|CERT|PFX|BEARER|AUTH)(?:_|$)",
    re.IGNORECASE,
)

# A literal env-var assignment inside a quoted string: `"NAME=…"` (plain) or
# `"NAME<<delim"` (GitHub's heredoc-delimited form, which every `*-from-kv`
# composite action uses so a value carrying a newline cannot inject a second
# variable). The opening quote is REQUIRED, which is what keeps a dynamic write
# out: `Add-Content -Value "$Name<<$delim"` starts with `$` and is not a name
# this can know — it is recovered from the helper's CALL SITE instead, below.
_ENV_ASSIGN = re.compile(r"""["']([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|<<)""")

# `{ echo "NAME<<$d"; …; } >> "$GITHUB_ENV"` — the bash form used by 120, 701,
# 720 and friends. The closing brace must be followed by the redirect, which is
# what stops the non-greedy match from ending early on a `${d}` inside the block.
_BRACE_TO_GITHUB_ENV = re.compile(
    r"\{(.*?)\}\s*>>\s*[\"']?\$\{?GITHUB_ENV\}?[\"']?", re.DOTALL
)

# `- uses: ./.github/actions/<name>` — a composite action in THIS repo, whose
# steps run in the caller's job and whose `GITHUB_ENV` writes therefore land in
# the caller's later steps. A third-party `uses:` is not judged (see the report's
# closing "Not judged" line): its source is not in the tree.
_LOCAL_ACTION = re.compile(r"^\.{1,2}/(?:\./)?(\.github/actions/[A-Za-z0-9._-]+)/?$")

# A pwsh helper that writes to GITHUB_ENV, e.g. `Add-EnvVar`. Matched loosely on
# purpose: what matters is the brace-balanced body, extracted separately.
_PS_FUNCTION = re.compile(r"\bfunction\s+([A-Za-z][A-Za-z0-9_-]*)\s*\{")


def looks_like_credential(name: str, value: str | None = None) -> bool:
    """Would code running with this variable in its environment hold a secret?

    Two independent signals, either sufficient:

    * the VALUE references `secrets.` — definitive, and name-blind, so a
      credential mapped under an innocuous name is still caught;
    * the NAME matches `_CREDENTIAL_NAME` — the only signal available for a
      `GITHUB_ENV` export, whose value was computed at run time and is not in
      the file at all.
    """
    if value is not None and "secrets." in "".join(str(value).split()):
        return True
    return bool(_CREDENTIAL_NAME.search(name))


def _heredoc_bodies_to_github_env(body: str) -> list[str]:
    """Bodies of `cat <<EOF >> "$GITHUB_ENV"` blocks.

    Only entered for a line that mentions GITHUB_ENV and `<<` but carries NO
    literal assignment of its own. Without that second condition the pwsh line
    `"FRAUD_REVIEW_JSON<<FRAUD_EOF" | Out-File … $env:GITHUB_ENV` — a complete
    write, already handled — would be read as OPENING a heredoc and would then
    swallow the rest of the step.
    """
    lines = body.splitlines()
    bodies: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if "GITHUB_ENV" in line and "<<" in line and not _ENV_ASSIGN.search(line):
            opener = re.search(r"<<-?\s*[\"']?([A-Za-z_][A-Za-z0-9_]*)[\"']?", line)
            if opener:
                delimiter = opener.group(1)
                collected: list[str] = []
                index += 1
                while index < len(lines) and lines[index].strip() != delimiter:
                    collected.append(lines[index])
                    index += 1
                bodies.append("\n".join(collected))
        index += 1
    return bodies


def _brace_balanced_body(text: str, open_at: int) -> str:
    """The `{ … }` body starting at `open_at`, or the tail if it never closes."""
    depth = 0
    for position in range(open_at, len(text)):
        if text[position] == "{":
            depth += 1
        elif text[position] == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:position]
    return text[open_at:]


def _helper_exported_names(body: str) -> set[str]:
    """Names passed as `-Name 'X'` to a pwsh helper that writes to GITHUB_ENV.

    The `*-from-kv` composite actions all funnel their writes through one
    `Add-EnvVar` whose `$Name` is a parameter, so the literal name exists only at
    the CALL SITE. Resolved per-function rather than by sweeping every `-Name` in
    the body, so a helper that does NOT touch GITHUB_ENV contributes nothing.
    """
    names: set[str] = set()
    for match in _PS_FUNCTION.finditer(body):
        function = match.group(1)
        definition = _brace_balanced_body(body, match.end() - 1)
        if "GITHUB_ENV" not in definition:
            continue
        for call in re.finditer(
            rf"\b{re.escape(function)}\b[^\r\n]*?-Name\s+[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']",
            body,
        ):
            names.add(call.group(1))
    return names


def exported_env_names(body: str) -> set[str]:
    """Every env var name a script body writes to `$GITHUB_ENV`.

    Four shapes, all live in this repo:

      pwsh direct    `"GRAPH_ACCESS_TOKEN=$token" | Out-File … $env:GITHUB_ENV`
      pwsh heredoc   `"FRAUD_REVIEW_JSON<<EOF"    | Out-File … $env:GITHUB_ENV`
      bash group     `{ echo "GH_TOKEN<<$d"; … } >> "$GITHUB_ENV"`
      pwsh helper    `Add-EnvVar -Name 'WHMCS_API_SECRET' -Value $secret`
    """
    if "GITHUB_ENV" not in body:
        return set()
    names: set[str] = set()
    for line in body.splitlines():
        if "GITHUB_ENV" in line:
            names |= {m.group(1) for m in _ENV_ASSIGN.finditer(line)}
    for block in _BRACE_TO_GITHUB_ENV.findall(body):
        names |= {m.group(1) for m in _ENV_ASSIGN.finditer(block)}
    for block in _heredoc_bodies_to_github_env(body):
        for line in block.splitlines():
            assignment = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
            if assignment:
                names.add(assignment.group(1))
    names |= _helper_exported_names(body)
    return names


def _local_action_steps(uses: str, depth: int = 0) -> list[dict]:
    """The steps of a LOCAL composite action, or `[]` if it is not one.

    Shared by both resolvers below, which ask the same question of the same file
    for two different reasons: what it EXPORTS, and what it ACQUIRES. Splitting
    it means a third-party `uses:`, an unreadable file and a non-composite
    action are refused in one place and in one way — a resolver that answered
    "nothing" for a file it could not read while the other answered from a stale
    copy would be the worst of both.
    """
    if depth > 2:
        return []
    match = _LOCAL_ACTION.match(str(uses).strip())
    if not match:
        return []
    directory = REPO_ROOT / match.group(1)
    for candidate in ("action.yml", "action.yaml"):
        path = directory / candidate
        if not path.exists():
            continue
        try:
            action = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return []
        if not isinstance(action, dict):
            return []
        return [
            step
            for step in ((action.get("runs") or {}).get("steps") or [])
            if isinstance(step, dict)
        ]
    return []


def action_exported_env_names(uses: str, depth: int = 0) -> set[str]:
    """Env names a LOCAL composite action exports into the caller's job.

    A composite action's steps run in the calling job, so its `GITHUB_ENV` writes
    are in reach of every later step of the CALLER — which is exactly how the
    Cloudflare and WHMCS credentials arrive in workflows whose files contain no
    `secrets.` reference at all.
    """
    names: set[str] = set()
    for step in _local_action_steps(uses, depth):
        names |= step_exported_env_names(step, depth + 1)
    return names


def step_exported_env_names(step: dict, depth: int = 0) -> set[str]:
    """Env names one step exports, whether it is a `run:` or a local action."""
    names: set[str] = set()
    if isinstance(step.get("run"), str):
        names |= exported_env_names(step["run"])
    if isinstance(step.get("uses"), str):
        names |= action_exported_env_names(step["uses"], depth)
    return names


# --- credentials a step ACQUIRES rather than is handed (#1208) ---------------
#
# WHY THIS EXISTS
#     Everything above indexes ARRIVAL IN A VARIABLE — a name in the step's own
#     `env:`, a name in the job's, a name written to `$GITHUB_ENV`. That is the
#     right index for a credential somebody mapped in, and it is blind to the
#     shape a GATED job reaches for first: an OIDC login that leaves an
#     AUTHENTICATED CLI SESSION on disk (`~/.azure`, or `$AZURE_CONFIG_DIR`).
#     Nothing lands in any `env:`, nothing is written to `GITHUB_ENV`, and no
#     `secrets.` name is in reach of the body — so all three sweeps this repo
#     recommends score the step as holding nothing, while the shipped body one
#     line below runs
#
#         az account get-access-token --resource-type ms-graph …
#
#     and gets a Graph bearer token. An injected body does not need to READ
#     anything. It runs `az` itself, exactly as the shipped body does.
#
#     Measured on `main` at `b7a9f2b` (#1208): `--reachability
#     101-domain-status.yml` named 101's two `cloudflare` steps and OMITTED both
#     `m365` steps — and the `m365` job is `environment: m365-prod`, the only
#     reason 101 was a `[W]` entry in the #1080 freeze at all. Every available
#     sweep scored the two sites carrying the write environment as empty.
#
#     Why that is worth mechanising rather than remembering: the empty answer is
#     the reassuring one AND it was the mechanical one, so it is the one that
#     gets trusted. An absent line and an empty line read identically to an
#     operator and mean very different things.
#
# WHAT IT DELIBERATELY DOES NOT DO
#     It does not become a finding. Like the rest of this section it is a
#     READING: it never creates or clears a finding and never touches the exit
#     code (#1188 criterion 4, restated by #1208).

# `uses:` references known to leave an authenticated session behind for every
# LATER step of the caller's job. First match wins, so the generic `*/…login…@`
# row sits last: it is the catch-all that keeps an unfamiliar login action from
# reading as "no credential" — the failure this whole block exists to end.
#
# Third-party sources are not in this tree, so this is a NAME-based judgement in
# a file that otherwise refuses to guess at a third-party `uses:`
# (`action_exported_env_names` returns nothing for one, deliberately). The
# asymmetry is the point and it runs the safe way: naming a session that turns
# out to be unused costs a reviewer one line, and the opposite error is #1208.
#
# WHAT DOES *NOT* BELONG HERE, AND THE LINE IT DRAWS
#     Every row above leaves state a later step inherits with no mapping of any
#     kind — `~/.azure`, a credentials file plus `GOOGLE_APPLICATION_CREDENTIALS`,
#     the `AWS_*` job env, `~/.docker/config.json`. That is what makes "in the
#     process environment of the later step" true of them.
#
#     `actions/create-github-app-token@…` is NOT one, and #1208's proposal listed
#     it with the qualifier "where the token is consumed from a step output".
#     It hands back `steps.<id>.outputs.token` and leaves nothing ambient, so
#     reporting it here would assert the one thing this whole section exists to
#     get right — the ARRIVAL PATH — incorrectly, in the flattering-looking
#     direction of a longer list. Neither of its real shapes is lost by the
#     omission: mapped into a later `env:` it is already reported as `step env:`
#     (the name matches `_CREDENTIAL_NAME`), and interpolated straight into a
#     body it is not "in reach" at all but literally pasted into the source,
#     which is a #1080 finding rather than a reachability line. Resolving the
#     output-consumption case properly means indexing `steps.<id>.outputs`
#     references, which nothing here does yet. Raised by review on #1219.
_ACQUIRING_ACTIONS = (
    (re.compile(r"^azure/login@"), "az CLI session"),
    (re.compile(r"^google-github-actions/auth@"), "gcloud CLI session"),
    (re.compile(r"^aws-actions/configure-aws-credentials@"), "aws CLI session"),
    (re.compile(r"^docker/login-action@"), "docker registry session"),
    (re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]*login[A-Za-z0-9._-]*@"), "CLI session"),
)

# The same acquisition performed by a `run:` body instead of an action. The
# lookbehind keeps `pwsh-az login` and a path ending in `/az` out; a mention
# inside a comment or an error string is NOT excluded, on the over-report side
# of the same trade as above.
_ACQUIRING_COMMANDS = (
    (re.compile(r"(?<![\w./-])az\s+login(?![\w-])"), "az CLI session"),
    (re.compile(r"(?<![\w./-])gh\s+auth\s+login(?![\w-])"), "gh CLI session"),
    (re.compile(r"(?<![\w./-])docker\s+login(?![\w-])"), "docker registry session"),
    (
        re.compile(r"(?<![\w./-])gcloud\s+auth\s+(?:login|activate-service-account)(?![\w-])"),
        "gcloud CLI session",
    ),
)


def uses_acquired_sessions(uses: str) -> set[str]:
    """The session a `uses:` reference is known BY NAME to leave behind."""
    reference = str(uses).strip()
    for pattern, label in _ACQUIRING_ACTIONS:
        if pattern.match(reference):
            return {label}
    return set()


def action_acquired_sessions(uses: str, depth: int = 0) -> set[str]:
    """Sessions a LOCAL composite action leaves behind in the caller's job.

    This is not a hypothetical extension of the direct case: all five
    `*-from-kv` actions in `.github/actions/` open with `azure/login@…` and then
    read Key Vault, and their steps run in the CALLER's job. So every workflow
    that fetches a credential through one of them also hands every later step in
    that job an `az` session — which is strictly broader than the two or three
    names the action exports, because the session can `az keyvault secret show`
    anything that identity may read.
    """
    acquired: set[str] = set()
    for step in _local_action_steps(uses, depth):
        acquired |= step_acquired_sessions(step, depth + 1)
    return acquired


def step_acquired_sessions(step: dict, depth: int = 0) -> set[str]:
    """Sessions one step leaves behind for LATER steps in the same job."""
    acquired: set[str] = set()
    run = step.get("run")
    if isinstance(run, str):
        for pattern, label in _ACQUIRING_COMMANDS:
            if pattern.search(run):
                acquired.add(label)
    uses = step.get("uses")
    if isinstance(uses, str):
        acquired |= uses_acquired_sessions(uses)
        acquired |= action_acquired_sessions(uses, depth)
    return acquired


class Reach:
    """One credential in reach at a step, and HOW it got there.

    The arrival path is the point, not a decoration. A bare list of names
    rebuilds the same blind spot one layer up: `GH_TOKEN` in the injecting step's
    own `env:` is visible to anyone who opens the file, and `GH_TOKEN` exported
    by an earlier step is precisely what nobody sees.
    """

    STEP_ENV = "step env:"
    JOB_ENV = "job env:"
    GITHUB_ENV = "GITHUB_ENV"
    ACQUIRED = "acquired"

    def __init__(self, name: str, arrival: str, source: str | None = None) -> None:
        self.name = name
        self.arrival = arrival
        self.source = source

    @property
    def hidden(self) -> bool:
        """True when neither recommended sweep can see it (#1188, #1208).

        Both paths are invisible to an L213 read of the step's `env:` and to a
        `secrets.` grep of the file. `ACQUIRED` is the more thorough of the two:
        a `GITHUB_ENV` arrival at least has a NAME somewhere in the tree, and a
        CLI session has no textual trace at the call site at all.
        """
        return self.arrival in (self.GITHUB_ENV, self.ACQUIRED)

    def _key(self):
        return (self.name, self.arrival, self.source)

    def __eq__(self, other) -> bool:
        return isinstance(other, Reach) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def __str__(self) -> str:
        if self.arrival == self.GITHUB_ENV:
            return f"{self.name} (GITHUB_ENV from step '{self.source}')"
        if self.arrival == self.ACQUIRED:
            return f"{self.name} (acquired by step '{self.source}'; no variable in reach)"
        return f"{self.name} ({self.arrival})"


def _exporter_label(step: dict, index: int) -> str:
    """How to NAME the step a credential arrived from.

    `script_bodies` labels an unnamed step `step N`, which is fine for pointing
    at a finding and useless as an attribution: the steps that export
    credentials here are overwhelmingly the unnamed
    `- uses: ./.github/actions/cloudflare-tokens-from-kv` one-liners, and
    "GITHUB_ENV from step 'step 1'" tells a reader to go count steps. Prefer the
    action path, which is the thing they would then have to look up anyway.
    """
    name = step.get("name")
    if isinstance(name, str) and name.strip():
        return name
    uses = step.get("uses")
    if isinstance(uses, str) and uses.strip():
        return f"uses: {uses.strip()}"
    return f"step {index}"


def credentials_in_reach(workflow: dict, job_id: str, step_index: int) -> list[Reach]:
    """Credentials in the process environment of `steps[step_index]` of `job_id`.

    Four arrival paths, resolved in the order a runner applies them:

      job env:     a `env:` on the job — in reach of every step in it
      GITHUB_ENV   written by an EARLIER step (or a local composite action it
                   calls); the step's own writes are not in reach of itself
      acquired     an authenticated CLI session an EARLIER step logged in and
                   left on disk — no variable anywhere, so no sweep defined over
                   variables can see it (#1208)
      step env:    the step's own `env:` — ledger L213's surface

    Deliberately conservative about `if:`: a step guarded by a condition still
    counts as an exporter, because whether it ran is a run-time fact and this is
    a static reading. Over-reporting here costs a reviewer one line; the opposite
    error is the one that has been made three times.
    """
    job = ((workflow.get("jobs") or {}).get(job_id)) or {}
    if not isinstance(job, dict):
        return []
    reached: list[Reach] = []

    for name, value in (job.get("env") or {}).items():
        if looks_like_credential(str(name), value):
            reached.append(Reach(str(name), Reach.JOB_ENV))

    steps = job.get("steps") or []
    for index, step in enumerate(steps[:step_index]):
        if not isinstance(step, dict):
            continue
        label = _exporter_label(step, index)
        for name in sorted(step_exported_env_names(step)):
            if looks_like_credential(name):
                reached.append(Reach(name, Reach.GITHUB_ENV, label))
        # An acquired session is not name-classified: `looks_like_credential`
        # judges an env-var NAME, and there is no variable here to judge. What
        # makes it a credential is the login, which is the thing matched.
        for session in sorted(step_acquired_sessions(step)):
            reached.append(Reach(session, Reach.ACQUIRED, label))

    if 0 <= step_index < len(steps) and isinstance(steps[step_index], dict):
        for name, value in (steps[step_index].get("env") or {}).items():
            if looks_like_credential(str(name), value):
                reached.append(Reach(str(name), Reach.STEP_ENV))

    return sorted(reached, key=lambda r: (r.name, r.arrival, r.source or ""))


# --- the freeze -------------------------------------------------------------
#
# Every call site that already existed when this guard landed, as
# `workflow file -> the free-text inputs it interpolates`. Asserted EXACTLY in
# both directions by `compare()`.
#
# `[W]` marks a workflow that enters at least one environment not suffixed
# `-read`, i.e. one where the injected code would run holding a production
# write credential the dispatcher could not otherwise reach. Burn this list down
# in that order.
#
# RECONCILIATION WITH #1080's TABLE (34 workflows / 20 write, dated 2026-08-05).
# This list is 36 / 21, and every one of the two extra workflows is accounted
# for — the guard was not widened by accident:
#
#   +1 write  229-whmcs-client-field-populate.yml — #825 landed 2026-08-05
#             (cee6630), after the table was written. #1080 says in as many
#             words: "add 229 to the allowlist when #825 lands".
#   +1 read   115-domain-transfer-preflight.yml — all three of its interpolated
#             inputs are `type: number`, so a string-only count could not see
#             it. See the docstring for why `number` counts here.
#
# Two entries also carry more inputs than the table lists, without changing the
# workflow count:
#   101 / 102 / 103  +issue_number   `type: number`, same reason as 115.
#   702              +exclude        `type: string`, and simply missed by a
#                    pattern anchored on the bare `${{ inputs.x }}` spelling:
#                    it reaches the script through
#                    `${{ inputs.exclude && format('--exclude {0}', …) }}`.
KNOWN_UNGUARDED: dict[str, tuple[str, ...]] = {
    # [W] --- Cloudflare / domain -------------------------------------------
    # 101-domain-status.yml burned down: `domain` now reaches all FIVE pwsh bodies
    # and `issue_number` the `github-script` body through step-level `env:`. Six
    # call sites in FOUR jobs off one dispatch - the widest entry left after 103.
    #
    # It is the lane where THIS TOOL's own answer was the misleading one. Asked
    # `--reachability 101-domain-status.yml`, it named two steps, both in the
    # `cloudflare` job, and reported nothing at the two `m365` sites - which are
    # the sites carrying `m365-prod`, i.e. the only reason 101 was a [W] entry at
    # all. An L213 read of their `env:` and a `secrets.` grep agree with it: all
    # three sweeps score them as holding nothing.
    #
    # They hold a Microsoft Graph credential. The preceding step is
    # `azure/login@v3` (OIDC) and both bodies then call
    # `az account get-access-token`, so the credential is an authenticated CLI
    # SESSION ON DISK rather than a value in a variable - and every sweep the
    # burn-down has built, this index included, is defined over variables.
    # Measured against the shipped body with every credential variable removed
    # from the environment: the payload ran `az` itself, wrote a Graph bearer
    # token to a file, then invoked m365-domain-preflight.ps1 with a legal
    # `Domain=[ffcworkingsite1.org]`, and the step exited 0.
    #
    # Tracked as its own issue rather than papered over here: the next lane will
    # read the same listing and draw the same conclusion. `dmarc_mgmt_debug`
    # stays interpolated and is NOT a finding - it is `type: boolean`, which is
    # the whole of what makes it safe, so
    # test_101_domain_status_wiring.py pins the declaration rather than
    # leaving it as a reading of this comment.
    # 102-domain-add-ffc-cloudflare-and-whmcs.yml burned down: `domain` reaches both
    # pwsh and both bash bodies through step-level `env:`, and `domain` +
    # `issue_number` reach the `github-script` body through it too. It spans FOUR
    # jobs under THREE environments -- whmcs-prod twice, cloudflare-prod-write
    # twice, cloudflare-prod-read once -- so one payload in one dispatch ran under
    # two production credential families on a single approval.
    #
    # It is the lane that showed the freeze entry is not the whole call site. The
    # four `inputs.` sites this list named are the FIRST hop; `inputs.domain` is
    # then published as a step output and re-interpolated into SEVEN more script
    # bodies, and `.Trim().ToLowerInvariant().Trim('.')` removes nothing that
    # matters. Measured: with the `Metadata` step remedied the house way, the
    # payload correctly arrived as data there, was written verbatim into
    # GITHUB_OUTPUT, and executed one step later at
    # `$domain = "${{ steps.meta.outputs.domain }}"` -- WHMCS secret stolen,
    # `-Domain` still bound to a legal `example.org`, step exited 0. A lane that
    # fixed only the sites THIS GUARD can see would have removed the entry, gone
    # green, and left the injection working. Every hop therefore travels through
    # `env:`. The guard is defined over `inputs.X` and is blind to the laundering by
    # construction: filed as #1233, not papered over here. Ledger L255.
    #
    # `zone_type` (choice) and `jump_start` / `enforce_dry_run` /
    # `dmarc_mgmt_debug` / `update_whmcs_nameservers` / `require_whmcs_domain`
    # (boolean) stay interpolated and are NOT findings.
    # 103-enforce-domain-standard.yml burned down: `domain` now reaches all four
    # pwsh bodies through step-level `env:`, and `domain` + `issue_number` reach
    # the `github-script` body through it too. It is the WIDEST entry in the
    # freeze by blast radius: one input, SIX call sites, FIVE jobs, and TWO
    # distinct gated write environments — cloudflare-prod-write twice
    # (`cloudflare_enforce`, `cloudflare_set_dkim`) and m365-prod twice
    # (`exo_check`, `exo_enable`) — so one payload in one dispatch ran four times
    # under two production credentials, on a single approval.
    #
    # It is also the first lane in the burn-down with a `github-script` site, and
    # the first where the credential is reachable BOTH ways: the Cloudflare jobs
    # hide their tokens in GITHUB_ENV, while both m365-prod steps name
    # FFC_EXO_CERT_PFX_BASE64 / FFC_EXO_CERT_PASSWORD in the SAME step's `env:`
    # as the injection point (ledger L213).
    #
    # That contrast is no longer carried by this comment: it is what
    # `--reachability 103-enforce-domain-standard.yml` prints, per step, with the
    # exporting step named (#1188). The MEASURED detail above stays because it is
    # evidence — which token, which account, which environment, on one approval.
    #
    # `issue_number` is `type: number` and was moved for the same reason as
    # `domain`, not as a courtesy: `number` is NOT in CONSTRAINED_TYPES because
    # GitHub does not validate it — the dispatch form renders a free text box and
    # substitutes the value verbatim. `dry_run`, `github_pages_only`,
    # `skip_m365` and `dmarc_mgmt_debug` remain interpolated and are NOT findings:
    # all four are `type: boolean`.
    "107-audit-compliance.yml": ("domain",),
    "109-dns-export-all-records.yml": ("domain",),
    # 110-cloudflare-zone-create.yml burned down: `domain` now reaches the pwsh body
    # through step-level `env:`, at both of the step's invocation branches. It is the
    # first lane whose injection point sat in a process holding WRITE-scoped
    # Cloudflare tokens for BOTH accounts — `cloudflare-tokens-from-kv` with
    # `scope: write` exports `CLOUDFLARE_API_TOKEN_FFC` / `_CM` through GITHUB_ENV, so
    # the credential in reach appears in no `env:` block in the file. That last
    # sentence is now mechanical rather than remembered:
    # `--reachability 110-cloudflare-zone-create.yml` names both tokens and the
    # composite-action step they arrive from (#1188). Its `account`
    # and `zone_type` remain interpolated and are NOT a finding: both are
    # `type: choice`, so GitHub constrains the value.
    # 112-dns-bulk-replace-a-ip.yml burned down: `old_ip` / `new_ip` now reach the
    # pwsh body through step-level `env:`. It rewrites A records across every zone in
    # both Cloudflare accounts on cloudflare-prod-write, and the callee's
    # `[ValidatePattern]` on both parameters could never have stopped a payload — it
    # runs after the injected expression, so its rejection message reads like a
    # control working on a run where the code had already executed.
    "115-domain-transfer-preflight.yml": (
        "issue_number",
        "min_days_to_expiry",
        "post_reg_lock_days",
    ),
    "116-domain-transfer-epp-probe.yml": ("domain",),
    "117-domain-transfer-verify.yml": ("domain",),
    # 118-whmcs-domain-lock.yml burned down: `domain` now reaches the one pwsh body
    # through step-level `env:` (IN_DOMAIN). It runs on whmcs-prod and changes a
    # registrar lock on a production domain; the WHMCS credential in reach of the
    # injection point is the workflow's ONLY credential and arrives through
    # GITHUB_ENV from `whmcs-secrets-from-kv`, so neither an L213 `env:` read nor a
    # `secrets.` grep of the file sees it.
    #
    # Its fail-closed guard closes a SECOND defect the burn-down surfaced rather
    # than one it created: the body splats a HASHTABLE onto a native `pwsh -File`,
    # and hashtable enumeration order is undefined, so a blank `Domain` was dropped
    # from the rendered arguments and `-Domain` swallowed the next token. Measured
    # on the pre-fix body with the dispatch box blanked, 3 of 8 runs bound
    # `Domain=[-DryRun:True]` with `DryRun=[False]` and exited 0; the other 5 failed
    # loudly on the mandatory parameter. Same input, same commit, two outcomes.
    # Ledger L254.
    # 119-bulk-staging-cname-github-pages.yml burned down: `domains` / `target` now
    # reach the pwsh body through step-level `env:`. It writes `staging.<domain>` in
    # every FFC-EX zone across both Cloudflare accounts on cloudflare-prod-write,
    # with the write-scoped tokens arriving through GITHUB_ENV from
    # `cloudflare-tokens-from-kv` — see `--reachability` for the path (#1188).
    #
    # The lane the OTHER lanes' remedy does not fit: `domains` is `required: false`
    # with an empty default and a documented fallback to a 13-domain list, so a
    # fail-closed guard on empty would break the common path. It keeps the fallback
    # (a "default fill" guard, #1150) and omits `Target` from the splat when blank
    # rather than passing an empty string, because the callee already resolves the
    # canonical Pages host (#778) and a second copy of it would drift. `dry_run`
    # stays interpolated and is NOT a finding: `type: boolean`.
    # 120-bulk-cutover-to-github-pages.yml burned down: `domains` now reaches all four
    # bodies (two pwsh, two bash) through step-level `env:`. It held two write
    # environments at once — cloudflare-prod-write for the apex flip and github-prod for
    # the CNAME flip — so an injected payload ran twice, under two credentials.
    # --- WHMCS --------------------------------------------------------------
    "201-whmcs-export-domains.yml": ("output_file",),
    "202-whmcs-export-products.yml": (
        "client_products_output_file",
        "products_output_file",
    ),
    "203-whmcs-export-payment-methods.yml": ("output_file",),
    # 205-whmcs-ticket-open.yml burned down: `deptid` and `client_id` now reach
    # the pwsh body through step-level `env:` (TICKET_DEPTID / TICKET_CLIENT_ID).
    # It runs on `whmcs-prod` (write), and its injection point sits beside the
    # WHMCS API credential that `whmcs-secrets-from-kv` exports through GITHUB_ENV
    # (WHMCS_API_IDENTIFIER / WHMCS_API_SECRET / WHMCS_APIM_SUBSCRIPTION_KEY) --
    # so a reviewer reading the step's own `env:` (L213) or grepping the body for
    # `secrets.` (#1141) scores the file as holding nothing. `deptid` was the
    # simpler of the two live sites: it interpolated inside a SINGLE-quoted array
    # element, and single quotes stop `$( )` but not a `'); <payload>; $x = @('`
    # break-out, so the WHMCS secret exfiltrated and the legitimate call still ran
    # at exit 0. `priority` (choice) and `dry_run` (boolean) stay interpolated:
    # GitHub constrains both, so neither can carry a payload.
    "208-whmcs-tickets-export.yml": ("output_file", "status"),
    "213-whmcs-zeffy-payments-import-draft.yml": (
        "clients_output",
        "end_date",
        "invoices_output",
        "max_rows",
        "max_rows_per_file",
        "start_date",
        "transactions_output",
        "zeffy_output",
        "zeffy_output_xlsx",
    ),
    "214-whmcs-clients-metrics.yml": ("output_file",),
    "215-whmcs-nonprofit-clients-metrics.yml": ("output_file",),
    "216-whmcs-activity-metrics.yml": ("charity_gids", "output_file"),
    "217-whmcs-client-fields-survey.yml": ("output_file", "throttle_ms"),
    "218-whmcs-siteslist-reconciliation.yml": (
        "cloudflare_pid",
        "github_pages_pid",
        "output_file",
    ),
    "220-whmcs-served-metrics.yml": ("charity_gids", "output_file"),
    "222-whmcs-product-alignment.yml": ("product_id",),
    "224-whmcs-github-pages-product-alignment.yml": ("product_id",),
    "229-whmcs-client-field-populate.yml": ("client_id", "email"),
    # --- Microsoft 365 ------------------------------------------------------
    # 301-m365-domain-preflight.yml burned down: `domain` now reaches both pwsh
    # bodies through step-level `env:`. Its two call sites sit in TWO jobs under
    # TWO environments — `graph` on m365-prod and `cloudflare` on
    # cloudflare-prod-read, the second `needs:` the first — so one payload in one
    # dispatch ran in both. What distinguishes it from 303/304 is what sat in the
    # first injection point's own `env:`: not a `secrets.*` value but
    # GRAPH_ACCESS_TOKEN, a live Microsoft Graph bearer token mapped from a step
    # OUTPUT. A sweep of the file for `secrets.` in a `run:` body — the scope test
    # #1141's review recommended for the remaining lanes — finds nothing there.
    # The second site inherits the Key Vault Cloudflare tokens the composite
    # action loads, which are not in any `env:` block at all.
    # 303-m365-domain-and-dkim.yml burned down: `domain` now reaches both pwsh
    # bodies through step-level `env:`. Its two call sites sit in ONE job on
    # m365-prod, and the credential exposure is broader than 304's rather than
    # narrower: the JOB-level `env:` maps EXO_CERT_PFX_BASE64 and
    # EXO_CERT_PFX_PASSWORD, so the Exchange Online certificate and its password
    # were in the process environment of BOTH injection points — including the
    # `status` step, which reads as the harmless half of the workflow. The DKIM
    # step adds EXO_APP_ID / EXO_TENANT / EXO_ORGANIZATION in its own `env:`
    # (ledger L213). `action` and `create_if_missing` are still interpolated and
    # are NOT findings: GitHub constrains a `choice` and a `boolean` to a value it
    # generated, so neither can carry a payload.
    # 304-m365-dkim-enable.yml burned down: `domain` now reaches all three bodies
    # through step-level `env:`. It was the only remaining entry interpolating one
    # input into three jobs under two gated environments — m365-prod twice and
    # cloudflare-prod-write once — so a payload ran three times, under two credentials,
    # on one approval. Sharper than the neighbouring entries in WHERE the credential
    # sat: both m365-prod steps carry FFC_EXO_CERT_PFX_BASE64 / FFC_EXO_CERT_PASSWORD
    # in the SAME step's `env:` as the injection point, so the Exchange Online
    # certificate was already in the process environment of the body being injected
    # into. Measured against the shipped body: the cert password was written to a file,
    # m365-dkim.ps1 was then called with a legal `Domain=[ffcworkingsite1.org]`, and the
    # step exited 0.
    # 306-discover-uncaptured-comms.yml burned down: `mailboxes` / `since_days` now
    # reach the pwsh body through step-level `env:`. Two things distinguish it from
    # the other m365-prod lanes. First, the credential in reach appears NOWHERE in
    # the file: the preceding step mints a Graph bearer token from the OIDC login and
    # exports it through GITHUB_ENV, so both recommended sweeps — read the step's own
    # `env:` (L213), grep the body for `secrets.` (#1141) — score this workflow as
    # holding nothing, and it is the workflow's ONLY credential. This lane is why
    # #1188 exists: `--reachability 306-discover-uncaptured-comms.yml` answers it
    # in one call instead of a third per-lane rediscovery. Second, it is the
    # first lane whose injection point sat in SINGLE quotes, where `$( )` does not
    # expand: the payload every earlier lane used is inert here and reports the
    # pre-fix body as harmless, so the control had to close the quote, steal, and
    # re-issue the legitimate call. Measured against the shipped body that way: the
    # bearer token was written to a file, the scanner was then invoked with a legal
    # mailbox list, and the step exited 0. Both inputs carry a `default:` and neither
    # declares a `type:`, so both are plain strings — a pre-filled box is not a
    # constraint.
    # --- WPMUDEV ------------------------------------------------------------
    "601-wpmudev-export-sites.yml": ("output_file",),
    # --- GitHub -------------------------------------------------------------
    "702-ffc-ex-clone-deploy.yml": ("depth", "domain", "exclude"),
    # 704-website-analytics-wire.yml burned down: `gtm_id` / `measurement_id` now reach
    # the bash body through step-level `env:`. Its only interpolating step was the one
    # named `Validate inputs` — the step whose purpose is to reject a malformed id,
    # performing that check by pasting the value into the program doing the checking,
    # one step before `wr-all-cbm-github-pat` is loaded on github-prod. The neighbouring
    # step already used `env:` correctly, so the file read as if the pattern held.
    # 720-create-repo.yml burned down: its three free-text inputs now reach both the
    # pwsh and the bash bodies through step-level `env:`. It was the instance #1080
    # reproduced live in both directions, on github-prod.
}


def compare(current: dict[str, tuple[str, ...]],
            known: dict[str, tuple[str, ...]] | None = None) -> tuple[list[str], list[str]]:
    """(new_instances, stale_entries) between the tree and the freeze.

    Exact in both directions, per #1080 criteria 2/3/5. A workflow present in
    both but whose interpolated input set has CHANGED reports on both sides:
    an added input is a new instance to justify, a removed one is a fixed call
    site whose entry must be deleted.
    """
    known = KNOWN_UNGUARDED if known is None else known
    new: list[str] = []
    stale: list[str] = []

    for workflow, inputs in sorted(current.items()):
        if workflow not in known:
            new.append(
                f"{workflow}: NOT in KNOWN_UNGUARDED — interpolates "
                f"{', '.join(inputs)}"
            )
            continue
        added = sorted(set(inputs) - set(known[workflow]))
        if added:
            new.append(
                f"{workflow}: newly interpolates {', '.join(added)} "
                f"(frozen set was {', '.join(known[workflow])})"
            )

    for workflow, inputs in sorted(known.items()):
        if workflow not in current:
            stale.append(
                f"{workflow}: listed in KNOWN_UNGUARDED but nothing is "
                f"interpolated any more — delete the entry"
            )
            continue
        removed = sorted(set(inputs) - set(current[workflow]))
        if removed:
            stale.append(
                f"{workflow}: KNOWN_UNGUARDED still lists {', '.join(removed)}, "
                f"which is no longer interpolated — narrow the entry to "
                f"{', '.join(current[workflow])}"
            )

    return new, stale


REMEDY = (
    "Pass the value through `env:` instead of interpolating it into the script "
    "body, which is what 105-manage-record.yml:178-184 already does:\n"
    "    env:\n"
    "      DOMAIN: ${{ inputs.domain }}\n"
    "    run: |\n"
    '      pwsh -File ./x.ps1 -Zone $env:DOMAIN      # or "$DOMAIN" in bash\n'
    "An `env:` value is handed to the process as data and is never parsed as "
    "code, so no quoting in the value can change the program."
)


def _parse_workflow(name: str) -> dict | None:
    """Parsed workflow by file name, or None if it cannot be read.

    Reachability is reporting, never a verdict: a file this cannot parse is
    already a hard finding from `scan_all`, which runs first and returns 1. So
    swallowing the error here cannot hide anything — it only keeps the report
    from crashing on a tree that has already failed.
    """
    try:
        parsed = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return parsed if isinstance(parsed, dict) else None


def reachability_by_site(findings: list[Finding]) -> dict[tuple[str, str, str], list[Reach]]:
    """(workflow, job, step) -> credentials in reach, for sites that reach one.

    Keyed by SITE and not by workflow: 103 puts Cloudflare tokens in reach of
    three of its steps through `GITHUB_ENV` and the Exchange Online certificate
    in reach of two others through their own `env:`, and a per-workflow union
    would report one blurred set that is true of no single step.
    """
    sites: dict[tuple[str, str, str], list[Reach]] = {}
    for finding in findings:
        if finding.step_index is None:
            continue
        key = (finding.workflow, finding.job, finding.step)
        if key in sites:
            continue
        workflow = _parse_workflow(finding.workflow)
        if workflow is None:
            continue
        reached = credentials_in_reach(workflow, finding.job, finding.step_index)
        if reached:
            sites[key] = reached
    return sites


def reachability_paragraph(findings: list[Finding], write_workflows: list[str]) -> str:
    """The report section this guard gained for #1188.

    Additive by construction: it reads the findings that already exist and
    returns text. It cannot change `current_map`, `compare`, or the exit code.
    """
    sites = reachability_by_site(findings)
    if not sites:
        return (
            "credential reachability (#1188): no frozen call site has a credential in "
            "reach. That is a claim about THIS tree, not a property of the guard — if "
            "it appears after a burn-down, the lanes that held credentials were fixed."
        )

    hidden_sites = {k: v for k, v in sites.items() if any(r.hidden for r in v)}
    workflows = {k[0] for k in sites}
    lines = [
        f"credential reachability (#1188): {len(sites)} of {len(findings)} frozen call "
        f"sites run with a credential in reach, across {len(workflows)} workflow(s); "
        f"{len(hidden_sites)} of those reach one ONLY through GITHUB_ENV or an acquired "
        f"CLI session, where the injecting step's own `env:` (L213) and a `secrets.` "
        f"grep of the file (#1141) both read clean. That is the surface #1080 lanes 7, "
        f"9 and 10 each rediscovered by hand, plus the one lane 13 could not see at all "
        f"(#1208).",
        "",
    ]
    for workflow, job, step in sorted(sites, key=lambda k: (k[0] not in write_workflows, k)):
        marker = "[W]" if workflow in write_workflows else "   "
        lines.append(f"  {marker} {workflow} job '{job}' step '{step}'")
        for reach in sites[(workflow, job, step)]:
            lines.append(f"        {reach}")
    lines.append("")
    lines.append(
        "  [W] = the workflow enters an environment not suffixed `-read`. Arrival path is "
        "the point: a name in the step's own `env:` is visible to anyone who opens the "
        "file, a GITHUB_ENV arrival is the one nobody sees, and an `acquired` line is a "
        "credential with no variable at all — an authenticated CLI session an earlier "
        "step logged in and left on disk, which an injected body reaches by running the "
        "CLI itself (#1208). A third-party `uses:` is NOT resolved for its EXPORTS — its "
        "source is not in this tree — so a variable it exports is absent from these lines "
        "rather than known to be absent; a third-party login action is still named, "
        "because its name is enough to know a session exists."
    )
    return "\n".join(lines)


def reachability_listing(names: list[str]) -> int:
    """`--reachability [FILE …]`: every script step and what it can reach.

    The report above can only speak about FROZEN call sites, which by design
    shrink to nothing as #1080 burns down — 103, 110, 301 and 306 had all been
    fixed before #1188 was filed, and they are the four workflows the issue names
    as the motivating cases. So the mechanism is also exposed directly: a
    reviewer about to move an input into `env:` can ask what the step they are
    editing actually holds, whether or not it is still a finding.
    """
    if names:
        paths = []
        for name in names:
            path = WORKFLOWS / name
            if not path.exists():
                print(f"no such workflow: {name}")
                return 1
            paths.append(path)
    else:
        paths = workflow_paths()

    shown = 0
    for path in sorted(paths):
        workflow = _parse_workflow(path.name)
        if workflow is None:
            print(f"{path.name}: cannot be parsed")
            return 1
        rows = []
        for job_id, index, label, kind, _body in script_bodies(workflow):
            reached = credentials_in_reach(workflow, job_id, index)
            if reached:
                rows.append(f"  job '{job_id}' step '{label}' ({kind})")
                rows.extend(f"      {reach}" for reach in reached)
        if rows:
            shown += 1
            print(path.name)
            print("\n".join(rows))
    print(
        f"\n{shown} of {len(paths)} workflow file(s) run a script step with a credential "
        f"in reach. This is a READING, not a finding: a credential in reach is normal and "
        f"necessary — it matters only when free text is interpolated into that same body "
        f"(#1080), and what it answers is 'how bad is this one'."
    )
    return 0


USAGE = (
    "usage: check-workflow-input-interpolation.py [--reachability [WORKFLOW …]]\n"
    "  (no arguments)  run the guard: exit 1 on a new instance, a stale freeze "
    "entry, or an unreadable file\n"
    "  --reachability  report the credentials in reach at every script step, for "
    "the named workflow files (default: all)"
)

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        if args[0] == "--reachability":
            return reachability_listing(args[1:])
        print(USAGE)
        return 2

    findings, unreadable, scanned = scan_all()

    if unreadable:
        print(f"workflow input interpolation guard: {len(unreadable)} file(s) unreadable\n")
        for problem in unreadable:
            print(f"  {problem}")
        print(
            "\nThis guard fails closed: a workflow it cannot parse is a finding, "
            "because an unreadable file is exactly the blind spot it exists to close."
        )
        return 1

    current = current_map(findings)
    new, stale = compare(current)

    if new or stale:
        if new:
            print(
                f"free-text dispatch inputs newly interpolated into a script body: "
                f"{len(new)}\n"
            )
            for item in new:
                print(f"  {item}")
            print()
            print(REMEDY)
            print(
                "\nIf this really must ship as-is, add it to KNOWN_UNGUARDED in "
                f"{pathlib.Path(__file__).name} with a reason — but read #1080 first: "
                "on a gated workflow the injected code runs AFTER the approval, "
                "holding a credential the dispatcher does not have."
            )
        if stale:
            if new:
                print()
            print(f"stale KNOWN_UNGUARDED entries: {len(stale)}\n")
            for item in stale:
                print(f"  {item}")
            print(
                "\nA call site was fixed without deleting its freeze entry. Remove it "
                "in the same PR, or the list stops describing the tree and the next "
                "reader cannot tell what is left to burn down."
            )
        return 1

    write_workflows = []
    for workflow in current:
        try:
            parsed = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if any(is_write_environment(e) for e in environments(parsed)):
            write_workflows.append(workflow)

    print(
        f"workflow input interpolation OK: {scanned} workflow files scanned; "
        f"{len(current)} interpolate a free-text dispatch input into a script body "
        f"({len(findings)} call sites), of which {len(write_workflows)} enter a write "
        f"environment. All are in the KNOWN_UNGUARDED freeze and no entry is stale.\n"
        f"This is a FREEZE on new instances, not an endorsement: every entry is a "
        f"place a dispatcher can supply code that runs after an approver spends a "
        f"production credential (#1080). Burn down the {len(write_workflows)} write "
        f"ones first.\n"
        f"Not judged: composite actions under .github/actions/ (an action's `inputs.*` "
        f"is a different context), and reusable-workflow `workflow_call` inputs."
    )
    print()
    print(reachability_paragraph(findings, write_workflows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
