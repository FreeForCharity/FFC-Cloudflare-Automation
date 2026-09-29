#!/usr/bin/env python3
r"""Guard: a free-text dispatch input re-read raw by an artifact selector must be constrained (#1422).

THE SECOND CONSUMER THE #1080 FAMILY DOES NOT ASK ABOUT
    `scripts/check-workflow-input-interpolation.py` asks whether a free-text
    dispatch input is substituted into a `run:` body's TEXT, and the burn-down's
    completion test is that it no longer is. `scripts/check-workflow-empty-input-guard.py`
    asks whether the resulting `env:` mapping fails closed when empty.

    Neither asks the question this file exists for: the SAME input is very often
    re-read, RAW, by an `actions/upload-artifact` step's `path:` — and a `path:`
    is an `@actions/glob` SELECTOR, not a filename. The two consumers have
    different grammars (ledger L298), so moving the value into `env:` closes one
    of two doors and the burn-down scores the workflow finished.

    #1422 measured the consequence on `main`: `213` carried one of six guard
    conditions (a blank check per consuming step) and `218` one of six, while
    both were off the #1080 freeze. `801` and `802` carried four of six. All four
    were "done".

WHY THIS IS A SECURITY CHECK AND NOT A TIDINESS ONE
    `*` and `?` are illegal in a Windows filename, so those self-block at the
    export. `[` and `]` are legal filename characters AND character-class
    metacharacters to the globber, so `artifacts/whmcs/[m]sal_token_cache.json`
    writes a literal file, exits 0, and then makes the upload's glob match a
    DIFFERENT, REAL file. An absolute path or a `..` segment needs no glob at
    all. A newline turns one input into several `path:` patterns, because
    `upload-artifact` reads `path:` as a newline-delimited pattern list. And `~`
    is expanded by PowerShell but NOT by the globber, so the two consumers do
    not even agree which file is meant — and `~/.azure` is where the `az`
    session an OIDC login leaves behind actually lives (#1188/#1208).

    Most of these jobs enter a `*-prod-read` environment, so the runner holds a
    live credential that does not appear in the injecting step's own `env:`.

WHAT COUNTS AS CONSTRAINED — SIX HAZARD CLASSES, NOT ONE SPELLING
    This guard is deliberately permissive about the SHAPE of the test and strict
    about the HAZARDS covered, for the reason `check-workflow-empty-input-guard.py`
    states: a checker that fails on correct code is the one that gets switched
    off. Three genuinely different shapes ship in this tree today and all three
    are real guards:

      inline      `if ($env:IN_OUTPUT_FILE -match '[*?\[\]]') { throw … }`
                  (201, 203, 208, 218)
      loop        `foreach ($spec in @(@{ Input='x'; Value=$env:IN_X }, …)) { …
                  if ($value -match …) … }`  (202, 213)
      allowlist   `if ($env:IN_OUTPUT_FILE -notmatch '\A[A-Za-z0-9._-]+\z' …)`
                  (601)

    601's allowlist is STRICTER than the six conditions, not weaker: a character
    class of `[A-Za-z0-9._-]` admits no separator, no metacharacter, no `~`, no
    `:` and no newline. Reporting it would be a false finding, and the anchors
    matter — `\A`/`\z` rather than `^`/`$`, because in .NET `$` also matches
    before a trailing newline, so `^[A-Za-z0-9._-]+$` accepts "out.csv\n" (601's
    own comments record that measurement). So an allowlist is credited only when
    it is `\A`-anchored, and it is NOT credited for the `..` class: `..` is made
    only of characters the allowlist permits, so it needs its own test.

FAIL CLOSED
    A workflow whose YAML will not parse is a finding, never a skip. A site whose
    guard shape this checker does not recognise is a finding, not a pass —
    if a new shape is legitimate, teach it here in the same PR. Needs no
    PowerShell host: it is a textual/AST check, so tool absence cannot turn
    "could not run" into "everything passed".

Exit codes: 0 = the unguarded set is exactly the frozen one, 1 = a new instance,
a stale freeze entry, or an unreadable workflow.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:  # pragma: no cover - CI installs it
    print("::error::pyyaml is required: python3 -m pip install pyyaml")
    sys.exit(1)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_INPUT_REF = re.compile(r"inputs\.([A-Za-z_][A-Za-z0-9_-]*)")

# The six hazard classes, in the order the shipped guards apply them. NEWLINE is
# second for a reason that is itself under test in the lane modules: the ROOTED
# and TILDE spellings anchor with `^`, which is start-of-STRING, so over a
# multi-line value they examine only the first line.
CLASSES = ("BLANK", "NEWLINE", "GLOB", "ROOTED", "TILDE", "DOTDOT")

# Accepted spellings per class, as regexes over the run body with the value
# reference already substituted in. `REF` is replaced by an alternation of every
# way this job can name the value (the env var and any local alias).
_SPELLINGS: dict[str, tuple[str, ...]] = {
    "BLANK": (
        r"IsNullOrWhiteSpace\(\s*REF\s*\)",
    ),
    "NEWLINE": (
        r"REF\s*-match\s*'\[\\r\\n\]'",
        r"REF\s*-match\s*'\[\\n\\r\]'",
    ),
    "GLOB": (
        r"REF\s*-match\s*'\[\*\?\\\[\\\]\]'",
    ),
    "ROOTED": (
        r"REF\s*-match\s*'\^\(\[A-Za-z\]\+:\|\[\\\\/\]\)'",
        # 801/802: a colon test AND IsPathRooted, paired because the latter is
        # platform-dependent. Both must be present to credit the class.
        r"REF\.Contains\('\:'\)(?=.*IsPathRooted\(\s*REF\s*\))",
    ),
    "TILDE": (
        r"REF\s*-match\s*'\^~'",
    ),
    "DOTDOT": (
        r"\(\s*REF\s*-split\s*'\[\\\\/\]'\s*\)\s*-contains\s*'\.\.'",
        r"REF\s*-match\s*'\\A\\\.\\\.\?\\z'",
    ),
}

# A `\A`-anchored allowlist over a character class that admits none of the
# hazardous characters. Credited for BLANK, NEWLINE, GLOB, ROOTED and TILDE --
# but never for DOTDOT, because `..` is spelled entirely in permitted
# characters. `^`/`$` is deliberately NOT accepted: in .NET `$` also matches
# before a trailing newline, so that anchoring admits "out.csv\n".
_ALLOWLIST = re.compile(
    r"REF\s*-notmatch\s*'\\A\[(?P<class>[^]]*)\]\+\\z'"
)
_ALLOWLIST_CREDITS = ("BLANK", "NEWLINE", "GLOB", "ROOTED", "TILDE")
# Characters that must NOT appear in an allowlist's class for it to be credited.
_FORBIDDEN_IN_ALLOWLIST = set("*?[]~:/\\ ")


class WorkflowUnreadable(Exception):
    pass


@dataclass(frozen=True)
class Site:
    """One (workflow, job, input) pair reachable raw from an artifact selector."""

    workflow: str
    job: str
    input_name: str
    upload_step: str
    missing: tuple = ()

    @property
    def key(self) -> str:
        return f"{self.job}:{self.input_name}"

    def describe(self) -> str:
        miss = ", ".join(self.missing) if self.missing else "none"
        return (
            f"{self.workflow}: job {self.job!r} input {self.input_name!r} is read "
            f"raw by {self.upload_step!r}'s path: — unguarded classes: {miss}"
        )


@dataclass
class Job:
    name: str
    env_map: dict = field(default_factory=dict)
    bodies: list = field(default_factory=list)
    uploads: list = field(default_factory=list)


def workflow_paths() -> list[pathlib.Path]:
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def _on_block(doc: dict) -> dict:
    """`on:` parses to the YAML 1.1 boolean True (the Norway problem)."""
    on = doc.get(True, doc.get("on"))
    return on if isinstance(on, dict) else {}


def dispatch_inputs(doc: dict) -> dict:
    dispatch = _on_block(doc).get("workflow_dispatch") or {}
    if not isinstance(dispatch, dict):
        return {}
    inputs = dispatch.get("inputs") or {}
    return inputs if isinstance(inputs, dict) else {}


def free_text_inputs(doc: dict) -> set:
    """Inputs GitHub does not constrain: `type: string`, or no type at all.

    A `choice` is limited to its options and a `boolean` to two literals, so
    neither can carry a payload. An input whose body is empty parses to None
    rather than to a mapping, which is why the type is read defensively.
    """
    found = set()
    for name, spec in dispatch_inputs(doc).items():
        if spec is None:
            found.add(name)
            continue
        if not isinstance(spec, dict):
            continue
        if spec.get("type", "string") == "string":
            found.add(name)
    return found


def inputs_in(text: str) -> set:
    names = set()
    for match in _EXPRESSION.finditer(text or ""):
        names.update(_INPUT_REF.findall(match.group(1)))
    return names


def collect_job(name: str, job: dict) -> Job:
    """Every `env:` mapping, `run:` body and upload step in one job.

    Job-level `env:` is merged first so a step-level mapping wins, matching how
    Actions resolves them.
    """
    out = Job(name=name)
    job_env = job.get("env")
    if isinstance(job_env, dict):
        out.env_map.update({k: v for k, v in job_env.items() if isinstance(v, str)})
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        step_env = step.get("env")
        if isinstance(step_env, dict):
            out.env_map.update(
                {k: v for k, v in step_env.items() if isinstance(v, str)}
            )
        run = step.get("run")
        if isinstance(run, str):
            out.bodies.append(run)
        uses = step.get("uses") or ""
        if isinstance(uses, str) and "actions/upload-artifact" in uses:
            with_block = step.get("with")
            if isinstance(with_block, dict):
                path = with_block.get("path")
                if isinstance(path, str):
                    out.uploads.append((step.get("name") or uses, path))
    return out


def vars_for(job: Job, input_name: str) -> set:
    """Every env var in this job whose value interpolates exactly this input."""
    return {
        var
        for var, expression in job.env_map.items()
        if input_name in inputs_in(expression)
    }


def aliases_for(bodies: list, variables: set) -> set:
    """Local copies of a guarded variable: `$out = $env:IN_OUTPUT_FILE`.

    Without this the checker reports correct code — 801/802 and 218 both test a
    local copy rather than `$env:` directly, and that is the false-positive class
    #1150 names. One level is enough for every shape in the tree; a chain of
    copies is not credited, and that is the safe direction.
    """
    found = set()
    for body in bodies:
        for var in variables:
            for match in re.finditer(
                r"\$([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\$env:" + re.escape(var) + r"\b",
                body,
            ):
                found.add("$" + match.group(1))
    return found


def loop_values(bodies: list, variables: set) -> set:
    """`$value` when a `foreach ($spec in @(…))` spec list reads one of these vars.

    202 and 213 apply the six conditions once, to the loop variable, over a spec
    list of (name, value) pairs. Crediting `$value` for a variable that does NOT
    appear in the spec list would be the copy-paste bug that shape exists to
    prevent, so the list is read rather than assumed.
    """
    found = set()
    for body in bodies:
        for match in re.finditer(r"foreach\s*\(\s*\$(\w+)\s+in\s+@\(", body):
            start = match.end()
            depth, end = 1, None
            for i in range(start, len(body)):
                if body[i] == "(":
                    depth += 1
                elif body[i] == ")":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if end is None:
                continue
            spec_list = body[start:end]
            for var in variables:
                if f"$env:{var}" in spec_list:
                    # The value the loop body reads. `Value = …` is the shipped
                    # spelling; the loop variable itself is credited too, since
                    # `$spec.Value` is equivalent.
                    found.add("$value")
                    found.add(f"${match.group(1)}.Value")
    return found


def _refs(variables: set, extra: set) -> list:
    refs = [f"$env:{v}" for v in sorted(variables)] + sorted(extra)
    return refs


def satisfied_classes(bodies: list, refs: list) -> set:
    """Which hazard classes are covered for any of these value references."""
    if not refs:
        return set()
    ref_alt = "(?:" + "|".join(re.escape(r) for r in refs) + ")"
    blob = "\n".join(bodies)
    # `re.DOTALL` for the ROOTED lookahead, which spans the rest of a condition.
    covered = set()
    for cls, spellings in _SPELLINGS.items():
        for spelling in spellings:
            if re.search(spelling.replace("REF", ref_alt), blob, re.DOTALL):
                covered.add(cls)
                break
    for match in re.finditer(_ALLOWLIST.pattern.replace("REF", ref_alt), blob):
        klass = match.group("class")
        # An allowlist is credited only when its class admits none of the
        # hazardous characters. `-` and `.` are ordinary members here; the
        # forbidden set is what would let a payload through.
        if not (_FORBIDDEN_IN_ALLOWLIST & set(klass)):
            covered.update(_ALLOWLIST_CREDITS)
    return covered


def scan(paths=None) -> tuple[list, list]:
    """(sites, unreadable). Every site carries the classes it is MISSING."""
    sites: list[Site] = []
    unreadable: list[str] = []
    for path in paths or workflow_paths():
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:  # a parse failure is a finding, never a skip
            unreadable.append(f"{path.name}: {exc}")
            continue
        if not isinstance(doc, dict):
            unreadable.append(f"{path.name}: top level is not a mapping")
            continue
        free_text = free_text_inputs(doc)
        if not free_text:
            continue
        jobs = doc.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job_name, job in jobs.items():
            if not isinstance(job, dict):
                continue
            collected = collect_job(job_name, job)
            for step_name, path_value in collected.uploads:
                for input_name in sorted(inputs_in(path_value) & free_text):
                    variables = vars_for(collected, input_name)
                    extra = aliases_for(collected.bodies, variables) | loop_values(
                        collected.bodies, variables
                    )
                    covered = satisfied_classes(
                        collected.bodies, _refs(variables, extra)
                    )
                    missing = tuple(c for c in CLASSES if c not in covered)
                    if missing:
                        sites.append(
                            Site(
                                workflow=path.name,
                                job=job_name,
                                input_name=input_name,
                                upload_step=step_name,
                                missing=missing,
                            )
                        )
    return sites, unreadable


# The freeze. Each entry is an OPEN #1080 burn-down lane: the input is still
# interpolated into the `run:` body, so `check-workflow-input-interpolation.py`
# already reports it and the artifact-path guards belong in the PR that lands
# that lane. Removing a workflow from `KNOWN_UNGUARDED` there without adding the
# guard set here is exactly what #1422 found, so an entry must leave BOTH freezes
# at once.
#
# An entry is an explicit, reasoned exception, not the normal state. Adding one
# for a NEW workflow means writing down why the value cannot be constrained.
KNOWN_UNGUARDED: dict = {
    "214-whmcs-clients-metrics.yml": ["clients_metrics:output_file"],
    "215-whmcs-nonprofit-clients-metrics.yml": ["nonprofit_clients_metrics:output_file"],
    "216-whmcs-activity-metrics.yml": ["activity_metrics:output_file"],
    "217-whmcs-client-fields-survey.yml": ["client_fields_survey:output_file"],
    "220-whmcs-served-metrics.yml": ["served_metrics:output_file"],
}


def current_map(sites: list) -> dict:
    out: dict = {}
    for site in sites:
        out.setdefault(site.workflow, []).append(site.key)
    return {k: sorted(v) for k, v in out.items()}


def compare(current: dict, known: dict | None = None) -> tuple[list, list]:
    """(new_instances, stale_entries) between the tree and the freeze."""
    known = KNOWN_UNGUARDED if known is None else known
    new: list[str] = []
    stale: list[str] = []
    for workflow, keys in sorted(current.items()):
        if workflow not in known:
            new.append(f"{workflow}: NOT in KNOWN_UNGUARDED — {', '.join(keys)}")
            continue
        added = sorted(set(keys) - set(known[workflow]))
        if added:
            new.append(
                f"{workflow}: new unguarded site(s) not in KNOWN_UNGUARDED — "
                f"{', '.join(added)}"
            )
    for workflow, keys in sorted(known.items()):
        if not (WORKFLOW_DIR / workflow).exists():
            stale.append(
                f"{workflow}: listed in KNOWN_UNGUARDED but the file does not exist"
            )
            continue
        if workflow not in current:
            stale.append(
                f"{workflow}: listed in KNOWN_UNGUARDED but nothing is unguarded "
                f"any more — remove the entry in the PR that guarded it"
            )
            continue
        removed = sorted(set(keys) - set(current[workflow]))
        if removed:
            stale.append(
                f"{workflow}: KNOWN_UNGUARDED still lists {', '.join(removed)}, "
                f"which is now guarded — remove it"
            )
    return new, stale


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--json", action="store_true", help="machine-readable output"
    )
    args = parser.parse_args()

    sites, unreadable = scan()
    current = current_map(sites)
    new, stale = compare(current)

    if args.json:
        print(
            json.dumps(
                {
                    "sites": [
                        {
                            "workflow": s.workflow,
                            "job": s.job,
                            "input": s.input_name,
                            "upload_step": s.upload_step,
                            "missing": list(s.missing),
                        }
                        for s in sites
                    ],
                    "unreadable": unreadable,
                    "new": new,
                    "stale": stale,
                },
                indent=2,
            )
        )
        return 1 if (new or stale or unreadable) else 0

    print(
        "Artifact-selector guard audit: a free-text dispatch input read raw by an\n"
        "`actions/upload-artifact` path: must be constrained against all six hazard\n"
        f"classes ({', '.join(CLASSES)}).\n"
    )
    if unreadable:
        print("::error::workflows that would not parse (a finding, not a skip):")
        for line in unreadable:
            print(f"  {line}")
        print()
    for site in sites:
        frozen = site.key in KNOWN_UNGUARDED.get(site.workflow, [])
        mark = "frozen" if frozen else "NEW"
        print(f"  [{mark}] {site.describe()}")
    if not sites:
        print("  no unguarded sites at all")
    print()
    if new:
        print(f"::error::new unguarded artifact-selector site(s): {len(new)}")
        for line in new:
            print(f"  {line}")
        print(
            "\nEach must either carry the six guard conditions (see\n"
            "201/202/203/208/213/218 for the inline and loop shapes, 601 for the\n"
            "allowlist shape) or be added to KNOWN_UNGUARDED with a reason."
        )
        print()
    if stale:
        print(f"::error::stale KNOWN_UNGUARDED entries: {len(stale)}")
        for line in stale:
            print(f"  {line}")
        print()
    if new or stale or unreadable:
        return 1
    print("OK: the unguarded set is exactly the frozen one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
