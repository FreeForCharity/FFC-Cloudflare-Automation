#!/usr/bin/env python3
"""Send one WHMCS application ticket (workflow 205) per listed order, built from a 232 run.

The gap list in each ticket comes from that order's reason codes in a fraud-screened
232 `charity-lane-verdicts` artifact, rendered as short plain-English lines grouped
by topic (so a full older-form gap list fits 205's 1500-character extra_message).
Every send goes through `charity_lane_gate.execute` under rule `205-templated`, so
the template allowlist, input checks, gate approval and the audit comment are the
same as any other charity-lane dispatch.

    plan   --verdicts-run ID --template T --orders 1,2,3 [...]   print every ticket, send nothing
    send   (same arguments)                                     dispatch them one at a time (205)
    batch  (same arguments)                                     dispatch ONE 236 run for the whole
                                                                list: one approval, every ticket

`batch` is the default way to send more than one ticket. 205 runs share one
concurrency group, so separate dispatches reach the whmcs-prod gate one at a time
(one approval each) and GitHub cancels all but one pending run in a group.

Options:
    --head "text"               a first line for every ticket in this call (e.g. a final notice)
    --special ORDER="text"      an extra first item for one order (repeatable)
    --preview ORDER=URL         a "Preview: URL" line for one order (repeatable)
    --results FILE              where `send` writes the per-order results (JSON)
    --report FILE               read the verdicts report from a local file (236 does this)
    --specials-json / --previews-json JSON   the same as --special / --preview, as
                                {"ORDER": "text" | ["text", ...]} (how 236 passes them)
    --plan-out FILE / --summary FILE         `plan` also writes the plan (JSON) and a
                                Markdown table of every ticket

Codes the applicant cannot act on (link checks we could not complete, internal
routing codes) are left out. An unknown code stops the whole batch before anything
is sent, so a new code never reaches an applicant as nothing.
Exit codes: 0 all sent (or planned); 1 any send failed or a row was missing; 2 refused.
"""

from __future__ import annotations

import argparse
import json
import sys

import charity_lane_gate as gate

CATEGORIES = [
    "About your organization",
    "For your website",
    "Public contact details",
    "Public links (the public page, not an admin, dashboard or login link)",
    "People",
    "Boxes to tick in your application",
]

_OFFICERS = "three different officers (president or chair, secretary, treasurer)"
_OFFICER_LI = "each officer's own LinkedIn profile (linkedin.com/in/...)"
_GITHUB = "the GitHub username of your website maintainer"
_EIN_URL = "a public URL (such as Candid) showing your EIN under your organization's name"

