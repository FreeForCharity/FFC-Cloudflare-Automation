# Wix Domain Migration (Wix → eNom → Cloudflare)

The repeatable path for a charity whose domain is **registered with Wix**. This is the inbound
counterpart to [domain-transfer-automation.md](domain-transfer-automation.md), which covers the
outbound leg (eNom → Cloudflare Registrar) and assumes the domain is already at eNom.

Tooling: workflow `123. Domain - Inbound Transfer Preflight (Report)` and
`scripts/domain-inbound-transfer-preflight.ps1`.

## Why Wix is its own case

Every other registrar FFC has encountered lets the registrant delegate nameservers away. Wix does
not. Its own help center states that
[it is not possible to change name servers (edit NS records) for a Wix domain](https://support.wix.com/en/article/request-changing-name-server-ns-records-for-a-wix-domain)
— it is an open feature request with no timeline.

That single fact drives everything else:

- Cloudflare's **Free plan requires full nameserver delegation**. Partial (CNAME) setup, which
  avoids the nameserver change, is **Business plan only**.
- So a Wix-registered domain **cannot reach Cloudflare at all** until the registration itself moves.
  Adding the site in the Cloudflare dashboard will create the zone, but it stays **Pending**
  forever.
- Cloudflare Registrar **cannot** be the direct destination either: it only accepts domains whose
  zone is already active on Cloudflare nameservers, which is exactly what Wix is blocking.

**eNom breaks the deadlock.** It is FFC's registrar of record via WHMCS, it accepts a normal EPP
transfer from Wix, and once the domain is there nameservers can point wherever we like.

Do not open a Wix support ticket asking them to set custom nameservers. Reports of that working are
inconsistent at best, and the documented answer is no.

## The four stages

```
Wix  ──(1) unlock + EPP────►  (2) WHMCS transfer-in to eNom
                                          │
                                          ▼
                             (3) NS → Cloudflare   ◄── the step that actually delivers
                                          │
                                          ▼
                             (4) 60 days later: eNom → Cloudflare Registrar (optional)
```

**Stage 3 is the goal, not stage 4.** Once nameservers point at Cloudflare the site has DNS, CDN,
and WAF; who holds the registration is a billing detail. Treat stage 4 as cost optimization with no
deadline.

### Stage 0 — Capture the existing zone first

Losing mail routing is the main way this migration hurts a charity. Record everything before
touching anything; this capture is also the rollback.

```bash
for t in NS A AAAA CNAME MX TXT CAA; do dig +short $t example.org; done
dig +short CNAME www.example.org
dig +short TXT _dmarc.example.org
```

Pay particular attention to `MX` and the SPF/DKIM/DMARC `TXT` records. A charity on Google Workspace
or M365 loses email the moment those are dropped.

### Stage 1 — At Wix

- Confirm the domain is more than 60 days old at Wix (ICANN bars transfer before that).
- Disable DNSSEC — it will otherwise break resolution mid-transfer.
- Unlock the domain and disable WHOIS privacy.
- Confirm the admin contact email is reachable; the EPP code and the approval both go there.
- Request the EPP / authorization code (**Domains → select the domain → Advanced**).

### Stage 2 — Transfer in to eNom via WHMCS

A charity whose domain sits at a foreign registrar has **no domain product in WHMCS** — there is
nothing to drive the transfer from until an order exists. Place it first:

- Find the client id with `219. WHMCS - Application Detail` or `221. WHMCS - Application Search`.
- Run `229. WHMCS - Domain Order Add (Register/Transfer) (Admin) [WHMCS]` with `order_type=transfer`
  (pid 41, _Transfer your Existing Domain Name to the FFC CloudFlare_). It defaults to
  `mode=dry-run` — check the previewed request, then re-run with `mode=execute`.
- Then complete the transfer itself: WHMCS → Orders → the new order → submit the EPP code from stage
  1, registrar module eNom.
- Approve the transfer-confirmation email (unapproved transfers auto-complete in 5–7 days).
- Confirm the domain shows up in WHMCS with registrar `enom`.

> Use `order_type=register` (pid 39) instead only when FFC is buying the charity a **new** domain —
> that is a different path and does not belong in a migration.

### Stage 3 — Point nameservers at Cloudflare

No waiting period applies to a nameserver change.

- Run `110. DNS - Create Zone (Admin) [CF]`.
- Recreate every stage-0 record in the Cloudflare zone **before** switching nameservers.
- Verify MX/SPF/DKIM/DMARC match the stage-0 capture exactly.
- Set nameservers to the Cloudflare pair at eNom.
- Run `102. Domain - Add to FFC Cloudflare + WHMCS Nameservers (Admin) [CF+WHMCS]`.
- Run `103. Domain - Enforce Standard (GitHub Apex + M365) [CF+M365]` once the zone is active.

### Stage 4 — eNom → Cloudflare Registrar (later, optional)

The stage-2 transfer starts a **fresh ICANN 60-day lock**, so this cannot follow immediately. After
60 days the domain classifies as `ENOM_READY` and drops into the existing outbound pipeline:
workflows `115` → `116` → `117`.

## Reading the preflight output

Run workflow `123` (or the script directly) against the specific domain you are migrating. The
`domains` input is required and there is no fleet-wide default — this is a per-domain tool, run when
a charity's migration is actually on the table, not a sweep of the estate.

```bash
pwsh -File scripts/domain-inbound-transfer-preflight.ps1 \
  -Domain example.org -CurrentRegistrar 'Wix.com Ltd.' -RunbookDir _run_artifacts/runbooks
```

> **What is authoritative.** WHMCS is FFC's live record of the domains we hold — check it first, and
> treat it as the answer when anything disagrees. RDAP tells you what the _registry_ says about one
> domain right now, which is the useful cross-check when a domain is at a foreign registrar and
> therefore may not be in WHMCS at all. `docs/domain-registry-truth.csv` is a committed
> point-in-time snapshot for reference only: it is not current, it is not the live record, and work
> should not be driven from it.

| bucket                      | meaning                                                               |
| --------------------------- | --------------------------------------------------------------------- |
| `FOREIGN_TRANSFER_REQUIRED` | Registrar blocks NS delegation (Wix). Full four-stage path.           |
| `FOREIGN_NS_CAPABLE`        | Registrar allows NS delegation. Skip to stage 3; transfer at leisure. |
| `BLOCKED`                   | Foreign, but expired / locked / inside the ICANN lock.                |
| `UNKNOWN`                   | Registrar of record undetermined — run the RDAP probe.                |
| `AT_ENOM`                   | Already at eNom; use workflow `115` for the Cloudflare Registrar leg. |
| `AT_CLOUDFLARE`             | Already at Cloudflare Registrar; nothing to do.                       |

The two columns that decide the plan are **`nsDelegationAllowed`** and **`inboundPath`**. A
`BLOCKED` row with `nsDelegationAllowed = True` is not stuck: the transfer is blocked but the
nameserver change is not, and stage 3 can proceed today. The preflight says so explicitly in its
`reasons` column so a site does not sit unpublished waiting on a lock that never mattered.

Unlike the outbound preflight, a runbook is written for **every** actionable foreign domain, blocked
ones included, with the blockers listed at the top. Non-eNom domains previously classified as
`review` and produced nothing, which is how they fell off the worklist.

## Catching this at intake instead

The website request issue template and workflow `701` both ask **"Domain Registrar (who holds the
domain today)"**. Wix answers there should route straight to this playbook, so the multi-week
transfer starts on day one rather than being discovered midway through a provision.

## See also

- [domain-transfer-automation.md](domain-transfer-automation.md) — the outbound eNom → Cloudflare
  leg (workflows 115/116/117).
- [domain-transfer-automation-scope.md](domain-transfer-automation-scope.md) — why the transfer
  click itself stays manual.
- [domain-registry-truth.md](domain-registry-truth.md) — how the RDAP probe works and what its
  snapshot is (and is not) good for.
