#!/usr/bin/env python3
"""Guard: our gated/ungated claims match GitHub's real protection rules (#993).

WHY THIS EXISTS

`tests/workflow-logic/` already checks that a workflow's declared `environment:`
agrees with `docs/workflow-safety-and-approvals.md` and
`docs/workflow-catalog.json` (`test_the_environment_is_ungated`,
`test_the_safety_row_names_the_environment_the_job_actually_declares`, and
friends). All of those compare **our files to our other files**. None of them
asks GitHub whether an environment actually has zero protection rules, so the
three artifacts can agree perfectly and be wrong together while the tree stays
green.

That is not hypothetical. The reason several workflows sit on `github-prod-read`
rather than `github-prod` is that the former is ungated; #834 is the recorded
instance of getting it wrong (8 of 21 scheduled runs failed or were cancelled
waiting for an approval nobody was watching at 07:47 UTC). The input to that
outcome — an environment's protection rules — lives in GitHub's Settings UI, is
editable by any admin, is **not** in this repository, and was covered by no
check. Adding `required_reviewers` to `github-prod-read` tomorrow would convert
every workflow on that lane into a gate-parker and leave 100% of the suite
green.

This adds the missing third side: **platform truth**.

WHAT IT ASSERTS, per environment

  * one the docs call **ungated** has no `required_reviewers`;
  * one the docs call **gated** has `required_reviewers`;
  * every environment a workflow references **exists** on the platform — a
    typo'd `environment:` is created on first run with no protection at all,
    silently ungating a lane that was meant to be gated;
  * every environment that exists but is referenced by no workflow is
    **reported** (dead lanes accumulate credentials). Reported, not fatal:
    `cloudflare-prod`, `copilot` and `github-pages` are legitimately in that
    state today, and a check that cannot pass on a correct tree gets disabled.

NOTHING HERE IS HAND-LISTED. Both sides are derived — the lanes from
`.github/workflows/*.yml`, the gated set from the single declaration this repo
already keeps (`GATED_ENVS` in `scripts/check-workflow-doc-consistency.py`,
which `tests/workflow-logic/test_gated_env_hygiene.py` pins to the safety doc),
and the truth from the API. The point of the check is to catch the pair nobody
thought to list, so a literal list in here would defeat it (ledger L16 / L62).
Note also that the naming convention is not a usable proxy: `zeffy-prod` is
**ungated** despite the `-prod` suffix. Any check must read the API.

FAIL CLOSED. A missing token, an HTTP error, a short page, or a response whose
shape we do not recognise all exit non-zero. A check that silently no-ops is the
exact failure shape it exists to catch (#722 / ledger L52), and "we could not
read the environments" must never render as "no gates" (the same rule workflow
730 states in its own error path).

AUTH. `GET /repos/:owner/:repo/environments` is satisfied by the ambient
`GITHUB_TOKEN` reading its own repo — workflow 730 has been doing exactly this
with `${{ github.token }}` and `permissions: {contents: read, actions: read}`.
No Key Vault credential is needed or wanted here.

USAGE

    GH_TOKEN=... python3 scripts/check-environment-protection.py
    python3 scripts/check-environment-protection.py --environments-json fixture.json

The `--environments-json` form substitutes a synthetic API payload for the
network call, which is how the mutation tests in
`tests/workflow-logic/test_environment_protection_matches_docs.py` prove this
guard fails in each direction.
"""

from __future__ import annotations

import argparse
import configparser
import importlib.util
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
DOC_CHECKER = REPO_ROOT / "scripts" / "check-workflow-doc-consistency.py"

API_ROOT = "https://api.github.com"
HTTP_TIMEOUT = 30
PER_PAGE = 100
USER_AGENT = "ffc-check-environment-protection"

# The protection rule that makes a deployment wait for a human. `wait_timer` and
# `branch_policy` are protections too, but they do not park a run at
# `status: waiting` for a reviewer, which is the thing #834 is about.
REVIEWER_RULE = "required_reviewers"


# --------------------------------------------------------------------------
# The declared side: derived from the tree, never hand-listed.
# --------------------------------------------------------------------------


