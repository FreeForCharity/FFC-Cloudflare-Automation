"""Tests for the free-text dispatch-input interpolation guard (#1080).

The subject is `scripts/check-workflow-input-interpolation.py`. What it claims:
a `workflow_dispatch` input the dispatcher types freely never reaches a `run:`
or `github-script` body through `${{ }}`, except for the call sites frozen in
`KNOWN_UNGUARDED` — and that freeze is exact in both directions.

The two failure modes worth designing against, both seen in this repo before:

* **Vacuous green.** A sweep whose input set silently empties passes by
  inspecting nothing. `test_the_scan_sees_real_workflows` and
  `test_the_free_text_set_is_not_empty` pin the input, and every helper that
  filters is given a positive control before anything is built on it.
* **A guard that cannot fail.** Reading the checker proves it is wired, never
  that it detects (AGENTS.md). So the tree-level assertions are backed by
  mutations: each acceptance criterion of #1080 that says "X makes it exit 1"
  is exercised by actually doing X.

The false-positive surface gets its own module-level attention, because
`env:` is the REMEDY for this defect — a guard that flagged it would have to be
switched off to land the fix for the thing it guards (ledger L27).
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import REPO_ROOT  # noqa: E402

CHECKER = REPO_ROOT / "scripts" / "check-workflow-input-interpolation.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_wf_input_interp", CHECKER)
    # Narrower than it looks, and measured rather than assumed: for a merely
    # MISSING .py file `spec_from_file_location` still returns a usable spec and
    # `exec_module` raises FileNotFoundError naming the path, which is already a
    # perfect diagnosis. `spec` is None only when the path is not a recognised
    # Python source file at all — renamed to another extension, given no
    # extension, or pointed at a directory. In those cases `module_from_spec(None)`
    # raises `AttributeError: 'NoneType' object has no attribute '__name__'`,
    # which names neither the path nor the cause, at module import time — so
    # run_all.py reports "defines 20 tests and reported an outcome for none of
    # them" and the reader hunts a broken test instead of a moved file.
    assert spec is not None and spec.loader is not None, (
        f"cannot import the checker at {CHECKER} — the path is not an importable "
        "Python source file (wrong extension, or a directory). If it was renamed, "
        "update CHECKER above."
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load()


def _workflow(name: str) -> dict:
    """Parse a real workflow by file name.

    `or {}` because `yaml.safe_load` returns None for an empty document, and
    every consumer here expects a mapping. Factored into one place after review
    found the fallback applied at one of two identical call sites — which is the
    shape that makes a later reader think the guarded one knows something the
    other does not.
    """
    return guard.yaml.safe_load(
        (guard.WORKFLOWS / name).read_text(encoding="utf-8")
    ) or {}


def _scan_text(text: str) -> list:
    """Findings for a synthetic workflow. Returns Finding objects, not strings."""
    with tempfile.TemporaryDirectory() as tmp:
        sample = pathlib.Path(tmp) / "999-sample.yml"
        sample.write_text(text, encoding="utf-8")
        return guard.scan_workflow(sample)


def _input_names(findings) -> set[str]:
    return {f.input_name for f in findings}


def _a_frozen_subject(current: dict) -> tuple[str, tuple[str, ...]]:
    """Any workflow that is BOTH frozen and still interpolating, with its inputs.

    The per-input freeze tests need a live subject to perturb. Naming one
    (`720-create-repo.yml` until it was burned down) makes those tests depend on
    a call site whose whole purpose is to stop existing: once it is fixed,
    `current` no longer holds it, both perturbations become no-ops against an
    absent key, and the tests report a vacuous result rather than a failure —
    the same silent-zero shape the freeze itself exists to prevent.

    Deriving the subject keeps them meaningful for the rest of the burn-down.
    The assertion is the positive control: if the intersection is ever empty the
    burn-down is complete, and these two tests must be deleted rather than left
    passing over nothing.
    """
    for workflow in sorted(guard.KNOWN_UNGUARDED):
        inputs = tuple(current.get(workflow, ()))
        if inputs:
            return workflow, inputs
    raise AssertionError(
        "no workflow is both frozen and still interpolating — if the burn-down "
        "is finished, delete the per-input freeze tests instead of leaving them "
        "asserting over an empty set"
    )


# --------------------------------------------------------------------------
# Samples
# --------------------------------------------------------------------------

# The real pre-fix shape from 720-create-repo.yml:274, kept close to verbatim.
# A synthetic payload would drift from the defect; this one is the defect.
_DEFECT_BASH = """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      RepoName:
        type: string