# code -> (category index, short item). Items repeat across codes on purpose; each is shown once.
ITEMS: dict[str, tuple[int, str]] = {
    "ORG_NAME_MISSING": (0, "its name"),
    "ORG_NAME_WITHHELD": (0, "its name"),
    "ORG_NAME_IS_APPLICANT": (0, "its name (the application shows a person's name)"),
    "ORG_LEGAL_NAME_MISSING": (0, "its legal name as registered with the IRS or your state"),
    "EIN_MISSING": (0, "its EIN"),
    "EIN_NAME_MISMATCH": (0, _EIN_URL),
    "EIN_NOT_VERIFIED": (0, _EIN_URL),
    "CANDID_EIN_MISMATCH": (0, "a Candid profile under the same EIN as your application"),
    "MISSION_MISSING": (0, "mission statement"),
    "FOUNDING_YEAR_MISSING": (0, "year founded"),
    "FOUNDING_YEAR_INVALID": (0, "year founded"),
    "TAGLINE_MISSING": (1, "tagline"),
    "SITE_DESCRIPTION_MISSING": (1, "short description"),
    "SHORT_DESCRIPTION_MISSING": (1, "one-sentence description"),
    "PROGRAM_1_MISSING": (1, "at least one program with a short description"),
    "TEMPLATE_CHOICE_MISSING": (1, "template choice"),
    "DOMAIN_OWNERSHIP_MISSING": (1, "whether you already own your domain"),
    "DESIRED_DOMAIN_INVALID": (1, "the domain you want (for example yourcharity.org)"),
    "PUBLIC_EMAIL_MISSING": (2, "email"),
    "PUBLIC_EMAIL_INVALID": (2, "email (the one given is not valid)"),
    "PUBLIC_PHONE_MISSING": (2, "phone"),
    "PUBLIC_PHONE_INVALID": (2, "phone (the one given is not valid)"),
    "FOOTER_LOCATION_MISSING": (2, "city and state"),
    "FOOTER_LOCATION_INVALID": (2, "city and state"),
    "FACEBOOK_URL_MISSING": (3, "Facebook page"),
    "FACEBOOK_URL_INVALID": (3, "Facebook page"),
    "FACEBOOK_PAGE_NOT_PUBLIC": (3, "Facebook page"),
    "LINKEDIN_URL_MISSING": (3, "LinkedIn company page"),
    "LINKEDIN_URL_NOT_ORG_PAGE": (3, "LinkedIn company page"),
    "LINKEDIN_PAGE_NOT_PUBLIC": (3, "LinkedIn company page"),
    "DONATION_URL_MISSING": (3, "Zeffy donation page"),
    "DONATION_URL_NOT_PUBLIC": (3, "Zeffy donation page"),
    "DONATION_URL_NOT_FOUND": (3, "Zeffy donation page (the one given does not open)"),
    "VOLUNTEER_URL_MISSING": (3, "Idealist volunteer page"),
    "VOLUNTEER_URL_NOT_PUBLIC": (3, "Idealist volunteer page"),
    "VOLUNTEER_URL_NOT_FOUND": (3, "Idealist volunteer page (the one given does not open)"),
    "CANDID_URL_MISSING": (3, "Candid profile"),
    "CANDID_URL_NOT_PUBLIC": (3, "Candid profile"),
    "CANDID_URL_NOT_FOUND": (3, "Candid profile (the one given does not open)"),
    "CURRENT_SITE_URL_INVALID": (3, "current website, if you have one"),
    "CURRENT_SITE_UNREACHABLE": (3, "current website (the one given does not open)"),
    "OFFICER_NAMES_MISSING": (4, _OFFICERS),
    "OFFICERS_NOT_DISTINCT": (4, _OFFICERS),
    "OFFICER_LINKEDIN_MISSING": (4, _OFFICER_LI),
    "OFFICER_LINKEDIN_NOT_PROFILE": (4, _OFFICER_LI),
    "OFFICER_LINKEDIN_NOT_DISTINCT": (4, _OFFICER_LI),
    "GITHUB_USER_MISSING": (4, _GITHUB),
    "GITHUB_USER_INVALID": (4, _GITHUB),
    "GITHUB_USER_NOT_FOUND": (4, _GITHUB + " (the one given does not exist)"),
    "OFFICERS_CONSENT_MISSING": (5, "officers agree to be listed"),
    "US_NOT_ATTESTED": (5, "established in the United States"),
    "TOS_NOT_ATTESTED": (5, "Terms of Service"),
}

# Codes the applicant cannot act on: ours to check, or internal routing.
NOT_FOR_APPLICANT = {
    "TRACK_MISMATCH",
    "ORDER_HAS_MULTIPLE_LANE_ITEMS",
    "DUPLICATE_DOMAIN_OTHER_CLIENT",
    "LINKEDIN_URL_UNVERIFIED",
    "FACEBOOK_URL_UNVERIFIED",
    "LINK_UNVERIFIED",
    "LINK_BOT_WALL",
    "GITHUB_USER_UNVERIFIED",
    "COMPLETENESS_NOT_CHECKED",
    "FRAUD_REVIEW",
}


class BatchError(Exception):
    pass


BATCH_RULE = "236-batch"
MAX_BATCH = 50