def _load_doc_checker():
    """Import the doc-consistency script by path (its filename has hyphens)."""
    spec = importlib.util.spec_from_file_location("check_workflow_doc_consistency", DOC_CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def declared_gated_environments():
    """The environments the docs claim require a reviewer.

    Read from `GATED_ENVS` in scripts/check-workflow-doc-consistency.py, which
    is this repo's one machine-readable statement of the safety doc's
    "Environment approval gates" layer. Reading it rather than re-typing it is
    what keeps this guard from becoming a fourth artifact that agrees with
    itself.
    """
    gated = set(_load_doc_checker().GATED_ENVS)
    if not gated:
        raise SystemExit(
            f"error: GATED_ENVS in {DOC_CHECKER.relative_to(REPO_ROOT)} is empty. "
            "With no declared gated environments this check would assert nothing "
            "about the gated direction, so it fails rather than passing vacuously."
        )
    return gated


def workflow_lanes(workflows_dir=WORKFLOWS_DIR):
    """Map every environment named in the tree to the workflow files using it.

    Reuses `declared_environments` from the doc-consistency script so the two
    guards cannot disagree about what an `environment:` declaration looks like
    (it handles both the inline and the `environment:`/`name:` block forms).
    """
    parse = _load_doc_checker().declared_environments
    lanes: dict[str, set[str]] = {}
    files = sorted(pathlib.Path(workflows_dir).glob("*.yml"))
    if not files:
        raise SystemExit(
            f"error: no workflow files under {workflows_dir}. Refusing to report "
            "'every lane is fine' about a tree this check could not read."
        )
    for path in files:
        for env in parse(path.read_text(encoding="utf-8-sig")):
            lanes.setdefault(env, set()).add(path.name)
    return {env: sorted(names) for env, names in lanes.items()}


# --------------------------------------------------------------------------
# The platform side.
# --------------------------------------------------------------------------


def resolve_repo(explicit=None):
    """owner/name for the repository under test."""
    if explicit:
        return explicit
    env = os.environ.get("GITHUB_REPOSITORY")
    if env:
        return env
    cfg_path = REPO_ROOT / ".git" / "config"
    if cfg_path.is_file():
        cfg = configparser.ConfigParser()
        cfg.read(cfg_path, encoding="utf-8")
        for section in cfg.sections():
            if section.startswith('remote "origin"'):
                url = cfg[section].get("url", "")
                m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url)
                if m:
                    return m.group(1)
    raise SystemExit(
        "error: could not determine the repository. Set GITHUB_REPOSITORY or pass "
        "--repo owner/name."
    )