jobs:
  repo:
    runs-on: ubuntu-latest
    environment: github-prod
    steps:
      - name: Create repo
        shell: bash
        run: |
          repo_input="${{ inputs.RepoName }}"
"""

# 101-domain-status.yml:684 — a `number` input in a SINGLE-quoted JS literal
# inside a github-script step. Criterion 6 plus the `number` widening at once.
_DEFECT_GITHUB_SCRIPT = """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      issue_number:
        type: number
jobs:
  report:
    runs-on: ubuntu-latest
    environment: m365-prod
    steps:
      - uses: actions/github-script@v7
        with:
          script: |
            const issueNumber = Number('${{ inputs.issue_number }}');
"""

# 702-ffc-ex-clone-deploy.yml:271 — the input reaches the script through a
# `format()` inside a larger expression, not the bare `${{ inputs.x }}` spelling.
_DEFECT_NESTED_EXPRESSION = """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      exclude:
        type: string
        default: ''
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: github-prod
    steps:
      - run: |
          ./capture.sh ${{ inputs.exclude && format('--exclude {0}', inputs.exclude) || '' }}
"""

_CLEAN_SAMPLES = [
    (
        "env: is the remedy and must never be flagged",
        """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  run:
    runs-on: ubuntu-latest
    environment: cloudflare-prod-write
    steps:
      - name: Safe
        env:
          DOMAIN: ${{ inputs.domain }}
        run: |
          pwsh -File ./x.ps1 -Zone $env:DOMAIN
""",
    ),
    (
        "with: is an action input, not script text",
        """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: ./.github/actions/thing
        with:
          zone: ${{ inputs.domain }}
      - run: echo done
""",
    ),
    (
        "if: is evaluated by the expression engine, never substituted into a script",
        """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  run:
    if: ${{ inputs.domain != '' }}
    runs-on: ubuntu-latest
    steps:
      - run: echo done
""",
    ),
    (
        "a boolean input is constrained by GitHub and carries no payload",
        """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      dry_run:
        type: boolean
        default: true
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: echo "dry=${{ inputs.dry_run }}"
""",
    ),
    (
        "a choice input is constrained to an option the workflow itself wrote",
        """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      mode:
        type: choice
        options: [audit, apply]
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: echo "mode=${{ inputs.mode }}"
""",
    ),
    (
        "a non-input expression is out of scope — this rule is about dispatch inputs",
        """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: echo "${{ github.run_id }} ${{ github.sha }}"
""",
    ),
    (
        "a shell variable is not an expression",
        """
name: '999. Sample'
on:
  workflow_dispatch:
    inputs:
      domain:
        type: string
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - run: |
          d="${RANDOM}"
          echo "$env:GH_TOKEN is pwsh, ${d} is bash"
