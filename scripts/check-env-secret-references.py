#!/usr/bin/env python3
"""Every `secrets.*` reference must resolve where the job actually runs (#912).

A workflow job that references `secrets.X` against an environment that does not
carry `X` is valid YAML, passes `actionlint`, and fails only when the job runs —
by which time the failure is a red scheduled run nobody reads. `228. WHMCS Fraud
Review` had **never** succeeded: its `fetch_fraud_orders` job took the OIDC
identifiers from `vars.*` and its `fraud_review` job, ten lines below in the same
file, took them from `secrets.*` against `fraudlabspro-prod-read`, which holds no
secrets at all. #834 was the same defect twice over (726, and 502's `deliver`).

## What this checks, and what it deliberately does not

The general claim — "environment E carries secret X" — is **not readable from the
tree**, and not readable in CI either: listing environment secret *names* needs
the `secrets` REST permission, which `GITHUB_TOKEN` cannot be granted (there is
no `secrets:` key in a workflow `permissions:` block). Reaching it needs the
org-wide PAT, which lives in `github-prod`, which is gated — an audit that only
runs when a human approves it is #834's defect wearing a different hat.

So this guard checks the decidable half, which is also where every observed
instance of the defect lived: **the Azure OIDC identifiers.** Those exist as
repository *Variables* (`vars.READ_ALL_FFC_AZURE_KV_CLIENT_ID` and friends) which
resolve in every environment, and — for a handful of lanes wired before that
convention — as per-environment *secret copies*, which resolve only in the
environments somebody provisioned. `vars.*` is therefore correct everywhere and
`secrets.*` is correct in exactly the environments listed below, so a new lane
written with `secrets.*` is a defect that can be caught by reading the tree.

Two rules, both exact in both directions:

  R1  `secrets.<an OIDC identifier>` is allowed only for the environments in
      IDENTIFIER_SECRET_ENVIRONMENTS, each of which is there because something
      has actually run green against it. A new environment fails; and a listed
      environment with no references left must be deleted from the list, so it
      cannot rot into a permanent exemption.

  R2  any other `secrets.X` requires the job to declare an `environment:` at
      all. This repo keeps **zero** repo-level Actions secrets — every secret
      lives inside an environment — so an environment-less job gets the empty
      string. That is how 737's first scheduled fire broke.

## The trap this guard was nearly written into

The obvious rule is "identifiers are Variables, so `secrets.<identifier>` is
always wrong" — CLAUDE.md says as much. Measured against the tree, that rule
flags **51 jobs across 5 environments that run green in production.** A guard
derived from the documented convention rather than from the tree would have
arrived as 51 false findings, and the remedy under review pressure is to switch
the guard off. Hence the evidence column below: the list is not a style
preference, it is a record of which environments were observed to work.
"""

from __future__ import annotations

import pathlib
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

# Only matches inside a `${{ ... }}` expression. Without this scoping, pwsh
# property access (`$secrets.Count`, `$secrets.name`) and prose in a comment
# ("…/environments/{env}/secrets") are read as references — a scan written
# without it reported three references this repo does not contain.
_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)

# Both syntaxes GitHub accepts. `secrets['NAME']` is exactly equivalent at run
# time, so matching only the dot form would leave a one-character bypass of
# every rule here. Mirrors the same pair in
# tests/workflow-logic/test_no_raw_credential_secrets.py.
_CONTEXT_REF = re.compile(
    r"""\b(secrets|vars)(?:\.([A-Za-z_][A-Za-z0-9_]*)
                         |\[\s*['"]([^'"]+)['"]\s*\])""",
    re.VERBOSE,
)

# The Azure OIDC identifiers: an Entra application (client) id and a tenant id.
# Matched by SHAPE, not by an enumerated list of the names in the tree today, so
# a new `<PREFIX>_AZURE_KV_CLIENT_ID` is covered the day it is written.
_OIDC_IDENTIFIER = re.compile(r"_AZURE_(?:KV_)?CLIENT_ID$|_AZURE_TENANT_ID$")