def resolve_token():
    """The token to read the environments with — absent is an error, not a skip."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        token = os.environ.get(var, "").strip()
        if token:
            return token
    raise SystemExit(
        "error: no GitHub token in GH_TOKEN or GITHUB_TOKEN, so the environment "
        "protection rules could not be read.\n"
        "This check FAILS rather than skipping: an unread environment list must "
        "never be reported as 'no gates' (#993, and the same rule workflow 730 "
        "states in its own error path). In Actions, pass the ambient token:\n"
        "    env:\n"
        "      GH_TOKEN: ${{ github.token }}\n"
        "and give the job `permissions: {contents: read, actions: read}`."
    )


def fetch_environments_payload(repo, token):
    """GET /repos/:repo/environments, paginated, as one merged payload.

    The endpoint wraps its rows in an object, so `Link`-following alone is not
    enough: we page explicitly and then compare what we collected against the
    `total_count` the API reported. A short read here would understate the gates
    (CLAUDE.md: compare total_count against what you actually got before
    concluding anything from an absence).
    """
    rows = []
    total = None
    page = 1
    while True:
        params = urllib.parse.urlencode({"per_page": PER_PAGE, "page": page})
        url = f"{API_ROOT}/repos/{repo}/environments?{params}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", USER_AGENT)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").strip()
            raise SystemExit(
                f"error: GitHub API {exc.code} reading {url}: {detail}\n"
                "The environment list could not be read, so this check reports "
                "nothing rather than a clean bill of health."
            )
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"error: could not reach the GitHub API ({url}): {exc.reason}\n"
                "The environment list could not be read, so this check reports "
                "nothing rather than a clean bill of health."
            )
        if not isinstance(payload, dict) or "environments" not in payload:
            raise SystemExit(
                f"error: unexpected response shape from {url} — no 'environments' key. "
                "Refusing to interpret an unrecognised payload as 'no gates'."
            )
        batch = payload.get("environments") or []
        rows.extend(batch)
        if total is None:
            total = payload.get("total_count")
        if len(batch) < PER_PAGE:
            break
        page += 1
    if isinstance(total, int) and len(rows) != total:
        raise SystemExit(
            f"error: read {len(rows)} environments but the API reported "
            f"total_count={total}. A short read understates the gates, so this "
            "check fails rather than concluding anything from the difference."
        )
    return {"total_count": total if total is not None else len(rows), "environments": rows}


def protection_map(payload):
    """{environment name -> sorted list of protection rule types}."""
    if not isinstance(payload, dict) or "environments" not in payload:
        raise SystemExit(
            "error: environments payload has no 'environments' key. Refusing to "
            "interpret an unrecognised payload as 'no gates'."
        )
    rules = {}
    for env in payload["environments"]:
        name = env.get("name")
        if not name:
            raise SystemExit(f"error: environments payload contains a nameless entry: {env!r}")
        rules[name] = sorted(
            r.get("type", "") for r in (env.get("protection_rules") or []) if r.get("type")
        )
    return rules


# --------------------------------------------------------------------------
# The comparison. Pure — this is what the mutation tests drive.
# --------------------------------------------------------------------------


def evaluate(lanes, gated, actual):
    """Compare declared lanes against platform truth.

    Args:
        lanes:  {environment -> [workflow files declaring it]} (from the tree)
        gated:  {environment} the docs claim requires a reviewer
        actual: {environment -> [protection rule types]} (from the API)

    Returns (errors, warnings) as lists of human-readable strings.
    """
    errors = []
    warnings = []

    for env in sorted(lanes):
        users = ", ".join(lanes[env])
        if env not in actual:
            errors.append(
                f"{env}: referenced by {users} but no such environment exists on the "
                "platform. A typo'd `environment:` is created on first run with NO "
                "protection rules, which silently ungates the lane."
            )
            continue
        has_reviewer = REVIEWER_RULE in actual[env]
        observed = ", ".join(actual[env]) or "none"
        if env in gated and not has_reviewer:
            errors.append(
                f"{env}: the docs call this GATED but the platform has no "
                f"{REVIEWER_RULE} (rules: {observed}). Runs on this lane no longer "
                f"pause for a reviewer. Workflows affected: {users}"
            )
        elif env not in gated and has_reviewer:
            errors.append(
                f"{env}: the docs call this UNGATED but the platform requires "
                f"reviewers (rules: {observed}). Every run on this lane now parks at "
                f"an approval gate — this is #834. Workflows affected: {users}"
            )

    # A gated environment the docs name that does not exist at all. Not covered
    # above, which only walks environments some workflow references.
    for env in sorted(gated - set(lanes)):
        if env not in actual:
            warnings.append(
                f"{env}: the docs list this as a gated environment but it exists "
                "neither on the platform nor in any workflow — a stale claim."
            )

    for env in sorted(set(actual) - set(lanes)):
        observed = ", ".join(actual[env]) or "none"
        warnings.append(
            f"{env}: exists on the platform (rules: {observed}) but no workflow "
            "references it. Dead lanes accumulate credentials."
        )

    return errors, warnings


def render_table(lanes, gated, actual):
    lines = ["", "Environment protection — platform truth vs declared:", ""]
    width = max([len(n) for n in set(actual) | set(lanes)] + [11])
    for env in sorted(set(actual) | set(lanes)):
        if env in lanes:
            declared = "gated" if env in gated else "ungated"
        else:
            declared = "unreferenced"
        observed = ", ".join(actual.get(env, [])) or ("none" if env in actual else "ABSENT")
        lines.append(f"  {env:<{width}}  declared={declared:<12}  platform={observed}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", help="owner/name (default: $GITHUB_REPOSITORY, then git origin)")
    parser.add_argument(
        "--environments-json",
        help=(
            "read the environments payload from this file instead of the API. "
            "Used by the mutation tests; not for CI."
        ),
    )
    args = parser.parse_args(argv)

    lanes = workflow_lanes()
    gated = declared_gated_environments()

    if args.environments_json:
        path = pathlib.Path(args.environments_json)
        if not path.is_file():
            raise SystemExit(f"error: no such environments payload: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
    else:
        payload = fetch_environments_payload(resolve_repo(args.repo), resolve_token())

    actual = protection_map(payload)
    errors, warnings = evaluate(lanes, gated, actual)

    print(render_table(lanes, gated, actual))

    if warnings:
        print("\nReported (not fatal):")
        for w in warnings:
            print(f"  - {w}")

    if errors:
        print("\nEnvironment protection check FAILED:")
        for e in errors:
            print(f"  - {e}")
        print(
            "\nEither the platform changed (Settings -> Environments) or the docs "
            "did. Re-run workflow 730 to see the live rules, then reconcile "
            "GATED_ENVS in scripts/check-workflow-doc-consistency.py and the "
            '"Environment approval gates" layer in '
            "docs/workflow-safety-and-approvals.md. See #993 and #834."
        )
        return 1

    print(
        f"\nEnvironment protection OK: {len(lanes)} referenced environment(s), "
        f"{len(gated & set(lanes))} gated, {len(set(lanes) - gated)} ungated, "
        f"all agreeing with the platform."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