""",
    ),
]


# --------------------------------------------------------------------------
# The burn-down ledger
#
# `BURNED_DOWN` is the ONE place a #1080 lane records itself, and both counts
# below are derived from it rather than written down again.
#
# They used to be two literals inside the test, which made this file a mutex on
# the burn-down: every lane rewrote the same two integers to the same value, so
# two lanes branched off one `main` merged them SILENTLY at the first lander's
# number — outside any conflict marker — and the follower went red on a line
# the resolver was never shown (#1210).
#
# The counts are not independent facts. A burn-down removes exactly one
# interpolating workflow, so the first baseline decrements with
# `len(BURNED_DOWN)`. The write baseline does NOT: it decrements only for the
# burned entries that actually enter a write environment, which is re-derived
# from the tree rather than assumed. Every entry so far happens to be one, so
# the two move together today and would silently keep doing so if this were
# written as `- len(BURNED_DOWN)` — and the first read-only burn-down would
# then fail a correct tree, which reads as "the ledger is wrong" rather than
# "the arithmetic is". A lane appends ONE line here and both follow, in any
# landing order.
# --------------------------------------------------------------------------

FROZEN_BASELINE_CURRENT = 36  # the #1122 freeze, before any #1080 burn-down
FROZEN_BASELINE_WRITE = 21

BURNED_DOWN = (
    "720-create-repo.yml",
    "120-bulk-cutover-to-github-pages.yml",
    "112-dns-bulk-replace-a-ip.yml",
    "704-website-analytics-wire.yml",
    "304-m365-dkim-enable.yml",
    "303-m365-domain-and-dkim.yml",
    "301-m365-domain-preflight.yml",
    "110-cloudflare-zone-create.yml",
    "103-enforce-domain-standard.yml",
    "306-discover-uncaptured-comms.yml",
    "119-bulk-staging-cname-github-pages.yml",
    "205-whmcs-ticket-open.yml",
    "101-domain-status.yml",
    "118-whmcs-domain-lock.yml",
    "102-domain-add-ffc-cloudflare-and-whmcs.yml",
)


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


def _expected_write(burned) -> int:
    """The write baseline decrements only for burned entries that enter a write
    environment — NOT for every entry. Both the assertion and its polarity
    control call this, so a revert to `len(burned)` is caught by the control
    rather than waiting for the first read-only burn-down to false-red."""
    return FROZEN_BASELINE_WRITE - len({w for w in burned if _is_write(w)})


def _is_write(workflow: str) -> bool:
    """Does this workflow enter a write environment? Read from the tree, never
    assumed — a burned-down entry keeps its environments, which is what lets the
    write baseline decrement for the right entries and only those."""
    return any(
        guard.is_write_environment(e) for e in guard.environments(_workflow(workflow))
    )


def test_the_tree_matches_the_freeze_exactly():
    """#1080 criterion 1: the guard exits 0 with the allowlist populated."""
    findings, unreadable, _ = guard.scan_all()
    assert not unreadable, f"workflows that would not parse: {unreadable}"
    new, stale = guard.compare(guard.current_map(findings))
    assert not new and not stale, (
        "the tree and KNOWN_UNGUARDED disagree.\n"
        + "\n".join(["NEW: " + n for n in new] + ["STALE: " + s for s in stale])
    )