# The ambient per-run token: not a stored secret, and it resolves without an
# environment.
_AMBIENT_SECRETS = frozenset({"GITHUB_TOKEN"})

# Environments that carry per-environment COPIES of the OIDC identifier secrets,
# each with the evidence that says so. Exact, in both directions.
#
# Adding a row means asserting that an environment really holds those secrets.
# The only admissible evidence is a run: a workflow using that environment
# completing green. "The setup doc says to create them" is not evidence — that is
# how `candid-prod-read` came to be wired with `secrets.*` while holding nothing
# (its 801/802 lanes were converted to `vars.*` in #912 rather than listed here).
#
# The right direction for anything new is `vars.*`: repository Variables resolve
# in every environment, so a lane using them needs no per-environment secret
# provisioning at all — only the federated credential, which is required either
# way.
IDENTIFIER_SECRET_ENVIRONMENTS: dict[str, str] = {
    "cloudflare-prod-read": (
        "101/104/107/109 and the 700-series zone checks run green on this "
        "environment; the Cloudflare read lane is the oldest OIDC wiring in the "
        "repo (docs/azure-oidc-federated-credentials.md)"
    ),
    "cloudflare-prod-write": (
        "the 1xx write lane (106/110/111/112/120) runs green; #778 consolidated "
        "it and its runs are the repo's most-exercised gated writes"
    ),
    "google-prod-read": (
        "502/504/506 and the 320/321 Key Vault monitors run green on this "
        "environment (docs/google-api.md provisioning record)"
    ),
    "google-prod-write": (
        "503/505 provisioned live GA4/GTM assets for real charities "
        "(docs/google-api.md)"
    ),
    "m365-prod": (
        "#844 phase 4 — this environment still holds raw GitHub secrets "
        "(FFC_AZURE_*, FFC_EXO_CERT_*) because the Exchange Online certificate "
        "cannot be moved to Key Vault by an agent. Its identifiers have no "
        "repository-Variable counterpart, so `vars.*` is not available here."
    ),
}


class Finding:
    """One unresolvable reference, with the remediation attached."""

    def __init__(self, workflow: str, job: str, rule: str, message: str) -> None:
        self.workflow = workflow
        self.job = job
        self.rule = rule
        self.message = message

    def __str__(self) -> str:
        return f"{self.workflow}: job '{self.job}' [{self.rule}] {self.message}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Finding({self!s})"


def context_references(text: str) -> set[tuple[str, str]]:
    """{(context, name)} for every `secrets.X` / `vars.X` in `${{ }}` in `text`."""
    found: set[tuple[str, str]] = set()
    for expression in _EXPRESSION.findall(text):
        for match in _CONTEXT_REF.finditer(expression):
            context = match.group(1)
            name = match.group(2) or match.group(3)
            found.add((context, name))
    return found


def is_oidc_identifier(name: str) -> bool:
    return bool(_OIDC_IDENTIFIER.search(name))


def job_environment(job: dict) -> str | None:
    """The environment a job deploys to, or None if it declares none."""
    env = job.get("environment")
    if isinstance(env, dict):
        name = env.get("name")
        return name if isinstance(name, str) else None
    if isinstance(env, str):
        return env
    return None


def jobs_of(workflow: dict) -> list[tuple[str, dict]]:
    return [
        (job_id, job)
        for job_id, job in (workflow.get("jobs") or {}).items()
        if isinstance(job, dict)
    ]


