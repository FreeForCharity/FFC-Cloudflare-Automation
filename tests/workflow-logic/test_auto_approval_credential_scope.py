"""Guard: every workflow on the Conductor ✅ auto-approve list must load only
read-scoped credentials (#919, mechanizing the #915/#916 ruling).

#916 made **credential scope** a necessary condition for auto-approval: a
waiting gate may be auto-approved only if the job is `Reads`-level (or a
`dry_run=true` write) **and** every credential it loads resolves to read scope.
Applying that removed 114, 221 and 401 from the ✅ list. Until now the rule
lived only in prose in docs/workflow-safety-and-approvals.md.

**Why the obvious implementation is wrong.** The tempting check is

    grep -l 'wr-all-' .github/workflows/*.yml     # 14 files — 114 and 221 are NOT among them

`whmcs-secrets-from-kv` and `zeffy-secrets-from-kv` both declare
`scope: {required: false, default: 'write'}` and build the secret name at run
time (`$prefix = if ($scope -eq 'write') { 'wr-all' } else { 'read-all' }`). So
a workflow that simply **omits** the input loads write-scoped material while
containing no `wr-all-` string anywhere: **omission reads as safe.** 221 is
exactly that shape. Every check here therefore resolves the scope the action
will actually use — the step's explicit value, or the invoked action's own
declared `default:` read from its `action.yml` — and never pattern-matches the
workflow text to decide scope.

Three deliberate design choices, each from a ledger lesson:

1. **An unresolvable scope fails.** A missing `action.yml`, an absent `scope`
   input, or an expression the guard cannot evaluate is reported as a violation,
   never assumed to be `read`. "Cannot determine" is the failure mode this whole
   issue is about (L45-4: presence read as validity).
2. **A vacuous pass fails.** The ✅ list must parse to a non-empty set of real
   workflows, and a listed workflow with no credential step at all must be
   enumerated in `NO_KV_CREDENTIAL_WORKFLOWS` — otherwise renaming a `*-from-kv`
   action would make its callers silently, wrongly green. Same shape as the
   correct-looking count #917 fixed in the duplicate-number checker.
3. **The OIDC identity is checked alongside the scope.** `scope` selects which
   copy of a secret is read; the caller separately passes
   `azure-client-id: ${{ vars.{READ_ALL,WR_ALL}_FFC_AZURE_KV_CLIENT_ID }}`,
   which selects **which managed identity authenticates**. `scope: read` on the
   writer identity would read the read-all copy while holding writer access to
   the whole vault — a privilege leak the scope check alone cannot see (#848
   names this split as unenforced). The tree is consistent today; this keeps it
   so.
"""

from __future__ import annotations

import copy
import pathlib
import re
import sys
import tempfile

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import WORKFLOWS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SAFETY_DOC = REPO_ROOT / "docs" / "workflow-safety-and-approvals.md"
ACTIONS_ROOT = REPO_ROOT / ".github" / "actions"

# --- parsing the ✅ list out of the policy doc -------------------------------
#
# Anchored on BOTH the section heading and the sentence that states the list, so
# a rename or a reformat fails loudly instead of matching nothing. A guard that
# reads an empty list would pass every assertion below while checking nothing.
POLICY_HEADING = "## Conductor auto-approval policy"
AUTO_APPROVE_SENTENCE = re.compile(
    r"gated reads[^.]*?\bare\s+\*\*([0-9,\s–—-]+?)\*\*",
)

# Listed workflows that load no Key-Vault credential at all. Legitimate: these
# run against environments that still hold raw GitHub secrets (#844's migration
# backlog — m365-prod, wpmudev-prod), so there is no `*-from-kv` step to resolve.
#
# The list is exact, not a floor. If a workflow here gains a credential step it
# must be removed; if a listed workflow's credential step disappears (e.g. an
# action renamed and the caller not updated) it must be added deliberately.
# Without this, "no credential steps found" — the exact output of a broken
# matcher — would read as "no write-scoped credentials".
NO_KV_CREDENTIAL_WORKFLOWS = {
    302,  # m365-prod, raw secrets (#844)
    303,  # m365-prod, raw secrets (#844)
    306,  # m365-prod, raw secrets (#844)
    601,  # wpmudev-prod, raw secrets (#844)
}