def test_the_frozen_counts_are_what_1080_reconciles_to():
    """The denominator is pinned, so a silent collapse in either direction fails.

    32 / 17, and every step away from #1080's 34 / 20 is accounted for rather
    than adopted:

      34 / 20  #1080's table (string-typed inputs, bare `${{ inputs.X }}`)
      +229     landed after the table was written, which #1080 anticipates
      +115     all-`number` inputs, invisible to a string-only count
      = 36 / 21 as frozen by #1122
      -720     burned down: its three free-text inputs moved to step-level
               `env:`. It was the only entry #1080 reproduced live in both
               directions, and it ran on github-prod.
      = 35 / 20
      -120     burned down: `domains` moved to step-level `env:` in all four
               bodies. It was the only entry holding two write environments at
               once (cloudflare-prod-write and github-prod).
      = 34 / 19
      -112     burned down: `old_ip` / `new_ip` moved to step-level `env:`. It
               rewrites A records across every zone in both Cloudflare accounts
               on cloudflare-prod-write, and the callee's own `[ValidatePattern]`
               on both parameters was never a defence — it runs after the
               injected expression, so its rejection message reads like a
               control working on a run where the payload had already executed.
      = 33 / 18
      -704     burned down: `gtm_id` / `measurement_id` moved to step-level
               `env:`. Its only interpolating step was the one named `Validate
               inputs`, which checked the id by pasting it into the program
               doing the checking — and the exploited run exits 0, because a
               payload whose residue is a legal GTM id passes the very check it
               is smuggled through. github-prod, one step before the PAT loads.
      = 32 / 17
      -304     burned down: `domain` moved to step-level `env:` in all three
               bodies. It was the only remaining entry interpolating one input
               into three jobs under two gated environments (m365-prod twice,
               cloudflare-prod-write once), and the only one whose injection
               point sat in a step whose OWN `env:` carried the credential —
               the Exchange Online certificate. Measured: the payload wrote the
               cert password to a file, the callee was then handed a legal
               domain, and the step exited 0.
      = 31 / 16
      -303     burned down: `domain` moved to step-level `env:` in both pwsh
               bodies. Its two call sites sit in ONE m365-prod job, and the
               credential exposure is broader than 304's rather than narrower:
               the JOB-level `env:` maps EXO_CERT_PFX_BASE64 and
               EXO_CERT_PFX_PASSWORD, so the certificate was in the process
               environment of both injection points — including the `status`
               step, which maps no secret of its own and reads as the harmless
               half of the workflow. Measured from that step: the payload wrote
               the cert password to a file, the callee was handed a legal
               domain, and the step exited 0.
      = 30 / 15
      -301     burned down: `domain` moved to step-level `env:` in both pwsh
               bodies. Its two call sites sit in TWO jobs under TWO environments
               — `graph` on m365-prod and `cloudflare` on cloudflare-prod-read,
               the second `needs:` the first — so one payload in one dispatch
               ran in both. What distinguishes it from 303/304 is what sat in
               the first injection point's own `env:`: not a value from the
               secrets store but GRAPH_ACCESS_TOKEN, a live Microsoft Graph
               bearer token mapped from a step OUTPUT, which a sweep of the
               file for secret references does not see. The second site
               inherits the Key Vault Cloudflare tokens the composite action
               exports through GITHUB_ENV, which appear in no `env:` block at
               all. Measured at both: the payload wrote the credential to a
               file, the callee was handed a legal domain, and the step exited
               0.
      = 29 / 14
      -110     burned down: `domain` moved to step-level `env:` at BOTH branches
               of the step's `jump_start` conditional — two call sites in one
               step, not two steps. The first lane whose injection point sat in
               a process holding WRITE-scoped Cloudflare tokens for both
               accounts: the job is cloudflare-prod-write and calls
               cloudflare-tokens-from-kv with `scope: write`, which exports
               CLOUDFLARE_API_TOKEN_FFC and _CM through GITHUB_ENV, so the
               credential in reach appears in no `env:` block in the file.
               Measured in both branches: the payload wrote the write-scoped
               token to a file, the callee was handed a legal domain, and the
               step exited 0. Its `account` / `zone_type` stay interpolated and
               are not findings — both are `type: choice`.
      = 28 / 13
      -103     burned down: `domain` moved to step-level `env:` in all four pwsh
               bodies, and `domain` + `issue_number` in the `github-script` one.
               The widest entry in the freeze by blast radius — SIX call sites,
               FIVE jobs, TWO distinct gated write environments
               (cloudflare-prod-write twice, m365-prod twice) — so one payload
               ran four times under two production credentials on one approval.
               The first lane with a `github-script` site, and the first where
               the credential is reachable BOTH ways: the Cloudflare jobs hide
               their tokens in GITHUB_ENV while both m365-prod steps name the
               Exchange Online certificate in the SAME step's `env:` as the
               injection point. `issue_number` is `type: number`, which is NOT a
               constrained type — GitHub validates `boolean` and `choice` only —
               so it was moved for the same reason as `domain`, not as a
               courtesy. Its four `boolean` inputs stay interpolated and are not
               findings.
      = 27 / 12
      -306     burned down: `mailboxes` / `since_days` moved to step-level
               `env:`. The credential in reach appears NOWHERE in the file —
               the preceding step mints a Graph bearer token from the OIDC
               login and exports it through GITHUB_ENV — so reading the step's
               own `env:` (L213) and grepping the body for `secrets.` (#1141)
               both score this workflow as holding nothing, and it is the
               workflow's ONLY credential. It is also the first lane whose
               injection point sat in SINGLE quotes, where `$( )` does not
               expand: the payload every earlier lane used is inert here and
               reports the pre-fix body as harmless, so the control had to
               close the quote, steal, and re-issue the legitimate call.
               Measured: the bearer token was written to a file, the scanner
               was then handed a legal mailbox list, and the step exited 0.
      = 26 / 11
      -119     burned down: `domains` / `target` moved to step-level `env:`. It
               writes `staging.<domain>` in every FFC-EX zone across BOTH
               Cloudflare accounts on cloudflare-prod-write, with the
               write-scoped tokens arriving through GITHUB_ENV. The first lane
               where the remedy every earlier one used would have been WRONG:
               `domains` is `required: false` with an empty default and a
               documented fallback to a 13-domain list, so a fail-closed guard
               on empty breaks the common path. It keeps the fallback and omits
               `Target` from the splat when blank rather than passing an empty
               string, because the callee already resolves the canonical Pages
               host (#778). `dry_run` stays interpolated: `type: boolean`.
      = 25 / 10
      -205     burned down: `deptid` / `client_id` moved to step-level `env:`
               (TICKET_DEPTID / TICKET_CLIENT_ID). It runs on whmcs-prod, and
               the WHMCS API credential the preceding `whmcs-secrets-from-kv`
               action exports through GITHUB_ENV is the workflow's ONLY
               credential — invisible to both the L213 `env:` read and the
               #1141 `secrets.` grep. Its injection point sat in SINGLE quotes
               like 306, so the `$( )` payload is inert and the control had to
               close the quote and re-open the array. Measured: the WHMCS secret
               was written to a file, the callee was handed a legal deptid, and
               the step exited 0. `priority` (choice) and `dry_run` (boolean)
               stay interpolated and are not findings.
      = 24 / 9
      -101     burned down: `domain` moved to step-level `env:` in all FIVE pwsh
               bodies and `issue_number` in the `github-script` body — six call
               sites in four jobs off one dispatch, the widest entry left after
               103. It is the lane where the #1188 reachability index gave the
               MISLEADING answer rather than merely a partial one: asked about
               101 it named two `cloudflare` steps and reported nothing at the
               two `m365` sites, which are the sites carrying `m365-prod` and
               so the only reason 101 was a `[W]` entry at all. An L213 read and
               a `secrets.` grep agree with it, and all three are wrong the same
               way — the preceding step is `azure/login@v3` and both bodies then
               run `az account get-access-token`, so the credential is a CLI
               SESSION rather than a value in a variable, and every sweep the
               burn-down has built is defined over variables. Measured with
               every credential variable removed from the environment: the
               payload ran `az` itself, wrote a Graph bearer token to a file,
               the callee was then handed a legal domain, and the step exited 0.
               Filed as #1208; ledger L248.
      = 23 / 8
      -118     burned down: `domain` moved to step-level `env:` (IN_DOMAIN). It
               runs on whmcs-prod and CHANGES a registrar lock on a production
               domain, with the WHMCS credential arriving through GITHUB_ENV
               from `whmcs-secrets-from-kv` — the workflow's only credential,
               invisible to both the L213 `env:` read and the #1141 `secrets.`
               grep, as 205's was. Its injection point sat in SINGLE quotes, so
               the `$( )` payload is inert and the control had to close the
               quote and the hashtable and re-open both. Measured: the WHMCS
               secret was written to a file, the callee was then handed a legal
               domain, and the step exited 0. It is the first lane where the
               fail-closed guard fixes a defect the interpolation was HIDING
               rather than one the remedy introduced: the body splats a
               hashtable onto a native `pwsh -File`, hashtable order is
               undefined, and a blank `Domain` was dropped so `-Domain`
               swallowed the next token — 3 of 8 runs bound
               `Domain=[-DryRun:True]` with `DryRun=[False]` at exit 0, the
               other 5 failed loudly. Ledger L254. `action` (choice) and
               `dry_run` (boolean) stay interpolated and are not findings.
      = 22 / 7
      -102     burned down: `domain` moved to step-level `env:` in both pwsh and
               both bash bodies, `domain` + `issue_number` in the
               `github-script` body. FOUR jobs, THREE environments —
               whmcs-prod twice, cloudflare-prod-write twice,
               cloudflare-prod-read once — so one payload ran under two
               production credential families on one approval, with both the
               WHMCS and the Cloudflare credentials arriving through GITHUB_ENV
               and so invisible to the L213 `env:` read and the #1141
               `secrets.` grep alike.

               It is the lane that showed a freeze entry is not the whole call
               site. The four `inputs.` sites this list named are the FIRST hop:
               `inputs.domain` is published as a step output and
               re-interpolated into SEVEN more script bodies, and
               `.Trim().ToLowerInvariant().Trim('.')` removes nothing that
               matters. Measured — with `Metadata` remedied the house way, the
               payload arrived as data THERE, went verbatim into GITHUB_OUTPUT,
               and executed one step later at
               `$domain = "${{ steps.meta.outputs.domain }}"`: WHMCS secret
               stolen, `-Domain` still bound to a legal `example.org`, exit 0.
               So a lane fixing only what this guard can see would have removed
               the entry, gone green, and left the injection working. The guard
               is defined over `inputs.X` and cannot see the laundering; filed
               as #1233 rather than absorbed here. Ledger L255.
      = 21 / 6

    Pinned so that a silent collapse in either direction fails. If someone
    narrows the type rule back to string-only, this says which workflows just
    went dark instead of quietly reporting a smaller, tidier set — and if a
    burn-down lands without updating this number, it says that too.
    """
    findings, _, scanned = guard.scan_all()
    current = guard.current_map(findings)
    assert scanned >= 90, f"only {scanned} workflow files scanned — the glob broke"
    assert len(BURNED_DOWN) == len(set(BURNED_DOWN)), (
        "BURNED_DOWN holds a duplicate entry: "
        f"{sorted({w for w in BURNED_DOWN if BURNED_DOWN.count(w) > 1})}. "
        "Both baselines are derived from its contents, so a line union-resolved "
        "in twice would move the expected numbers rather than the tree."
    )
    expected_current = FROZEN_BASELINE_CURRENT - len(BURNED_DOWN)
    assert len(current) == expected_current, (
        f"expected {expected_current} interpolating workflows, got "
        f"{len(current)}: {sorted(current)}"
    )
    write = {w for w in current if _is_write(w)}
    expected_write = _expected_write(BURNED_DOWN)
    assert len(write) == expected_write, (
        f"expected {expected_write} write-environment ones, got {len(write)}: "
        f"{sorted(write)}"
    )
    for expected in ("229-whmcs-client-field-populate.yml", "115-domain-transfer-preflight.yml"):
        assert expected in current, f"{expected} must be in the frozen set"
    for burned in BURNED_DOWN:
        assert burned not in current, (
            f"{burned} was burned down — it must no longer interpolate any "
            "free-text input"
        )