def batch_dispatch_inputs(a, orders: list[str], specials: dict, previews: dict) -> dict:
    """Pure-ish: the 236 inputs for one batch, validated against rule 236-batch."""
    if len(orders) > MAX_BATCH:
        raise BatchError(f"a batch holds at most {MAX_BATCH} orders; split the list")
    if len(a.head) > 1:
        raise BatchError("236 takes one --head line")
    inputs = {
        "verdicts_run": str(a.verdicts_run),
        "template": a.template,
        "orders": ",".join(orders),
        "head": a.head[0] if a.head else "",
        "specials_json": json.dumps(specials, separators=(",", ":")) if specials else "",
        "previews_json": json.dumps(previews, separators=(",", ":")) if previews else "",
        "dry_run": "false",
    }
    inputs = {k: v for k, v in inputs.items() if v != ""}
    gate.validate_inputs(gate.find_rule(gate.load_policy(), BATCH_RULE), inputs)
    return inputs


def render(codes: list[str], specials: list[str] = (), head: list[str] = ()) -> str:
    """Pure: the extra_message for one order."""
    buckets: list[list[str]] = [[] for _ in CATEGORIES]
    for code in codes:
        if code in NOT_FOR_APPLICANT:
            continue
        if code not in ITEMS:
            raise BatchError(f"no applicant wording for code {code!r}; add it to ITEMS before sending")
        idx, item = ITEMS[code]
        if item not in buckets[idx]:
            buckets[idx].append(item)
    lines = list(head)
    body = [f"- {s}" for s in specials] + [f"- {CATEGORIES[i]}: {'; '.join(b)}." for i, b in enumerate(buckets) if b]
    if body:
        lines += ["Still needed:", *body]
    if not lines:
        raise BatchError("nothing to say: no gaps, no special item and no head line")
    return "\n".join(lines)