# Steps where the resolved scope and the OIDC identity deliberately disagree.
# Empty, and it should stay that way. The only defensible reason to add a row is
# that the read identity has no federated credential for that environment — in
# which case record the reason and the issue number here rather than deleting
# the assertion. Keys: (workflow filename, job id, action name).
IDENTITY_SCOPE_EXCEPTIONS: set[tuple[str, str, str]] = set()

# Writer material in either spelling: the hyphenated KV secret names
# (`wr-all-cbm-github-pat`) and the underscored repository Variables that select
# the writer managed identity (`vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID`). Both mean
# the job rides on writer credentials. Must not match `read-all-*`/`READ_ALL_*`.
WRITER_MATERIAL = re.compile(r"wr[-_]all[-_]", re.I)

READER_IDENTITY = re.compile(r"READ_ALL_[A-Z0-9_]*", re.I)
WRITER_IDENTITY = re.compile(r"WR_ALL_[A-Z0-9_]*", re.I)


class PolicyParseError(AssertionError):
    """The policy doc could not be read the way this guard expects."""


def policy_section(doc_text: str) -> str:
    """The 'Conductor auto-approval policy' section, or raise."""
    start = doc_text.find(POLICY_HEADING)
    if start < 0:
        raise PolicyParseError(
            f"section {POLICY_HEADING!r} not found in {SAFETY_DOC.name} — the "
            "heading was renamed. Update POLICY_HEADING in this module in the "
            "same PR; do not leave this guard reading nothing."
        )
    end = doc_text.find("\n## ", start + len(POLICY_HEADING))
    return doc_text[start:] if end < 0 else doc_text[start:end]


def parse_auto_approve_numbers(doc_text: str) -> set[int]:
    """The ✅ auto-approvable workflow numbers stated in the policy section."""
    section = policy_section(doc_text)
    matches = AUTO_APPROVE_SENTENCE.findall(section)
    if len(matches) != 1:
        raise PolicyParseError(
            f"expected exactly one auto-approve list sentence in the "
            f"{POLICY_HEADING!r} section, found {len(matches)}. The sentence "
            "this guard reads is the one naming the workflows the ruling still "
            "governs; if it was reworded, update AUTO_APPROVE_SENTENCE here in "
            "the same PR."
        )

    numbers: set[int] = set()
    for token in matches[0].split(","):
        token = token.strip()
        if not token:
            continue
        bounds = [b for b in re.split(r"[–—-]", token) if b.strip()]
        try:
            lo = int(bounds[0])
            hi = int(bounds[-1])
        except ValueError as exc:  # pragma: no cover - defensive
            raise PolicyParseError(f"unparsable workflow number {token!r}: {exc}") from exc
        if hi < lo:
            raise PolicyParseError(f"descending range {token!r} in the ✅ list")
        numbers.update(range(lo, hi + 1))

    if not numbers:
        raise PolicyParseError(
            "the ✅ auto-approve list parsed to an EMPTY set — this guard would "
            "then be vacuously green. Check the policy section's wording."
        )
    bad = sorted(n for n in numbers if not 100 <= n <= 999)
    if bad:
        raise PolicyParseError(f"not three-digit workflow numbers: {bad}")
    return numbers


# --- resolving the effective scope of a credential step ---------------------


def _load_workflow(filename: str) -> tuple[str, dict]:
    text = (WORKFLOWS / filename).read_text(encoding="utf-8-sig")
    return text, yaml.safe_load(text)


