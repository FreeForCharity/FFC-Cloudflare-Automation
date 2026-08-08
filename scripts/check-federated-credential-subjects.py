#!/usr/bin/env python3
"""Federated-credential subject audit — exact-match, not just presence.

The #625 outage was an *expected* Azure AD federated credential
(`github-oidc-m365-prod`) whose **subject had a typo** — a trailing hyphen,
`repo:FreeForCharity/FFC-Cloudflare-Automation-:environment:m365-prod`. OIDC
subject matching is exact-string, so every M365 job failed `AADSTS700213` while
the credential still *existed* — a presence/enumeration check (cf. #589) passes it.

This audit compares live Azure state to the declarative expected map in
`config/federated-credentials.json` and asserts, for each expected credential,
that a matching credential exists with an EXACT canonical `subject`, `issuer`,
and `audiences`. It also flags any THIS-repo credential that is not expected.

**The map's denominator is checked too (#1081).** Every mode first asserts set
equality between the environments the map lists and the environments this repo's
workflows actually perform an Azure OIDC exchange under. Without it the audit
could only ever be as complete as somebody's memory: on `main` it printed
`config OK: 3 apps, 9 expected env credentials` while four OIDC lanes sat outside
its denominator — and those four held every failing workflow in the repo. A
control whose input set is hand-maintained cannot detect an omitted input (L133),
so the input set is derived from the tree instead.

Modes:
  # Self-check the config is well-formed (default; runs in CI, no Azure):
  python3 scripts/check-federated-credential-subjects.py

  # Live audit against Azure (operator; needs an authenticated `az` with Graph read):
  python3 scripts/check-federated-credential-subjects.py --live

  # Offline audit against saved `az ad app federated-credential list` dumps
  # (files named creds-<objectId>.json in a directory):
  python3 scripts/check-federated-credential-subjects.py --actual-dir ./dumps

Exit codes: 0 = OK, 1 = a problem (malformed config, or, in audit mode, drift).
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

import yaml

CONFIG = "config/federated-credentials.json"
GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
ENV_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def load_config(path):
    cfg = json.load(open(path, encoding="utf-8"))
    for key in ("repo", "issuer", "audiences", "apps"):
        if key not in cfg:
            raise SystemExit(f"{path}: missing required key '{key}'")
    return cfg


def expected_subject(cfg, env):
    return f"repo:{cfg['repo']}:environment:{env}"


def self_check(cfg, path):
    errors = []
    seen = set()
    for app in cfg["apps"]:
        oid = app.get("objectId", "")
        if not GUID_RE.match(oid):
            errors.append(f"{app.get('displayName', '?')}: objectId '{oid}' is not a GUID")
        envs = app.get("environments", [])
        if not envs:
            errors.append(f"{app.get('displayName', '?')}: no environments listed")
        for env in envs:
            if not ENV_RE.match(env):
                errors.append(f"{app.get('displayName', '?')}: bad environment slug '{env}'")
            key = (oid, env)
            if key in seen:
                errors.append(f"duplicate (app, environment): {oid} / {env}")
            seen.add(key)
    if cfg["issuer"] != "https://token.actions.githubusercontent.com":
        errors.append(f"unexpected issuer: {cfg['issuer']}")
    if cfg["audiences"] != ["api://AzureADTokenExchange"]:
        errors.append(f"unexpected audiences: {cfg['audiences']}")
    if errors:
        print(f"Federated-credential config self-check FAILED ({path}):")
        for e in errors:
            print("  -", e)
        return 1
    n = sum(len(a["environments"]) for a in cfg["apps"])
    print(f"Federated-credential config OK: {len(cfg['apps'])} apps, {n} expected env credentials.")
    return 0


def az_list(object_id):
    az = shutil.which("az") or shutil.which("az.cmd") or "az"
    r = subprocess.run(
        [az, "ad", "app", "federated-credential", "list", "--id", object_id, "-o", "json"],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    if r.returncode != 0:
        raise SystemExit(f"az list failed for {object_id}: {r.stderr.strip()}")
    return json.loads(r.stdout or "[]")


def audit(cfg, get_actual):
    """get_actual(object_id) -> list of credential dicts."""
    prefix = f"repo:{cfg['repo']}:environment:"
    problems = []
    checked = 0
    for app in cfg["apps"]:
        name = app["displayName"]
        actual = get_actual(app["objectId"])
        by_subject = {c.get("subject"): c for c in actual}
        # This-repo credentials actually present on the app.
        present_envs = set()
        for c in actual:
            subj = c.get("subject", "")
            if subj.startswith(prefix):
                present_envs.add(subj[len(prefix):])

        for env in app["environments"]:
            checked += 1
            want = expected_subject(cfg, env)
            cred = by_subject.get(want)
            if not cred:
                problems.append(
                    f"{name}: expected credential for environment '{env}' with subject "
                    f"'{want}' is MISSING (renamed env, deleted cred, or a malformed subject)."
                )
                continue
            if cred.get("issuer") != cfg["issuer"]:
                problems.append(f"{name}/{env}: issuer '{cred.get('issuer')}' != '{cfg['issuer']}'")
            if cred.get("audiences") != cfg["audiences"]:
                problems.append(f"{name}/{env}: audiences {cred.get('audiences')} != {cfg['audiences']}")

        # This-repo creds present but not expected (drift / typo'd subjects show up here).
        for env in sorted(present_envs - set(app["environments"])):
            problems.append(
                f"{name}: UNEXPECTED this-repo credential for '{env}' "
                f"(subject '{prefix}{env}') — not in the expected map; investigate."
            )

    if problems:
        print(f"Federated-credential audit FAILED ({checked} expected creds checked):")
        for p in problems:
            print("  -", p)
        return 1
    print(f"Federated-credential audit OK: {checked} expected creds present with exact subject/issuer/audiences.")
    return 0


# --- coverage: derive the expected set from the tree, don't trust the map -----
#
# #1081. Everything above audits the map against Azure. Nothing audited the map
# against *this repo*, and the map is hand-maintained — so an environment nobody
# remembered to add is invisible to the audit, and the audit reports OK over
# exactly the gap it exists to close (L133). Measured on `main` at the time of
# writing: 12 environments performed an Azure OIDC exchange, 9 were mapped, and
# the 4 unmapped ones (`candid-prod-read`, `fraudlabspro-prod-read`,
# `github-prod`, `github-prod-read`) contained 100% of the repo's failing
# automation — #949, #921, #1033 and the never-runnable 801/802.
#
# The check is static and offline: it reads the workflow YAML already in the
# tree, so it gates every PR without an Azure call or a credential.

WORKFLOW_DIR = os.path.join(".github", "workflows")

# An Entra application (client) id or tenant id, matched by SHAPE so a new
# `<PREFIX>_AZURE_KV_CLIENT_ID` is covered the day it is written. Same pattern
# as scripts/check-env-secret-references.py, deliberately: the two guards agree
# on what an OIDC identifier looks like.
OIDC_IDENTIFIER_RE = re.compile(r"_AZURE_(?:KV_)?CLIENT_ID$|_AZURE_TENANT_ID$")
EXPRESSION_RE = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
CONTEXT_REF_RE = re.compile(
    r"""\b(?:secrets|vars)(?:\.([A-Za-z_][A-Za-z0-9_]*)
                           |\[\s*['"]([^'"]+)['"]\s*\])""",
    re.VERBOSE,
)
AZURE_LOGIN_RE = re.compile(r"^azure/login(@|$)")
LOCAL_ACTION_RE = re.compile(r"^\./(\.github/actions/[^@\s]+)")


def job_environment(job):
    """The environment a job deploys to, or None. `{name: …, url: …}` counts."""
    env = job.get("environment")
    if isinstance(env, dict):
        name = env.get("name")
        return name if isinstance(name, str) else None
    return env if isinstance(env, str) else None


def _identifier_references(rendered):
    """True if the rendered job text references an Azure OIDC identifier."""
    for expression in EXPRESSION_RE.findall(rendered):
        for match in CONTEXT_REF_RE.finditer(expression):
            name = match.group(1) or match.group(2)
            if OIDC_IDENTIFIER_RE.search(name):
                return True
    return False


def _local_action_does_oidc(rel_path, root, cache, problems):
    """Does the composite action at `rel_path` perform an Azure OIDC exchange?

    Resolved by READING the action, not by matching `*-from-kv` on its name: the
    naming convention is a convention, and a lane that departs from it must not
    become invisible to this check. Fails closed — an action that cannot be read
    or parsed is a finding, never a quiet False.
    """
    if rel_path in cache:
        return cache[rel_path]
    verdict = False
    for candidate in ("action.yml", "action.yaml"):
        path = os.path.join(root, rel_path, candidate)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8-sig") as fh:
                action = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            problems.append(f"{rel_path}/{candidate}: unreadable/unparseable ({exc}) — cannot decide whether it performs an OIDC exchange; fix the file.")
            cache[rel_path] = True  # fail closed: assume it does
            return True
        rendered = yaml.safe_dump(action, default_flow_style=False, allow_unicode=True)
        steps = ((action.get("runs") or {}).get("steps")) or []
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("uses"), str):
                if AZURE_LOGIN_RE.match(step["uses"].strip()):
                    verdict = True
        if _identifier_references(rendered):
            verdict = True
        break
    else:
        problems.append(f"{rel_path}: referenced by a workflow but has no action.yml — cannot decide whether it performs an OIDC exchange.")
        cache[rel_path] = True  # fail closed
        return True
    cache[rel_path] = verdict
    return verdict


def job_performs_azure_oidc(job, root=".", cache=None, problems=None):
    """True if this job exchanges a GitHub OIDC token with Azure.

    Three signals, any one of which is sufficient:
      1. an `azure/login` step,
      2. a local composite action that itself performs the exchange
         (`.github/actions/*-from-kv` today — resolved by reading it),
      3. a reference to an Azure OIDC identifier (client/tenant id) anywhere in
         the job, which is how a job hand-rolling the exchange presents.

    A job declaring `environment:` and doing NONE of these is not a finding:
    `github-pages` (721) and `wpmudev-prod` (601) are real environments with no
    Azure lane, and a check that flagged them would be a blanket environment
    audit, which this is not.
    """
    cache = {} if cache is None else cache
    problems = [] if problems is None else problems
    for step in job.get("steps") or []:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses")
        if not isinstance(uses, str):
            continue
        uses = uses.strip()
        if AZURE_LOGIN_RE.match(uses):
            return True
        local = LOCAL_ACTION_RE.match(uses)
        if local and _local_action_does_oidc(local.group(1), root, cache, problems):
            return True
    rendered = yaml.safe_dump(job, default_flow_style=False, allow_unicode=True)
    return _identifier_references(rendered)


def load_workflows(root="."):
    """[(filename, parsed)] for every workflow, plus a list of parse failures.

    Fail-closed: a workflow that will not parse is returned as an error, never
    skipped. A guard that silently drops the file it cannot read reports clean
    over the one place nobody has looked.
    """
    loaded, errors = [], []
    directory = os.path.join(root, WORKFLOW_DIR)
    names = []
    for pattern in ("*.yml", "*.yaml"):
        names.extend(glob.glob(os.path.join(directory, pattern)))
    for path in sorted(names):
        try:
            with open(path, encoding="utf-8-sig") as fh:
                loaded.append((os.path.basename(path), yaml.safe_load(fh) or {}))
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{os.path.basename(path)}: cannot be parsed ({exc}) — its environments cannot be audited.")
    return loaded, errors


def oidc_environment_usage(workflows=None, root="."):
    """(env -> sorted workflow filenames, problems) over OIDC-performing jobs."""
    problems = []
    if workflows is None:
        workflows, errors = load_workflows(root)
        problems.extend(errors)
    usage = {}
    cache = {}
    for filename, workflow in workflows:
        if not isinstance(workflow, dict):
            problems.append(f"{filename}: top level is not a mapping — cannot be audited.")
            continue
        for job_id, job in (workflow.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            env = job_environment(job)
            if not env:
                continue
            if job_performs_azure_oidc(job, root=root, cache=cache, problems=problems):
                usage.setdefault(env, set()).add(filename)
    return {env: sorted(files) for env, files in usage.items()}, problems


def coverage_check(cfg, usage, problems=None):
    """Set equality between the mapped environments and the OIDC-using ones."""
    findings = list(problems or [])
    mapped = {}
    for app in cfg["apps"]:
        for env in app.get("environments", []):
            mapped.setdefault(env, []).append(app.get("displayName", "?"))

    for env in sorted(set(usage) - set(mapped)):
        owners = ", ".join(usage[env])
        findings.append(f"environment '{env}' performs an Azure OIDC exchange in {owners} but is NOT in the expected map — the subject audit cannot check a credential it does not expect. Add it to the app that owns the lane (READ_ALL_* identifiers => kv-reader, WR_ALL_* => kv-writer); do not allowlist it.")
    for env in sorted(set(mapped) - set(usage)):
        findings.append(f"environment '{env}' is mapped to {'/'.join(mapped[env])} but no workflow job performs an Azure OIDC exchange under it — a stale entry. Delete it in the same PR that removed the last use.")

    if findings:
        print("Federated-credential coverage FAILED:")
        for finding in findings:
            print("  -", finding)
        return 1
    print(f"Federated-credential coverage OK: {len(usage)} environment(s) perform an Azure OIDC exchange and all {len(mapped)} are mapped.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--live", action="store_true", help="Audit against live Azure via `az` (operator).")
    ap.add_argument("--actual-dir", help="Audit against saved creds-<objectId>.json dumps in this dir.")
    ap.add_argument("--root", default=".", help="Repo root to read .github/workflows from (default: cwd).")
    args = ap.parse_args()

    cfg = load_config(args.config)
    rc = self_check(cfg, args.config)
    if rc:
        return rc
    # Coverage runs in every mode, including the default offline one: it is the
    # check that makes the map's denominator honest, and the audit modes below
    # iterate that same map. Ordered after self_check for the same reason
    # self_check comes first — there is no point auditing a map that is wrong
    # about which environments exist.
    usage, problems = oidc_environment_usage(root=args.root)
    rc = coverage_check(cfg, usage, problems)
    if rc:
        return rc
    if args.live:
        return audit(cfg, az_list)
    if args.actual_dir:
        def from_dir(oid):
            p = os.path.join(args.actual_dir, f"creds-{oid}.json")
            return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []
        return audit(cfg, from_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