def test_the_write_baseline_decrements_only_for_write_entries():
    """Polarity control for the write baseline — it cannot be measured today.

    Every entry in BURNED_DOWN so far enters a write environment, so
    `FROZEN_BASELINE_WRITE - len(BURNED_DOWN)` and the correct
    `- len(write subset)` return the SAME number on this tree. The module going
    green is therefore no evidence that the right one is in use, and the wrong
    one fails on a correct tree the first time a read-only workflow is burned
    down — a false red that reads as "the ledger is wrong" rather than "the
    arithmetic is". Found by review on #1211, before that could happen.

    So this pins the difference directly, with the read-only entry taken from
    the tree rather than invented.
    """
    burned_write = {w for w in BURNED_DOWN if _is_write(w)}
    assert burned_write, (
        "positive control: no BURNED_DOWN entry enters a write environment, so "
        "this test would pass by comparing two empty sets"
    )
    # Must not already be in BURNED_DOWN: appending a duplicate leaves both
    # baselines unmoved for the wrong reason, and the test would pass while
    # proving nothing. Sound on today's tree (all 13 burned entries are write),
    # which is exactly why it needs pinning rather than trusting.
    read_only = [
        p.name
        for p in guard.workflow_paths()
        if not _is_write(p.name) and p.name not in BURNED_DOWN
    ]
    assert read_only, (
        "positive control: every workflow in the tree reads as write-environment "
        "or is already burned down, so the case this test exists for cannot be "
        "constructed"
    )
    hypothetical = BURNED_DOWN + (read_only[0],)
    assert _expected_write(hypothetical) == _expected_write(BURNED_DOWN), (
        f"{read_only[0]} enters no write environment, so burning it down must "
        "not move the write baseline — `_expected_write` is counting every "
        "entry rather than the write ones"
    )
    assert (
        FROZEN_BASELINE_CURRENT - len(hypothetical)
        == FROZEN_BASELINE_CURRENT - len(BURNED_DOWN) - 1
    ), (
        "…while it MUST move the interpolating baseline — if this side stops "
        "holding, the two baselines have been collapsed back into one"
    )