def audit_workflow(
    filename: str,
    workflow: dict,
    allowed_environments: dict[str, str] | None = None,
) -> list[Finding]:
    """Findings for one parsed workflow. Empty list = clean."""
    allowed = (
        IDENTIFIER_SECRET_ENVIRONMENTS
        if allowed_environments is None
        else allowed_environments
    )
    findings: list[Finding] = []

    for job_id, job in jobs_of(workflow):
        # Dump the job rather than reading raw file text: references are then
        # attributed to the job that actually holds them, which is the whole
        # point (228's two jobs disagreed inside one file). allow_unicode keeps
        # the ✓/❌/em-dashes these workflows print from being escaped into
        # something the regexes would still match but a human would not.
        rendered = yaml.safe_dump(job, default_flow_style=False, allow_unicode=True)
        environment = job_environment(job)

        for context, name in sorted(context_references(rendered)):
            if context != "secrets" or name in _AMBIENT_SECRETS:
                continue

            if is_oidc_identifier(name):
                if environment is None:
                    findings.append(
                        Finding(
                            filename,
                            job_id,
                            "R1",
                            f"references secrets.{name} but declares no "
                            "`environment:` — an environment-scoped secret copy "
                            f"cannot resolve. Use vars.{name} (a repository "
                            "Variable, which resolves everywhere).",
                        )
                    )
                elif environment not in allowed:
                    findings.append(
                        Finding(
                            filename,
                            job_id,
                            "R1",
                            f"references secrets.{name} under environment "
                            f"'{environment}', which is not recorded as carrying "
                            "the OIDC identifier secrets — the value will be the "
                            f"EMPTY STRING at run time. Use vars.{name} instead: "
                            "repository Variables resolve in every environment, "
                            "so no per-environment copy has to be provisioned. "
                            "Only add the environment to "
                            "IDENTIFIER_SECRET_ENVIRONMENTS if a run has actually "
                            "completed green against it.",
                        )
                    )
                continue

            if environment is None:
                findings.append(
                    Finding(
                        filename,
                        job_id,
                        "R2",
                        f"references secrets.{name} but declares no "
                        "`environment:`. This repo keeps no repo-level Actions "
                        "secrets — every secret lives in an environment — so the "
                        "value is the EMPTY STRING at run time. Declare the "
                        "environment that holds it.",
                    )
                )

    return findings


def load_workflows() -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        # utf-8-sig: a BOM would otherwise become part of the first key.
        out.append(
            (path.name, yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {})
        )
    return out


def audit_tree(
    workflows: list[tuple[str, dict]] | None = None,
) -> tuple[list[Finding], dict[str, int]]:
    """(findings, environment -> number of identifier-secret references)."""
    loaded = load_workflows() if workflows is None else workflows
    findings: list[Finding] = []
    usage: dict[str, int] = {}

    for filename, workflow in loaded:
        findings.extend(audit_workflow(filename, workflow))
        for job_id, job in jobs_of(workflow):
            rendered = yaml.safe_dump(job, default_flow_style=False, allow_unicode=True)
            environment = job_environment(job)
            if environment is None:
                continue
            for context, name in context_references(rendered):
                if context == "secrets" and is_oidc_identifier(name):
                    usage[environment] = usage.get(environment, 0) + 1

    return findings, usage


def unused_allowed_environments(usage: dict[str, int]) -> list[str]:
    """Listed environments no job references any more — stale exemptions."""
    return sorted(env for env in IDENTIFIER_SECRET_ENVIRONMENTS if env not in usage)


def main() -> int:
    findings, usage = audit_tree()
    stale = unused_allowed_environments(usage)

    if not findings and not stale:
        listed = len(IDENTIFIER_SECRET_ENVIRONMENTS)
        referenced = sum(usage.values())
        print(
            f"Environment secret references OK: {referenced} OIDC-identifier "
            f"secret reference(s) across {listed} recorded environment(s); every "
            "other secret reference declares an environment."
        )
        return 0

    for finding in findings:
        print(f"::error::{finding}")
    for env in stale:
        print(
            f"::error::'{env}' is listed in IDENTIFIER_SECRET_ENVIRONMENTS but no "
            "job references an OIDC identifier secret under it any more. Delete "
            "the entry in the same PR that removed the last reference — a stale "
            "exemption silently re-permits the defect it was written for."
        )
    print(
        f"FAILED: {len(findings)} unresolvable secret reference(s), "
        f"{len(stale)} stale exemption(s)."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