def kv(pairs: list[str], what: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for p in pairs or []:
        if "=" not in p:
            raise BatchError(f"{what} {p!r} is not ORDER=value")
        k, v = p.split("=", 1)
        out.setdefault(k.strip(), [])
        out[k.strip()].append(v.strip())
    return out


def from_json(raw: str, what: str) -> dict[str, list[str]]:
    """{"ORDER": "text" | [..]} -> {"ORDER": [..]}; blank means none."""
    if not (raw or "").strip():
        return {}
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BatchError(f"{what} is not JSON: {e}") from e
    if not isinstance(doc, dict):
        raise BatchError(f"{what} must be a JSON object keyed by order id")
    out: dict[str, list[str]] = {}
    for k, v in doc.items():
        vals = v if isinstance(v, list) else [v]
        if not str(k).strip().isdigit() or not all(isinstance(x, str) and x.strip() for x in vals):
            raise BatchError(f"{what}: {k!r} must be an order id mapped to text")
        out[str(k).strip()] = [x.strip() for x in vals]
    return out


def merge(a: dict, b: dict) -> dict:
    out = {k: list(v) for k, v in a.items()}
    for k, v in b.items():
        out.setdefault(k, []).extend(v)
    return out


def summary_markdown(plan: list[dict]) -> str:
    lines = [f"### {len(plan)} ticket(s) to open", "", "| order | client | template | chars |", "| --- | --- | --- | --- |"]
    for p in plan:
        i = p["inputs"]
        lines.append(f"| {p['order']} | {i['client_id']} | {i['template']} | {len(i['extra_message'])} |")
    for p in plan:
        lines += ["", f"#### Order {p['order']} (client {p['inputs']['client_id']})", "", "```text", p["inputs"]["extra_message"], "```"]
    return "\n".join(lines) + "\n"


def build(report: dict, template: str, orders: list[str], head: list[str], specials: dict, previews: dict) -> list[dict]:
    """Pure: the 205 inputs for each order, validated against rule 205-templated."""
    if report.get("fraud_check") != "screened":
        raise BatchError("verdicts report is not fraud-screened; use the run's charity-lane-verdicts artifact")
    rows = {str(r.get("order_id")): r for r in report.get("verdicts") or []}
    missing = [o for o in orders if o not in rows]
    if missing:
        raise BatchError(f"orders not in the verdicts report: {', '.join(missing)}")
    rule = gate.find_rule(gate.load_policy(), "205-templated")
    out = []
    for o in orders:
        r = rows[o]
        lead = list(head) + [f"Preview: {u}" for u in previews.get(o, [])]
        msg = render([str(c) for c in r.get("reason_codes") or []], specials.get(o, []), lead)
        # dry_run must be explicit: 205 defaults to a dry run, which would "send" nothing.
        inputs = {"template": template, "client_id": str(r.get("client_id")), "extra_message": msg, "dry_run": "false"}
        gate.validate_inputs(rule, inputs)
        out.append({"order": o, "inputs": inputs})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["plan", "send", "batch"])
    ap.add_argument("--verdicts-run", required=True, type=int)
    ap.add_argument("--template", required=True)
    ap.add_argument("--orders", required=True, help="comma-separated order ids, sent in this order")
    ap.add_argument("--head", action="append", default=[])
    ap.add_argument("--special", action="append", default=[])
    ap.add_argument("--preview", action="append", default=[])
    ap.add_argument("--results", default="ticket-batch-results.json")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--report", default="")
    ap.add_argument("--specials-json", default="")
    ap.add_argument("--previews-json", default="")
    ap.add_argument("--plan-out", default="")
    ap.add_argument("--summary", default="")
    a = ap.parse_args(argv)
    orders = [o.strip() for o in a.orders.split(",") if o.strip()]
    if len(orders) != len(set(orders)):
        print("REFUSED: an order is listed twice", file=sys.stderr)
        return 2
    try:
        # 236's plan job runs `plan`, not `batch`, so only `batch` ever reached the
        # rule-236 validation below -- and the send job's count check compares the plan
        # with the same plan, so it agrees at any size. A direct workflow_dispatch of 236
        # therefore planned (and on approval opened) any number of tickets, while the
        # workflow, catalog and safety doc all document a 50-order ceiling.
        if a.action in ("plan", "batch") and len(orders) > MAX_BATCH:
            raise BatchError(f"a batch holds at most {MAX_BATCH} orders; split the list")
        policy = gate.load_policy()
        if a.report:
            with open(a.report, encoding="utf-8-sig") as fh:
                report = json.load(fh)
        else:
            report = gate.download_json_artifact(policy["repo"], a.verdicts_run, "charity-lane-verdicts")
        specials = merge(kv(a.special, "--special"), from_json(a.specials_json, "--specials-json"))
        previews = merge(kv(a.preview, "--preview"), from_json(a.previews_json, "--previews-json"))
        plan = build(report, a.template, orders, a.head, specials, previews)
        batch_inputs = batch_dispatch_inputs(a, orders, specials, previews) if a.action == "batch" else None
    except (BatchError, gate.PolicyError) as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    if a.action == "plan":
        if a.plan_out:
            with open(a.plan_out, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(plan, fh, indent=2)
        if a.summary:
            with open(a.summary, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(summary_markdown(plan))
        for p in plan:
            print(f"== order {p['order']} client {p['inputs']['client_id']} ({len(p['inputs']['extra_message'])} chars)")
            print(p["inputs"]["extra_message"])
        return 0
    if a.action == "batch":
        code, res = gate.execute(policy, gate.find_rule(policy, BATCH_RULE), batch_inputs, a.timeout, audit=True)
        print(f"{'DISPATCHED' if code == 0 else 'FAILED'} batch of {len(plan)}: {(res or {}).get('run') or (res or {}).get('error')}")
        return 0 if code == 0 else 1
    rule = gate.find_rule(policy, "205-templated")
    results, worst = [], 0
    for p in plan:
        try:
            code, res = gate.execute(policy, rule, p["inputs"], a.timeout, audit=True)
        except Exception as e:  # keep going: one bad send must not hide the rest
            code, res = 1, {"error": str(e)[:300]}
        ok = code == 0 and (res or {}).get("conclusion") == "success"
        worst = max(worst, 0 if ok else 1)
        results.append({"order": p["order"], "client_id": p["inputs"]["client_id"], "ok": ok, "exit": code, **(res or {})})
        print(f"{'SENT' if ok else 'FAILED'} order {p['order']}: {(res or {}).get('run') or (res or {}).get('error')}")
        if code == gate.EXIT_DISPATCHED_UNIDENTIFIED:
            print("STOP: a run could not be identified; do not continue until it is checked", file=sys.stderr)
            break
    with open(a.results, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(results, fh, indent=2)
    return worst


if __name__ == "__main__":
    sys.exit(main())