def test_the_scan_sees_real_workflows():
    """Guard the sweep's INPUT, so it cannot pass by reading nothing.

    `on:` parses to the YAML 1.1 boolean `True` (the Norway problem). If the
    lookup in `dispatch_inputs` ever stops handling that, every workflow returns
    no inputs, every finding disappears, and `compare()` would then report 36
    stale entries rather than going quietly green — but only because the freeze
    is non-empty. This pins the input directly instead of relying on that.
    """
    paths = guard.workflow_paths()
    assert len(paths) >= 90, f"only {len(paths)} workflow files found"
    with_dispatch = [
        p
        for p in paths
        if guard.dispatch_inputs(
            guard.yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        )
    ]
    assert len(with_dispatch) >= 50, (
        f"only {len(with_dispatch)} workflows appear to declare dispatch inputs; "
        "the `on:`/`True` key handling probably broke"
    )


def test_the_free_text_set_is_not_empty_and_excludes_the_constrained_types():
    """Positive control for `free_text_inputs` before anything relies on it."""
    parsed = _workflow("720-create-repo.yml")
    free = guard.free_text_inputs(parsed)
    assert {"RepoName", "Description"} <= free, (
        f"720's free-text inputs should include RepoName/Description, got {sorted(free)}"
    )
    types = guard.dispatch_inputs(parsed)
    for name, declared in types.items():
        if declared in guard.CONSTRAINED_TYPES:
            assert name not in free, f"{name} is {declared} and must not be free text"


