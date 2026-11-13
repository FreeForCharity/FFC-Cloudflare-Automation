#!/usr/bin/env python3
"""Dispatch one charity-lane workflow run and clear its gates under policy.

This is the ONLY path by which the Onboarding Conductor clears an environment
approval gate (docs/charity-lane-pipeline.md, epic #1618). It never approves a run
it did not dispatch itself, and it never approves an environment the matched rule
does not list. Policy lives in config/charity-lane-gate-policy.json; read that file
for what is allowed.

    check  --rule ID --input k=v ...   validate only; dispatches nothing
    run    --rule ID --input k=v ...   validate, dispatch, approve allowed gates, watch
    run    --rule ID --inputs-env VAR  same, inputs as a JSON object in env var VAR
                                       (how workflow 753 passes a dispatcher's inputs)
    advance [--dry-run]                Gate 1/2: run 232, then accept each ACCEPT row
                                       (fraud screened and passed) through 226 / 211,
                                       at most policy.advance.max_accepts per run
    sequence NAME [--dry-run]          run a named policy sequence in order, stopping
                                       at the first step that does not succeed
                                       (e.g. weekly-sites-list: 601, then 703)
    live-site DOMAIN                   print the live-site verdict for a domain

`advance` and `sequence` exist so the whole loop can run inside GitHub Actions
(workflow 753, docs/charity-intake-autonomy.md), where no agent session and no
operator's PC is needed. Every step still goes through the same rule check,
dispatch, identify and approve path as `run`.

Order of operations for `run`:
  1. The inputs are checked against the rule: closed set, enum / pattern / maxlen,
     required keys present. Any failure refuses before anything is dispatched.
  2. The run is dispatched through the REST endpoint (`gh workflow run` uses GraphQL,
     which cloud sessions cannot reach).
  3. The new run is identified as the single workflow_dispatch run of that workflow,
     by this user, created after the dispatch. Zero or several candidates refuse.
  4. Each pending deployment is approved only when its environment is in the rule,
     and, for environments under `live_site_check`, only when the domain is not
     serving a live non-GitHub site. Anything else is left waiting for Clarke and
     reported as ESCALATE.
  5. One audit comment goes to the ledger issue: run, rule, inputs (PII-free keys
     only), every approval and every refusal.

Exit codes: 0 done (including runs left waiting for a human); 2 refused by policy,
nothing dispatched; 3 dispatched but the run could not be identified, nothing
approved, audit posted: never re-dispatch on 3; 1 error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "config" / "charity-lane-gate-policy.json"

GITHUB_PAGES_IPV4 = {"185.199.108.153", "185.199.109.153", "185.199.110.153", "185.199.111.153"}
# Inputs that may carry contact details; their values never go into the audit comment.
PII_INPUTS = {"footer_email", "footer_phone", "footer_address", "message", "leadership_lines", "social_links"}
EXIT_DISPATCHED_UNIDENTIFIED = 3


class PolicyError(Exception):
    """A request the policy does not allow."""


# ---------------------------------------------------------------- pure logic


def load_policy(path: pathlib.Path = POLICY_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def find_rule(policy: dict, rule_id: str) -> dict:
    for rule in policy["rules"]:
        if rule["id"] == rule_id:
            return rule
    raise PolicyError(f"unknown rule {rule_id!r}")


def validate_inputs(rule: dict, inputs: dict[str, str]) -> None:
    """Raise PolicyError unless `inputs` satisfy `rule` exactly."""
    allowed = rule["inputs"]
    for key in rule.get("required", []):
        if not str(inputs.get(key, "")).strip():
            raise PolicyError(f"rule {rule['id']}: required input {key!r} missing")
    for key, value in inputs.items():
        if key not in allowed:
            raise PolicyError(f"rule {rule['id']}: input {key!r} is not allowed")
        spec = allowed[key]
        value = str(value)
        if "enum" in spec and value not in spec["enum"]:
            raise PolicyError(f"rule {rule['id']}: {key}={value!r} not in {spec['enum']}")
        if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
            raise PolicyError(f"rule {rule['id']}: {key} does not match {spec['pattern']}")
        if "maxlen" in spec and len(value) > spec["maxlen"]:
            raise PolicyError(f"rule {rule['id']}: {key} longer than {spec['maxlen']}")


def parse_inputs(pairs: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise PolicyError(f"input {pair!r} is not key=value")
        key, value = pair.split("=", 1)
        if key in out:
            raise PolicyError(f"input {key!r} given twice")
        out[key] = value
    return out


def pick_run(runs: list[dict], login: str, dispatched_at: dt.datetime) -> dict:
    """Return the one run this dispatch created, or raise PolicyError."""
    floor = dispatched_at - dt.timedelta(seconds=5)
    candidates = [
        r
        for r in runs
        if r.get("event") == "workflow_dispatch"
        and (r.get("actor") or {}).get("login") == login
        and parse_ts(r["created_at"]) >= floor
    ]
    if len(candidates) != 1:
        raise PolicyError(
            f"expected exactly one new run, found {len(candidates)}; refusing to approve an ambiguous run"
        )
    return candidates[0]


def parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def classify_live_site(apex_a: list[str], www_cname: list[str], www_a: list[str], http: dict | None) -> tuple[bool, str]:
    """Return (is_live, reason). Conservative: anything not clearly GitHub or empty is live.

    `http` is None when nothing answered, else {"status": int, "server": str}.
    """
    non_github_a = sorted({ip for ip in apex_a + www_a if ip not in GITHUB_PAGES_IPV4})
    if non_github_a:
        return True, f"A records point outside GitHub Pages: {', '.join(non_github_a)}"
    if any(not c.rstrip(".").lower().endswith(".github.io") for c in www_cname):
        return True, f"www CNAME points outside GitHub Pages: {', '.join(www_cname)}"
    if http is not None:
        server = (http.get("server") or "").lower()
        if server != "github.com":
            return True, f"apex answered HTTP {http.get('status')} from server {server or 'unknown'!r}"
    return False, "no live non-GitHub site (no foreign records, nothing else answering)"


def decide(rule: dict, pending: list[dict], live: tuple[bool, str] | None) -> tuple[list[dict], list[str]]:
    """Split pending deployments into (approve, refusals)."""
    approve, refusals = [], []
    for dep in pending:
        env = dep["environment"]["name"]
        if env not in rule["environments"]:
            refusals.append(f"{env}: not an environment of rule {rule['id']}")
        elif not dep.get("current_user_can_approve", False):
            refusals.append(f"{env}: this identity is not a reviewer")
        elif env in rule.get("live_site_check", []) and (live is None or live[0]):
            refusals.append(f"{env}: live-site check failed ({live[1] if live else 'not run'})")
        else:
            approve.append(dep)
    return approve, refusals


def audit_inputs(inputs: dict[str, str]) -> dict[str, str]:
    return {k: ("<redacted>" if k in PII_INPUTS else v) for k, v in inputs.items()}


# ---------------------------------------------------------------- side effects


def gh(args: list[str], body: dict | None = None) -> object:
    cmd = ["gh", "api", *args]
    if body is not None:
        cmd += ["--input", "-"]
    r = subprocess.run(
        cmd,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh api {' '.join(args)} failed: {r.stderr.strip()[:500]}")
    out = r.stdout.strip()
    return json.loads(out) if out else None


def doh(name: str, rtype: str) -> list[str]:
    url = f"https://cloudflare-dns.com/dns-query?name={name}&type={rtype}"
    req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    want = {"A": 1, "CNAME": 5}[rtype]
    return [a["data"] for a in data.get("Answer", []) if a.get("type") == want]


def http_probe(domain: str) -> dict | None:
    req = urllib.request.Request(f"https://{domain}/", method="GET", headers={"user-agent": "ffc-charity-lane-gate"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"status": resp.status, "server": resp.headers.get("server", "")}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "server": e.headers.get("server", "")}
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def live_site(domain: str) -> tuple[bool, str]:
    try:
        apex_a = doh(domain, "A")
        www_cname = doh(f"www.{domain}", "CNAME")
        www_a = [] if www_cname else doh(f"www.{domain}", "A")
    except Exception as e:  # DNS lookup itself failed: treat as unknown, i.e. live
        return True, f"DNS lookup failed ({e}); treating as live"
    return classify_live_site(apex_a, www_cname, www_a, http_probe(domain))


def post_audit(policy: dict, lines: list[str]) -> None:
    body = "<!-- ffc-routine:charity-lane-gate -->\n" + "\n".join(lines)
    gh([f"repos/{policy['repo']}/issues/{policy['ledger_issue']}/comments", "-X", "POST"], {"body": body})


def cmd_run(policy: dict, rule: dict, inputs: dict[str, str], timeout_s: int, audit: bool) -> int:
    code, result = execute(policy, rule, inputs, timeout_s, audit)
    if result is not None:
        print(json.dumps(result, indent=2))
    return code


def execute(policy: dict, rule: dict, inputs: dict[str, str], timeout_s: int, audit: bool) -> tuple[int, dict | None]:
    """Dispatch one run under `rule`, clear its allowed gates, watch it. Returns (exit code, result)."""
    repo, wf = policy["repo"], rule["workflow"]
    # 103/701 name the input `domain`; 120 names it `domains` (one domain, by rule).
    live = live_site(inputs.get("domain") or inputs["domains"]) if rule.get("live_site_check") else None
    login = gh(["user"])["login"]
    started = dt.datetime.now(dt.timezone.utc)
    gh([f"repos/{repo}/actions/workflows/{wf}/dispatches", "-X", "POST"], {"ref": policy["ref"], "inputs": inputs})

    run = None
    for _ in range(20):
        time.sleep(6)
        runs = gh([f"repos/{repo}/actions/workflows/{wf}/runs?event=workflow_dispatch&per_page=20"])["workflow_runs"]
        try:
            run = pick_run(runs, login, started)
            break
        except PolicyError:
            continue
    if run is None:
        # The dispatch already happened, so this is not a policy refusal: say so on
        # the ledger (a run may now sit at a gate unsupervised) and use a distinct
        # exit code so the caller never treats it as "nothing happened, retry".
        msg = "dispatched, but could not identify exactly one new run; approved nothing"
        print(f"DISPATCHED-UNIDENTIFIED: {msg}", file=sys.stderr)
        if audit:
            post_audit(
                policy,
                [
                    f"**Charity-lane gate - rule `{rule['id']}`** - DISPATCHED, RUN NOT IDENTIFIED",
                    f"- workflow `{wf}`, dispatched {started.isoformat(timespec='seconds')}",
                    f"- inputs: `{json.dumps(audit_inputs(inputs))}`",
                    "- approved: none. Any run waiting at a gate from this dispatch is left for Clarke.",
                    "- DECISION NEEDED: check the workflow's recent runs; do not re-dispatch.",
                ],
            )
        return EXIT_DISPATCHED_UNIDENTIFIED, None

    rid, url = run["id"], run["html_url"]
    approved, refused = [], []
    deadline = time.time() + timeout_s
    status = run["status"]
    while time.time() < deadline:
        cur = gh([f"repos/{repo}/actions/runs/{rid}"])
        status = cur["status"]
        if status == "completed":
            break
        if status == "waiting":
            pending = gh([f"repos/{repo}/actions/runs/{rid}/pending_deployments"]) or []
            todo, why = decide(rule, pending, live)
            for msg in why:
                if msg not in refused:
                    refused.append(msg)
            if todo:
                gh(
                    [f"repos/{repo}/actions/runs/{rid}/pending_deployments", "-X", "POST"],
                    {
                        "environment_ids": [d["environment"]["id"] for d in todo],
                        "state": "approved",
                        "comment": f"charity-lane gate: rule {rule['id']} (scripts/charity_lane_gate.py)",
                    },
                )
                approved += [d["environment"]["name"] for d in todo]
            elif why:
                break  # only refused gates remain; leave them for Clarke
        time.sleep(15)

    conclusion = gh([f"repos/{repo}/actions/runs/{rid}"]).get("conclusion")
    result = {
        "run": url,
        "run_id": rid,
        "rule": rule["id"],
        "approved": approved,
        "refused": refused,
        "status": status,
        "conclusion": conclusion,
        "live_site": live[1] if live else None,
    }
    if audit:
        lines = [
            f"**Charity-lane gate - rule `{rule['id']}`** - {url}",
            f"- inputs: `{json.dumps(audit_inputs(inputs))}`",
            f"- approved: {', '.join(approved) or 'none'}",
        ]
        if live:
            lines.append(f"- live-site check: {live[1]}")
        lines += [f"- ESCALATE (left for Clarke): {r}" for r in refused]
        lines.append(f"- status: {status} / {conclusion}")
        post_audit(policy, lines)
    return 0, result


# ---------------------------------------------------------------- advance / sequence


def select_accepts(policy: dict, report: dict, max_accepts: int) -> list[tuple[str, dict[str, str]]]:
    """Pure: the (rule id, inputs) pairs `advance` will run for a 232 verdicts report.

    Only a fraud-SCREENED report counts (the unscreened intermediate carries
    FRAUD_CHECK_NOT_RUN on every row and is not a decision input), and only rows
    whose verdict is ACCEPT *and* whose own fraud_check is `pass`. Oldest order
    first, capped. Each pair is validated against its rule before it is returned,
    so a malformed row is refused here rather than at dispatch.
    """
    if not str(report.get("schema", "")).startswith("charity-lane-verdicts/"):
        raise PolicyError(f"unrecognised verdicts schema {report.get('schema')!r}")
    if report.get("fraud_check") != "screened":
        raise PolicyError("verdicts report is not fraud-screened; refusing to accept anything from it")
    rows = [r for r in report.get("verdicts") or [] if r.get("verdict") == "ACCEPT" and r.get("fraud_check") == "pass"]
    rows.sort(key=lambda r: int(r.get("order_id") or 0))
    out: list[tuple[str, dict[str, str]]] = []
    for r in rows:
        oid = str(r.get("order_id", ""))
        gate = int(r.get("gate") or 0)
        if gate == 1:
            pair = ("226-approve-one", {"mode": "approve", "orderids": oid})
        elif gate == 2:
            pair = ("211-accept-one", {"order_id": oid, "action": "accept", "dry_run": "false"})
        else:
            continue
        validate_inputs(find_rule(policy, pair[0]), pair[1])
        out.append(pair)
        if len(out) >= max_accepts:
            break
    return out


def gh_bytes(path: str) -> bytes:
    r = subprocess.run(["gh", "api", path], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api {path} failed: {r.stderr.decode('utf-8', 'replace').strip()[:500]}")
    return r.stdout


def download_json_artifact(repo: str, run_id: int, name: str) -> dict:
    import io
    import zipfile

    arts = gh([f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"])["artifacts"]
    hits = [a for a in arts if a.get("name") == name and not a.get("expired")]
    if len(hits) != 1:
        raise RuntimeError(f"run {run_id}: expected one artifact {name!r}, found {len(hits)}")
    blob = gh_bytes(f"repos/{repo}/actions/artifacts/{hits[0]['id']}/zip")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        members = [m for m in z.namelist() if m.endswith(".json")]
        if len(members) != 1:
            raise RuntimeError(f"artifact {name!r}: expected one .json member, found {members}")
        return json.loads(z.read(members[0]).decode("utf-8-sig"))


def cmd_advance(policy: dict, dry_run: bool, timeout_s: int) -> int:
    cfg = policy.get("advance") or {}
    cap = int(cfg.get("max_accepts", 0))
    if cap < 1:
        raise PolicyError("policy.advance.max_accepts is not set; advance is disabled")
    vrule = find_rule(policy, cfg.get("verdicts_rule", "232-verdicts"))
    code, res = execute(policy, vrule, {}, timeout_s, audit=False)
    if code != 0 or res is None or res.get("conclusion") != "success":
        print(f"ERROR: verdicts run did not succeed: {json.dumps(res)}", file=sys.stderr)
        return 1
    report = download_json_artifact(policy["repo"], res["run_id"], cfg.get("verdicts_artifact", "charity-lane-verdicts"))
    plan = select_accepts(policy, report, cap)
    counts = report.get("counts") or {}
    summary = {"verdicts_run": res["run"], "counts": counts, "plan": [{"rule": r, "inputs": i} for r, i in plan], "dry_run": dry_run, "results": []}
    worst = 0
    if not dry_run:
        for rule_id, inputs in plan:
            c, out = execute(policy, find_rule(policy, rule_id), inputs, timeout_s, audit=True)
            summary["results"].append({"rule": rule_id, "inputs": inputs, "exit": c, "conclusion": (out or {}).get("conclusion")})
            worst = max(worst, c)
    print(json.dumps(summary, indent=2))
    post_audit(
        policy,
        [
            f"**Charity-lane advance{' (dry run)' if dry_run else ''}** - verdicts {res['run']}",
            f"- counts: `{json.dumps(counts)}`",
            f"- planned accepts (cap {cap}): " + (", ".join(f"{r} {json.dumps(i)}" for r, i in plan) or "none"),
        ]
        + [f"- {x['rule']} {json.dumps(x['inputs'])}: exit {x['exit']}, {x['conclusion']}" for x in summary["results"]],
    )
    return worst


def cmd_sequence(policy: dict, name: str, dry_run: bool, timeout_s: int) -> int:
    steps = (policy.get("sequences") or {}).get(name)
    if not steps:
        raise PolicyError(f"unknown sequence {name!r}")
    # Validate every step before dispatching any, so a bad later step cannot leave
    # an earlier one half-done.
    resolved = []
    for st in steps:
        rule = find_rule(policy, st["rule"])
        validate_inputs(rule, dict(st.get("inputs") or {}))
        resolved.append((rule, dict(st.get("inputs") or {})))
    if dry_run:
        print(json.dumps({"sequence": name, "steps": [r["id"] for r, _ in resolved], "dry_run": True}))
        return 0
    for rule, inputs in resolved:
        code, res = execute(policy, rule, inputs, timeout_s, audit=True)
        if res is not None:
            print(json.dumps(res, indent=2))
        if code != 0 or res is None or res.get("conclusion") != "success":
            print(f"STOPPED: sequence {name} step {rule['id']} did not succeed", file=sys.stderr)
            return code or 1
    return 0


def inputs_from_env(var: str) -> dict[str, str]:
    import os

    raw = os.environ.get(var, "").strip() or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise PolicyError(f"{var} is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise PolicyError(f"{var} must be a JSON object")
    out: dict[str, str] = {}
    for k, v in data.items():
        if not isinstance(v, (str, int, bool)):
            raise PolicyError(f"input {k!r} must be a string, number or boolean")
        out[str(k)] = str(v).lower() if isinstance(v, bool) else str(v)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "run"):
        p = sub.add_parser(name)
        p.add_argument("--rule", required=True)
        p.add_argument("--input", action="append", default=[], metavar="KEY=VALUE")
        p.add_argument("--inputs-env", metavar="VAR", help="read inputs as a JSON object from env var VAR")
        if name == "run":
            p.add_argument("--timeout", type=int, default=1500, help="seconds to watch the run (default 1500)")
            p.add_argument("--no-audit", action="store_true", help="skip the ledger comment")
    p = sub.add_parser("advance")
    p.add_argument("--dry-run", action="store_true", help="read verdicts and print the plan; accept nothing")
    p.add_argument("--timeout", type=int, default=1500)
    p = sub.add_parser("sequence")
    p.add_argument("name")
    p.add_argument("--dry-run", action="store_true", help="validate the steps; dispatch nothing")
    p.add_argument("--timeout", type=int, default=3600)
    p = sub.add_parser("live-site")
    p.add_argument("domain")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "live-site":
            domain_spec = find_rule(load_policy(), "103-pages-apex")["inputs"]["domain"]
            if not re.fullmatch(domain_spec["pattern"], args.domain):
                raise PolicyError(f"live-site: {args.domain!r} is not a valid domain")
            is_live, reason = live_site(args.domain)
            print(json.dumps({"domain": args.domain, "live": is_live, "reason": reason}))
            return 0
        policy = load_policy()
        if args.cmd == "advance":
            return cmd_advance(policy, args.dry_run, args.timeout)
        if args.cmd == "sequence":
            return cmd_sequence(policy, args.name, args.dry_run, args.timeout)
        rule = find_rule(policy, args.rule)
        if args.inputs_env:
            if args.input:
                raise PolicyError("give inputs with --input or --inputs-env, not both")
            inputs = inputs_from_env(args.inputs_env)
        else:
            inputs = parse_inputs(args.input)
        validate_inputs(rule, inputs)
        if args.cmd == "check":
            print(json.dumps({"rule": rule["id"], "ok": True}))
            return 0
        return cmd_run(policy, rule, inputs, args.timeout, not args.no_audit)
    except PolicyError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - report and fail closed
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