def workflow_files_by_number() -> dict[int, list[str]]:
    """display number -> every workflow file declaring it (see #917)."""
    found: dict[int, list[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8-sig")
        m = re.search(r"^name:\s*['\"]?(\d{3})\.", text, re.M)
        if m:
            found.setdefault(int(m.group(1)), []).append(path.name)
    return found


def action_scope_default(
    uses: str,
    actions_root: pathlib.Path = ACTIONS_ROOT,
) -> tuple[str | None, str]:
    """(declared default scope, explanation) for a local composite action.

    Returns (None, reason) when the default cannot be determined — a missing
    action, no `scope` input, or an input with no `default:` (which means the
    caller MUST pass one explicitly, e.g. cloudflare-tokens-from-kv's
    `required: true`). Never guesses `read`.
    """
    action_name = uses.rstrip("/").split("/")[-1]
    action_dir = actions_root / action_name
    action_file = next(
        (
            action_dir / candidate
            for candidate in ("action.yml", "action.yaml")
            if (action_dir / candidate).is_file()
        ),
        None,
    )
    if action_file is None:
        return None, f"no action.yml found for {uses!r} at {action_dir}"
    spec = yaml.safe_load(action_file.read_text(encoding="utf-8-sig")) or {}
    scope_input = ((spec.get("inputs") or {}).get("scope")) or {}
    if not isinstance(scope_input, dict):
        return None, f"{action_name}: malformed 'scope' input declaration"
    if "scope" not in (spec.get("inputs") or {}):
        return None, f"{action_name}: declares no 'scope' input, so an omitted scope is undefined"
    if "default" not in scope_input:
        return (
            None,
            f"{action_name}: 'scope' has no default (required: "
            f"{scope_input.get('required')!r}), so the caller must pass it explicitly",
        )
    return str(scope_input["default"]).strip().lower(), f"{action_name} action default"


def resolve_scope(
    uses: str,
    explicit: object | None,
    actions_root: pathlib.Path = ACTIONS_ROOT,
) -> tuple[str | None, str]:
    """(effective scope, how it was resolved). None = undeterminable."""
    if explicit is not None:
        value = str(explicit).strip()
        if "${{" in value:
            return None, f"scope is the expression {value!r}, which this guard cannot evaluate"
        return value.lower(), f"explicit `scope: {value}`"
    return action_scope_default(uses, actions_root)


def _is_kv_action(uses: str) -> bool:
    return uses.startswith("./.github/actions/") and uses.rstrip("/").endswith("-from-kv")


def credential_uses(wf: dict, source: str, _depth: int = 0) -> list[dict]:
    """Every `*-from-kv` step reachable from this workflow, including via
    reusable-workflow calls (`uses: ./.github/workflows/NNN-….yml`).

    Following `workflow_call` matters because a listed workflow could delegate
    its credential loading to another file (701 → 729 is the live precedent);
    stopping at the top level would report such a job as loading nothing.
    """
    out: list[dict] = []
    for job_id, job in (wf.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        called = job.get("uses")
        if isinstance(called, str) and called.startswith("./.github/workflows/"):
            # removeprefix, NOT lstrip: `lstrip("./")` strips every leading '.'
            # and '/' character, so "./.github/workflows/…" became
            # "github/workflows/…" and every reusable call reported "does not
            # exist". Caught by Copilot on #923 — see
            # test_reusable_workflow_calls_are_actually_followed.
            child = REPO_ROOT / called.removeprefix("./")
            if _depth >= 3:
                out.append(
                    {
                        "source": f"{source} -> {job_id}",
                        "job": job_id,
                        "uses": called,
                        "explicit": None,
                        "unfollowable": "reusable-workflow nesting deeper than 3 levels",
                    }
                )
                continue
            if not child.is_file():
                out.append(
                    {
                        "source": source,
                        "job": job_id,
                        "uses": called,
                        "explicit": None,
                        "unfollowable": f"called workflow {called} does not exist",
                    }
                )
                continue
            child_wf = yaml.safe_load(child.read_text(encoding="utf-8-sig")) or {}
            out.extend(credential_uses(child_wf, f"{source} -> {child.name}", _depth + 1))
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            uses = str(step.get("uses") or "")
            if _is_kv_action(uses):
                with_block = step.get("with") or {}
                out.append(
                    {
                        "source": source,
                        "job": job_id,
                        "uses": uses,
                        "explicit": with_block.get("scope"),
                        "client_id": str(with_block.get("azure-client-id") or ""),
                    }
                )
    return out


def _identity_family(client_id: str) -> str | None:
    if WRITER_IDENTITY.search(client_id):
        return "write"
    if READER_IDENTITY.search(client_id):
        return "read"
    return None


def audit_workflow(
    number: int,
    filename: str,
    wf: dict,
    actions_root: pathlib.Path = ACTIONS_ROOT,
) -> list[str]:
    """Reasons this workflow is NOT auto-approvable. Empty list = clean."""
    problems: list[str] = []
    uses = credential_uses(wf, filename)

    for entry in uses:
        if entry.get("unfollowable"):
            problems.append(
                f"{number} ({entry['source']}): job '{entry['job']}' — "
                f"{entry['unfollowable']}; its credentials cannot be resolved, "
                "so it must not be auto-approved."
            )
            continue
        scope, how = resolve_scope(entry["uses"], entry["explicit"], actions_root)
        action_name = entry["uses"].rstrip("/").split("/")[-1]
        if scope is None:
            problems.append(
                f"{number} ({entry['source']}): job '{entry['job']}' step "
                f"`{action_name}` — cannot determine the effective scope ({how}). "
                "An undeterminable scope is a failure, not a read."
            )
        elif scope != "read":
            problems.append(
                f"{number} ({entry['source']}): job '{entry['job']}' step "
                f"`{action_name}` resolves to scope '{scope}' via {how} — it "
                f"loads wr-all-* material on the writer identity. Not "
                "auto-approvable under condition 2 of the Conductor policy."
            )

    if not uses and number not in NO_KV_CREDENTIAL_WORKFLOWS:
        problems.append(
            f"{number} ({filename}): no `*-from-kv` credential step found. "
            "Either the workflow genuinely loads no Key Vault credential — then "
            "add it to NO_KV_CREDENTIAL_WORKFLOWS with the reason — or this "
            "guard's matcher has stopped seeing the step, in which case it is "
            "reporting a false clean."
        )

    for job_id, job in (wf.get("jobs") or {}).items():
        if not isinstance(job, dict):
            continue
        rendered = yaml.safe_dump(job, default_flow_style=False)
        if WRITER_MATERIAL.search(rendered):
            # Report the whole identifier, not just the matched prefix, so the
            # message names which credential — `wr-all-cbm-github-pat` and
            # `vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID` need different remediations.
            snippets = sorted(
                {
                    m.group(0)
                    for m in re.finditer(r"[\w./-]*wr[-_]all[-_][\w./-]*", rendered, re.I)
                }
            )
            problems.append(
                f"{number} ({filename}): job '{job_id}' references writer "
                f"material literally: {snippets}. Any wr-all-* credential "
                "disqualifies the job regardless of its safety level."
            )

    for entry in uses:
        if entry.get("unfollowable"):
            continue
        scope, _ = resolve_scope(entry["uses"], entry["explicit"], actions_root)
        family = _identity_family(entry.get("client_id", ""))
        action_name = entry["uses"].rstrip("/").split("/")[-1]
        if scope == "read" and family == "write":
            problems.append(
                f"{number} ({filename}): job '{entry['job']}' step "
                f"`{action_name}` asks for scope 'read' but authenticates as the "
                "WRITER identity (azure-client-id is a WR_ALL_* variable) — it "
                "holds writer access to the whole vault while reading the "
                "read-all copy."
            )

    return problems


# --- the live invariants ----------------------------------------------------


def test_auto_approve_list_parses_to_real_workflows():
    """The ✅ list must be readable, non-empty, and map to actual files."""
    numbers = parse_auto_approve_numbers(SAFETY_DOC.read_text(encoding="utf-8"))
    assert len(numbers) >= 3, (
        f"only {len(numbers)} workflow(s) parsed off the ✅ list ({sorted(numbers)}) "
        "— suspiciously few; check the sentence this guard reads."
    )
    by_number = workflow_files_by_number()
    missing = sorted(n for n in numbers if n not in by_number)
    assert not missing, (
        f"the ✅ list names workflows with no file declaring that number: {missing}"
    )
    duplicated = sorted(n for n in numbers if len(by_number[n]) > 1)
    assert not duplicated, (
        f"listed numbers declared by more than one workflow file: "
        f"{ {n: by_number[n] for n in duplicated} } — resolve the collision first (#917)"
    )


def test_auto_approvable_workflows_load_only_read_scoped_credentials():
    """The rule from #916, mechanized: condition 2 for every listed workflow."""
    numbers = parse_auto_approve_numbers(SAFETY_DOC.read_text(encoding="utf-8"))
    by_number = workflow_files_by_number()

    problems: list[str] = []
    for number in sorted(numbers):
        for filename in by_number.get(number, []):
            _, wf = _load_workflow(filename)
            problems.extend(audit_workflow(number, filename, wf))
    assert not problems, (
        "workflows on the Conductor ✅ auto-approve list that fail the "
        "credential-scope condition:\n  " + "\n  ".join(problems)
    )


def test_no_kv_credential_list_is_exact():
    """Every enumerated raw-secret workflow must still be on the ✅ list and
    still have no credential step — the list may not rot into an alibi."""
    numbers = parse_auto_approve_numbers(SAFETY_DOC.read_text(encoding="utf-8"))
    by_number = workflow_files_by_number()

    stale = sorted(NO_KV_CREDENTIAL_WORKFLOWS - numbers)
    assert not stale, (
        f"these are no longer on the ✅ auto-approve list: {stale} — remove them "
        "from NO_KV_CREDENTIAL_WORKFLOWS so it keeps meaning something."
    )
    migrated = []
    for number in sorted(NO_KV_CREDENTIAL_WORKFLOWS):
        for filename in by_number.get(number, []):
            _, wf = _load_workflow(filename)
            if credential_uses(wf, filename):
                migrated.append(f"{number} ({filename})")
    assert not migrated, (
        f"these now load a Key Vault credential: {migrated} — drop them from "
        "NO_KV_CREDENTIAL_WORKFLOWS so their scope is actually checked."
    )


def test_every_from_kv_action_scope_default_is_determinable():
    """Requirement 4: an unresolvable default must be visible at review time.

    Either the action declares a `default:` (whmcs/zeffy: 'write';
    candid/google/fraudlabspro: 'read') or it marks `scope` `required: true` so
    every caller must pass one (cloudflare-tokens-from-kv). A third state — no
    default and not required — would silently make every omitting caller
    undeterminable.
    """
    actions = sorted(p for p in ACTIONS_ROOT.iterdir() if p.name.endswith("-from-kv"))
    assert actions, f"no *-from-kv actions found under {ACTIONS_ROOT}"

    problems = []
    for action_dir in actions:
        spec = yaml.safe_load((action_dir / "action.yml").read_text(encoding="utf-8-sig")) or {}
        scope_input = (spec.get("inputs") or {}).get("scope")
        if not isinstance(scope_input, dict):
            problems.append(f"{action_dir.name}: declares no 'scope' input")
            continue
        has_default = "default" in scope_input
        if has_default and str(scope_input["default"]).strip().lower() not in {"read", "write"}:
            problems.append(
                f"{action_dir.name}: default scope {scope_input['default']!r} is "
                "neither read nor write"
            )
        if not has_default and scope_input.get("required") is not True:
            problems.append(
                f"{action_dir.name}: 'scope' has no default and is not required — "
                "callers that omit it get undefined behaviour"
            )
    assert not problems, "\n".join(problems)


def test_scope_and_oidc_identity_agree_across_every_workflow():
    """Tree-wide: the resolved scope and the identity that authenticates match.

    Not limited to the ✅ list, because a `scope: read` step on the WR_ALL_*
    identity is a privilege leak wherever it appears — and the tree is
    consistent today, so this costs nothing to hold.
    """
    problems = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
        if not isinstance(wf, dict):
            continue
        for entry in credential_uses(wf, path.name):
            if entry.get("unfollowable"):
                # Reported, never skipped. Skipping these here is what hid the
                # `lstrip` defect: every reusable call was unresolvable and this
                # tree-wide assertion stayed green by ignoring exactly the
                # entries that said so.
                problems.append(
                    f"{path.name}: job '{entry['job']}' — {entry['unfollowable']}"
                )
                continue
            action_name = entry["uses"].rstrip("/").split("/")[-1]
            if (path.name, entry["job"], action_name) in IDENTITY_SCOPE_EXCEPTIONS:
                continue
            scope, how = resolve_scope(entry["uses"], entry["explicit"])
            family = _identity_family(entry.get("client_id", ""))
            if scope is None:
                problems.append(f"{path.name}: job '{entry['job']}' {action_name} — {how}")
            elif family is None:
                problems.append(
                    f"{path.name}: job '{entry['job']}' {action_name} — cannot tell "
                    f"which identity authenticates from azure-client-id "
                    f"{entry.get('client_id')!r}"
                )
            elif family != scope:
                problems.append(
                    f"{path.name}: job '{entry['job']}' {action_name} resolves to "
                    f"scope '{scope}' ({how}) but authenticates as the "
                    f"'{family}' identity ({entry.get('client_id')})"
                )
    assert not problems, (
        "credential scope and OIDC identity disagree (add a documented row to "
        "IDENTITY_SCOPE_EXCEPTIONS only if the read identity genuinely lacks a "
        "federated credential for that environment):\n  " + "\n  ".join(problems)
    )


# --- proof the guard is not vacuously green --------------------------------


def test_the_three_held_workflows_would_fail_if_relisted():
    """#916 removed 114, 221 and 401. Putting any back must fail, by name."""
    expectations = {
        114: (
            "114-cloudflare-registrar-access-check.yml",
            "cloudflare-tokens-from-kv",
            "explicit",
        ),
        221: ("221-whmcs-application-search.yml", "whmcs-secrets-from-kv", "action default"),
        401: ("401-zeffy-campaigns-export.yml", "zeffy-secrets-from-kv", "action default"),
    }
    for number, (filename, action_name, how) in expectations.items():
        _, wf = _load_workflow(filename)
        problems = audit_workflow(number, filename, wf)
        assert problems, f"{number} was relisted and the guard passed it"
        blob = "\n".join(problems)
        assert action_name in blob, (
            f"{number}: the violation must name the resolved credential; got {blob!r}"
        )
        assert "scope 'write'" in blob, (
            f"{number}: message must name the resolved scope; got {blob!r}"
        )
        assert how in blob, f"{number}: message must say how the scope was resolved; got {blob!r}"

    # 221 is the shape the naive grep misses: its own text has no `wr-all-`
    # secret name, so the failure can only come from resolving the default.
    text, _ = _load_workflow("221-whmcs-application-search.yml")
    assert "wr-all-" not in text.lower(), (
        "221 has grown a literal wr-all- secret reference; the 'omission reads "
        "as safe' case this test pins now needs a different example"
    )


def test_reusable_workflow_calls_are_actually_followed():
    """The traversal must resolve a real `workflow_call` chain, not just claim to.

    Regression pin for the defect Copilot caught on #923: the first draft used
    `called.lstrip("./")`, which strips every leading '.' and '/' CHARACTER
    rather than the "./" prefix, so `./.github/workflows/501-….yml` resolved to
    `github/workflows/501-….yml`. Every reusable call therefore reported "does
    not exist" — and nothing failed, because no ✅-listed workflow delegates
    today and the tree-wide assertion skipped unresolvable entries. Two silences
    stacked: an untested code path, and a check that ignored its own "cannot
    resolve" signal. This test exercises the live 502 → 501 chain so the path
    cannot rot untested again.
    """
    filename = "502-google-analytics-report.yml"
    _, wf = _load_workflow(filename)
    entries = credential_uses(wf, filename)

    unfollowable = [e for e in entries if e.get("unfollowable")]
    assert not unfollowable, f"502's reusable call did not resolve: {unfollowable}"

    followed = [e for e in entries if "501-google-api-smoke.yml" in e["source"]]
    assert followed, (
        "502 calls ./.github/workflows/501-google-api-smoke.yml, whose smoke job "
        f"loads google-secrets-from-kv — the traversal found none of it: {entries}"
    )
    scope, how = resolve_scope(followed[0]["uses"], followed[0]["explicit"])
    assert scope == "read", (
        f"501's credential step resolved to {scope!r} via {how} — expected read"
    )

    # And the negative: a call to a file that really is absent must be reported,
    # so a broken delegation can never read as "loads nothing".
    broken = yaml.safe_load(
        "jobs:\n  child:\n    uses: ./.github/workflows/000-does-not-exist.yml\n"
    )
    problems = audit_workflow(999, "synthetic.yml", broken)
    assert any("does not exist" in p for p in problems), problems


def test_an_explicit_write_scope_in_a_listed_workflow_fails():
    """Condition 2 must also catch a regression in an already-clean workflow."""
    filename = "104-domain-export-inventory.yml"
    _, clean = _load_workflow(filename)
    assert not audit_workflow(104, filename, clean), "104 is clean on the real tree"

    tampered = copy.deepcopy(clean)
    step = next(
        s
        for s in tampered["jobs"]["export_whmcs"]["steps"]
        if _is_kv_action(str(s.get("uses") or ""))
    )
    step["with"]["scope"] = "write"
    problems = audit_workflow(104, filename, tampered)
    assert any("resolves to scope 'write'" in p for p in problems), (
        f"a `scope: write` regression in a listed workflow was not caught: {problems}"
    )


def test_an_unresolvable_action_default_fails_rather_than_defaulting_to_read():
    """Requirement 4, at the resolver: no default is not `read`."""
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        cases = {
            "no-default-from-kv": "inputs:\n  scope:\n    required: true\n",
            "no-scope-input-from-kv": "inputs:\n  vault-name:\n    default: 'kv'\n",
            "read-default-from-kv": "inputs:\n  scope:\n    default: 'read'\n",
            "write-default-from-kv": "inputs:\n  scope:\n    default: 'write'\n",
        }
        for name, body in cases.items():
            (root / name).mkdir()
            (root / name / "action.yml").write_text(f"name: {name}\n{body}", encoding="utf-8")

        assert action_scope_default("./.github/actions/no-default-from-kv", root)[0] is None
        assert action_scope_default("./.github/actions/no-scope-input-from-kv", root)[0] is None
        assert action_scope_default("./.github/actions/missing-from-kv", root)[0] is None
        assert action_scope_default("./.github/actions/read-default-from-kv", root)[0] == "read"
        assert action_scope_default("./.github/actions/write-default-from-kv", root)[0] == "write"

        # …and an undeterminable scope must surface as a violation, not a pass.
        wf = yaml.safe_load(
            "jobs:\n"
            "  audit:\n"
            "    steps:\n"
            "      - uses: ./.github/actions/no-default-from-kv\n"
            "        with:\n"
            "          azure-client-id: ${{ vars.READ_ALL_FFC_AZURE_KV_CLIENT_ID }}\n"
        )
        problems = audit_workflow(999, "synthetic.yml", wf, actions_root=root)
        assert any("cannot determine the effective scope" in p for p in problems), problems


def test_an_expression_scope_is_unresolvable_not_assumed_read():
    scope, why = resolve_scope(
        "./.github/actions/whmcs-secrets-from-kv", "${{ inputs.credential_scope }}"
    )
    assert scope is None and "cannot evaluate" in why, (scope, why)


def test_a_literal_wr_all_reference_fails_in_either_spelling():
    """Both the KV secret name and the writer-identity Variable disqualify."""
    for literal in ("wr-all-cbm-github-pat", "${{ vars.WR_ALL_FFC_AZURE_KV_CLIENT_ID }}"):
        wf = yaml.safe_load(
            "jobs:\n"
            "  report:\n"
            "    steps:\n"
            "      - run: |\n"
            f"          echo '{literal}'\n"
        )
        problems = audit_workflow(999, "synthetic.yml", wf)
        assert any("references writer material literally" in p for p in problems), (
            f"{literal!r} was not flagged: {problems}"
        )

    # The mirror image: read-all material must NOT trip the pattern, or the
    # guard is red on every correct workflow and gets deleted.
    for benign in ("read-all-cbm-github-pat", "vars.READ_ALL_FFC_AZURE_TENANT_ID"):
        assert not WRITER_MATERIAL.search(benign), f"false positive on {benign!r}"


def test_a_missing_credential_step_is_a_failure_not_a_pass():
    """The vacuous-green case: an unlisted workflow with nothing to check."""
    wf = yaml.safe_load("jobs:\n  audit:\n    steps:\n      - uses: actions/checkout@v7.0.1\n")
    problems = audit_workflow(998, "synthetic.yml", wf)
    assert any("no `*-from-kv` credential step found" in p for p in problems), problems


def test_policy_parsing_fails_on_a_renamed_or_reformatted_section():
    """A doc edit must break this loudly, never silently empty the list."""
    doc = SAFETY_DOC.read_text(encoding="utf-8")
    assert parse_auto_approve_numbers(doc), "sanity: the real doc must parse"

    renamed = doc.replace(POLICY_HEADING, "## Conductor approval policy (renamed)")
    try:
        parse_auto_approve_numbers(renamed)
    except PolicyParseError as exc:
        assert "not found" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a renamed section still parsed")

    section = policy_section(doc)
    reformatted = doc.replace(
        section, AUTO_APPROVE_SENTENCE.sub("gated reads are listed above.", section)
    )
    try:
        parse_auto_approve_numbers(reformatted)
    except PolicyParseError as exc:
        assert "found 0" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("a reformatted list sentence still parsed")


def test_parsing_expands_ranges_and_rejects_an_empty_list():
    doc = (
        f"{POLICY_HEADING}\n\nblah blah the remaining **dispatch-only** gated "
        "reads, which after the tightening above are **101, 104, 301–303, 601**.\n"
    )
    assert parse_auto_approve_numbers(doc) == {101, 104, 301, 302, 303, 601}

    empty = f"{POLICY_HEADING}\n\ngated reads, which after the tightening above are **   **.\n"
    try:
        parse_auto_approve_numbers(empty)
    except PolicyParseError:
        pass
    else:  # pragma: no cover
        raise AssertionError("an empty ✅ list parsed as valid")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:2000]}")
    sys.exit(1 if failures else 0)