# --------------------------------------------------------------------------
# Detection — criteria 3, 4, 6
# --------------------------------------------------------------------------


def test_a_free_text_input_in_a_run_block_is_a_finding():
    """#1080 criterion 3, on the real 720 line."""
    findings = _scan_text(_DEFECT_BASH)
    assert len(findings) == 1, f"expected exactly one finding, got {[str(f) for f in findings]}"
    assert findings[0].input_name == "RepoName"
    assert findings[0].kind == "run"
    assert findings[0].line, "the finding must carry a line number"


def test_a_github_script_body_is_covered():
    """#1080 criterion 6: `script:` is script text too, on the real 101 line."""
    findings = _scan_text(_DEFECT_GITHUB_SCRIPT)
    assert len(findings) == 1, f"expected one finding, got {[str(f) for f in findings]}"
    assert findings[0].kind == "github-script"
    assert findings[0].input_name == "issue_number"


def test_a_number_input_counts_as_free_text():
    """The deliberate widening past #1080's string-only table.

    GitHub documents no constraint on `number` (it says `choice` "resolves to a
    string" and nothing equivalent for `number`), so treating it as constrained
    would be an assumption in the unsafe direction — and 101's real call site
    puts one inside a single-quoted JS literal on an `m365-prod` job.
    """
    assert "number" not in guard.CONSTRAINED_TYPES
    assert _input_names(_scan_text(_DEFECT_GITHUB_SCRIPT)) == {"issue_number"}


def test_an_unrecognised_type_is_treated_as_free_text():
    """Fail-safe default: a type nobody classified must not be waved through."""
    sample = _DEFECT_BASH.replace("type: string", "type: some-future-type")
    assert "some-future-type" in sample, "the mutation did not apply"
    assert _input_names(_scan_text(sample)) == {"RepoName"}


def test_an_input_reached_through_a_nested_expression_is_a_finding():
    """702:271 — the call site #1080's own bare-spelling pattern missed."""
    findings = _scan_text(_DEFECT_NESTED_EXPRESSION)
    assert findings, "an input inside format() still reaches the script text"
    assert _input_names(findings) == {"exclude"}


def test_the_deprecated_github_event_inputs_spelling_is_covered():
    sample = _DEFECT_BASH.replace("inputs.RepoName", "github.event.inputs.RepoName")
    assert "github.event.inputs.RepoName" in sample, "the mutation did not apply"
    assert _input_names(_scan_text(sample)) == {"RepoName"}


def test_correct_code_is_left_alone():
    """The false-positive surface. Three of these are the remedy itself."""
    misfires = []
    for label, text in _CLEAN_SAMPLES:
        findings = _scan_text(text)
        if findings:
            misfires.append(f"{label}: {[str(f) for f in findings]}")
    assert not misfires, "\n".join(misfires)


def test_the_clean_samples_would_fail_if_the_scanner_flagged_everything():
    """Positive control for the sample set above.

    A clean-sample list only means something if the samples are close enough to
    the defect that a broken scanner would trip on them. Each one is re-run with
    its safe position rewritten into a `run:` body; every sample that carries an
    input must then be flagged. Without this, `test_correct_code_is_left_alone`
    would still pass if `_scan_text` silently returned [] for everything.
    """
    env_sample = _CLEAN_SAMPLES[0][1]
    moved = env_sample.replace(
        "        env:\n          DOMAIN: ${{ inputs.domain }}\n        run: |\n"
        "          pwsh -File ./x.ps1 -Zone $env:DOMAIN",
        "        run: |\n          pwsh -File ./x.ps1 -Zone ${{ inputs.domain }}",
    )
    assert moved != env_sample, "the mutation did not apply — the sample text drifted"
    findings = _scan_text(moved)
    assert _input_names(findings) == {"domain"}, (
        "moving the value out of env: and into the run body must be a finding; "
        f"got {[str(f) for f in findings]}"
    )


