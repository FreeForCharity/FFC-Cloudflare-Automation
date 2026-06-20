# Domain Transfer Automation (eNOM → Cloudflare Registrar)

This is the **transfer-execution/readiness layer** for project
[#157](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/157). It does the
automatable work around a registrar transfer; the one step that the Cloudflare API cannot do (the
inbound transfer itself) stays a guided manual click in the dashboard. See
[domain-transfer-automation-scope.md](domain-transfer-automation-scope.md) for the scope boundary
and why transfers are dashboard-only.

## Control flow

```
04. Export Inventory (All Sources)  ──►  enom-cloudflare-transition-inventory.csv (#325)
            │                                         │
            ▼                                         ▼
14. Transfer Readiness Preflight  ◄── consumes ──  whmcs_domains.csv
            │  (read-only, offline)
            ├─► domain_transfer_preflight.csv   (ready / blocked / review / done)
            └─► runbooks/runbook-<domain>.md    (per-domain dashboard steps)
            │
            ▼
16. Transfer EPP/Auth Code Probe   (per ready domain: is the code copy-pasteable?)
            │
            ▼
   (manual) Cloudflare dashboard: Transfer Domains → enter EPP → confirm
            │
            ▼
25. Post-Transfer Verification     (registrar = Cloudflare, NS correct, site Live)
```

## Components

| Step                 | Workflow                                                     | Script                                  | Side effects                            |
| -------------------- | ------------------------------------------------------------ | --------------------------------------- | --------------------------------------- |
| Readiness preflight  | `14. Domain - Transfer Readiness Preflight (Report) [WHMCS]` | `scripts/domain-transfer-preflight.ps1` | None (read-only, offline)               |
| EPP code probe       | `16. Domain - Transfer EPP/Auth Code Probe (Admin) [WHMCS]`  | `scripts/domain-transfer-epp-probe.ps1` | `execute` mode may email the registrant |
| Post-transfer verify | `25. Domain - Post-Transfer Verification (Report) [CF]`      | `scripts/domain-transfer-verify.ps1`    | None (read-only)                        |

## 1. Readiness preflight

Offline analysis over CSV — no API calls, no secrets. The workflow first exports WHMCS domains, then
evaluates each one. Per domain it reports a `readiness`:

- **ready** — no blockers found in the inventory data.
- **blocked** — expired / expiring within `-MinDaysToExpiry` (default 15), or inside the ICANN
  `-PostRegLockDays` (default 60) post-registration lock.
- **review** — registrar is unknown or not eNOM; confirm it is transferable.
- **done** — already at Cloudflare Registrar; nothing to do.

Lock status and WHOIS privacy are not in the inventory exports, so they appear as "confirm in
dashboard" items in each runbook rather than hard gates.

```bash
# Consume the #325 categorized inventory (preferred):
pwsh -File scripts/domain-transfer-preflight.ps1 \
  -InventoryCsv _run_artifacts/enom_cloudflare_transition_inventory.csv \
  -RunbookDir _run_artifacts/runbooks

# Or run from a plain WHMCS export:
pwsh -File scripts/domain-transfer-preflight.ps1 \
  -WhmcsDomainsCsv whmcs_domains.csv
```

## 2. EPP / auth-code probe

Settles whether the transfer code is **copy-pasteable**. WHMCS `DomainRequestEPP` returns an
`eppcode` inline only if the registrar module supports it; otherwise it emails the registrant. The
probe reports `deliveredInline: true|false`. It is gated: `dry-run` does nothing live; `execute`
makes the call (which may email the registrant). The literal code is hidden unless `show_code` is
set.

```bash
# Dry run (no side effects):
pwsh -File scripts/domain-transfer-epp-probe.ps1 -Domain example.org

# Probe for real (reports inline vs email-only):
pwsh -File scripts/domain-transfer-epp-probe.ps1 -Domain example.org -Execute
```

## 3. The manual step (Cloudflare dashboard)

The Cloudflare Registrar API has no inbound-transfer endpoint, so this stays manual:
**dash.cloudflare.com → Domain Registration → Transfer Domains**, select the domain, paste the
EPP/auth code, confirm contacts, and complete (this charges and adds one year). The per-domain
runbook from step 1 lists these.

## 4. Post-transfer verification

After the transfer completes, confirm it landed:

```bash
pwsh -File scripts/domain-transfer-verify.ps1 -Domain example.org -Account FFC
```

`verified` is true only when the domain is managed by Cloudflare Registrar, its nameservers are
Cloudflare/FFC, and the site is Live or Redirect.

## See also

- [domain-transfer-automation-scope.md](domain-transfer-automation-scope.md) — scope and boundaries
- [domain-inventory-reconciliation.md](domain-inventory-reconciliation.md) — the inventory side
- [cloudflare-domain-registration.md](cloudflare-domain-registration.md) — buying _new_ domains
  (distinct path)