# --------------------------------------------------------------------------
# The freeze — criteria 2 and 5
# --------------------------------------------------------------------------


def test_removing_any_single_entry_is_detected():
    """#1080 criterion 2, for EVERY entry rather than a sampled one.

    A spot check on one entry would pass with a comparison that only looked at
    the first key.
    """
    findings, _, _ = guard.scan_all()
    current = guard.current_map(findings)
    misses = []
    for workflow in sorted(guard.KNOWN_UNGUARDED):
        trimmed = {k: v for k, v in guard.KNOWN_UNGUARDED.items() if k != workflow}
        new, _stale = guard.compare(current, trimmed)
        if not any(workflow in item for item in new):
            misses.append(workflow)
    assert not misses, (
        "dropping these entries from the freeze did not produce a finding naming "
        f"them: {misses}"
    )


def test_removing_a_single_input_from_an_entry_is_detected():
    """The freeze is per-input, not merely per-file.

    Otherwise a workflow already on the list could grow a new interpolated input
    silently — the likeliest way a real regression would arrive, since the
    burn-down edits exactly these files.
    """
    findings, _, _ = guard.scan_all()
    current = guard.current_map(findings)
    workflow, inputs = _a_frozen_subject(current)
    dropped = inputs[0]
    known = dict(guard.KNOWN_UNGUARDED)
    known[workflow] = inputs[1:]
    new, _stale = guard.compare(current, known)
    assert any(dropped in item for item in new), (
        f"an input missing from the freeze must be reported; got {new}"
    )


def test_a_stale_entry_is_detected():
    """#1080 criterion 5: a fixed call site must not leave the list lying."""
    findings, _, _ = guard.scan_all()
    current = guard.current_map(findings)
    known = dict(guard.KNOWN_UNGUARDED)
    known["999-does-not-exist.yml"] = ("ghost",)
    _new, stale = guard.compare(current, known)
    assert any("999-does-not-exist.yml" in item for item in stale), (
        f"an entry matching nothing must be reported stale; got {stale}"
    )


def test_a_narrowed_call_site_reports_the_leftover_input_as_stale():
    """Half-fixing a workflow must still force the entry to be narrowed."""
    findings, _, _ = guard.scan_all()
    current = guard.current_map(findings)
    workflow, inputs = _a_frozen_subject(current)
    known = dict(guard.KNOWN_UNGUARDED)
    known[workflow] = inputs + ("Extra",)
    _new, stale = guard.compare(current, known)
    assert any("Extra" in item for item in stale), (
        f"a frozen input no longer interpolated must be reported; got {stale}"
    )


# --------------------------------------------------------------------------
# Fail closed — criterion 7
# --------------------------------------------------------------------------


def test_unparseable_yaml_is_a_finding_not_a_skip():
    """#1080 criterion 7."""
    with tempfile.TemporaryDirectory() as tmp:
        bad = pathlib.Path(tmp) / "998-broken.yml"
        bad.write_text("name: [unclosed\n  - :\n", encoding="utf-8")
        try:
            guard.scan_workflow(bad)
        except guard.WorkflowUnreadable:
            pass
        else:
            raise AssertionError("a workflow that will not parse must raise")
        _findings, unreadable, _ = guard.scan_all([bad])
        assert len(unreadable) == 1, f"scan_all must surface it, got {unreadable}"


def test_a_non_mapping_workflow_is_a_finding():
    """A file that parses to a string or list is not a workflow — say so."""
    with tempfile.TemporaryDirectory() as tmp:
        bad = pathlib.Path(tmp) / "997-scalar.yml"
        bad.write_text("just a string\n", encoding="utf-8")
        _findings, unreadable, _ = guard.scan_all([bad])
        assert len(unreadable) == 1, f"expected one unreadable, got {unreadable}"


def test_the_write_environment_rule_is_pessimistic():
    """Only an explicit `-read` suffix counts as read-only."""
    assert guard.is_write_environment("github-prod")
    assert guard.is_write_environment("some-new-lane")
    assert not guard.is_write_environment("whmcs-prod-read")


def test_the_remedy_is_named_in_the_output():
    """A finding a reader cannot act on gets suppressed rather than fixed."""
    assert "env:" in guard.REMEDY
    assert "105-manage-record.yml" in guard.REMEDY


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
