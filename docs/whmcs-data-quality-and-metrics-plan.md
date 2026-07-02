# WHMCS data quality assessment & full-duration metrics plan

Free For Charity has **10+ years of WHMCS data (2014–2026)**, but the metrics derived from it so far
do not withstand scrutiny: three independent counting methods (point-in-time dashboard reads,
client-status reconstruction, service-evidence classification) produce three different series, and
the live runs of workflows 214/215 surfaced concrete data-cleanliness gaps that explain why. This
document is the holistic assessment: what the data actually contains, where it is dirty or
incomplete, what the defensible Candid metric definitions are for the **entire duration** of the
dataset, and the phased plan to clean, capture, and **maintain** these metrics over time.

Refs #490, #491. Companion docs: the freeforcharity.org `docs/METRICS-PLAYBOOK.md` (site + Candid
consumption side) and `docs/uncaptured-comms-discovery.md` (Gap B).

## 1. Evidence inventory — what the live runs established (2026-07-02)

From workflow **214** (clients by signup year × current status) and **215** (client groups, product
catalog, service evidence), all aggregate-only:

- **419 clients** total; signup years 2015–2026; **543 services** across 24 products in 8 product
  groups (earliest service regdate **2014** — predating the earliest client signup, see G8).
- **Client groups (native nonprofit classification) are 96% unassigned**: 404 of 419 clients have no
  group; only 15 are classified (3 For Profit, 4 Pre-501c3, 8 501c3-SWH).
- **51 clients have zero services** — accounts that never took a product.
- **211 clients have Active client-status but only 200 have an Active service** — the two "active"
  notions disagree; neither has history.
- **gid-6 (charity products) reaches 321 distinct clients** (Pre-501c3 onboarding 185, 501c3
  onboarding 31, free .org + M365 email 57, nonprofit WordPress hosting, etc.); For-Profit product
  groups reach ~20 clients; 1 internal account.
- **At least one record contains malformed UTF-8** (WHMCS's JSON encoder rejects whole responses;
  workflow 215 now falls back to XML).
- The legacy Candid/profile series (2021: 76, 2022: 104, 2023: 221) was reconstructed as
  **point-in-time admin-dashboard reads** with inconsistent counter choice (Active vs Total) and
  timing (the "2023" value was a 2024 read of the _total_ counter).
- The **sites-list** (`sites-list/sites_list.json`, rebuilt weekly from WHMCS + Cloudflare +
  WPMUDEV + health probes) tracks **376 domains** across 5 work tiers — including legacy-WordPress
  charities that have **no WHMCS client record at all**. WHMCS alone undercounts the served
  population; the sites-list alone lacks the member/organization dimension.

## 2. The gaps (numbered, with the fix each one needs)

| #   | Gap                                                                                               | Impact on metrics                                                          | Fix                                                                          |
| --- | ------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| G1  | Client group unassigned for 404/419 clients                                                       | Native nonprofit classification unusable                                   | Phase 2 backfill (auto-assign from evidence, dry-run first)                  |
| G2  | "Legal Organization Status" custom field population unknown (not in bulk API)                     | Second classification unverified                                           | Phase 1 survey via per-client `GetClientsDetails`                            |
| G3  | 51 clients with no services                                                                       | Inflates client counts; nature unknown (abandoned signup? prospect? stub?) | Phase 1 aging report → Phase 2 disposition                                   |
| G4  | Client status vs service status disagree (211 vs 200) and neither has history                     | "Active members in year Y" not answerable from status                      | Use per-year **invoice/order evidence** (§3) as the activity signal          |
| G5  | Encoding corruption (≥1 record, malformed UTF-8)                                                  | Breaks JSON API consumers                                                  | Phase 1 identify count → Phase 2 manual fix in admin                         |
| G6  | WHMCS ↔ sites-list coverage gap (legacy-WP charities not in WHMCS; WHMCS clients without domains) | Member counts and domain counts describe different populations             | Phase 1 domain-level reconciliation (WHMCS domains ⨝ sites_list.json)        |
| G7  | No historical status → past years unreconstructable from status flags                             | Legacy series can't be validated                                           | Invoices/orders/transactions span the full 10+ years — use them (§3)         |
| G8  | Date anomalies (service regdate 2014 < first client signup 2015)                                  | Suggests migrated/backdated records at the WHMCS install boundary          | Phase 1 anomaly count; document the boundary                                 |
| G9  | Possible duplicate accounts per organization                                                      | Overcounts members                                                         | Phase 1 dedup survey (aggregate: candidate-dup count by matching org fields) |

## 3. Target metric model — full duration, evidence-based

The principle: **a metric value for year Y must be derivable from records that carry their own
dates** (service regdate, invoice date, order date, domain regdate) — never from a today's-status
flag, and never from a dashboard read that leaves no trail.

For every calendar year 2014 → present, for the **nonprofit population** (clients holding charity
products, cross-checked against client group + legal-status field once backfilled):

| Metric                           | Definition                                                                                                                   | Source                                                                                                                           |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| New nonprofit members [Y]        | Distinct nonprofit clients whose **first charity service** was registered in Y                                               | `GetClientsProducts` regdate (working — workflow 215)                                                                            |
| Cumulative nonprofit members [Y] | Running total of the above                                                                                                   | Same                                                                                                                             |
| **Active nonprofit members [Y]** | Distinct nonprofit clients with **any activity evidence in Y**: invoice dated Y, order dated Y, or a service active during Y | `GetInvoices` / `GetOrders` (10+ years of history — the strongest historical signal we have)                                     |
| Domains managed [Y]              | Domains under management at end of Y                                                                                         | WHMCS domains regdates + sites-list git history (weekly snapshots are committed, so recent years have true point-in-time values) |
| Organizations served (holistic)  | Union of WHMCS nonprofit members + sites-list-only legacy-WP organizations                                                   | Phase 1 reconciliation (G6)                                                                                                      |

This model answers Candid's per-year "Progress & results" format for the entire duration, states its
methodology honestly, and keeps working as years accumulate.

## 4. Phased plan

### Phase 1 — Deep survey (read-only; extends the 21x workflow family)

1. **216 — WHMCS Activity Metrics (full history)**: paginate `GetInvoices` + `GetOrders`, tally per
   client-year (aggregate only) → the **active-members-by-year** matrix for 2014–present, split
   nonprofit vs other using the product evidence. This is the series that finally answers "how many
   nonprofit members were active in year Y" with dated evidence.
2. **217 — WHMCS Client Fields Survey**: throttled per-client `GetClientsDetails` loop (419 calls) →
   coverage of the Legal Organization Status custom field, cross-tab vs product evidence and client
   group (aggregate counts only).
3. **218 — WHMCS ⨝ sites-list reconciliation**: join workflow 201's domain export with
   `sites_list.json` → counts of in-both / WHMCS-only / sites-list-only (the legacy-WP population),
   by health tier. Establishes the **holistic organizations-served** number.
4. **Data-quality report** (can live in 216–218 summaries): no-service client aging, candidate
   duplicate count, date anomalies, encoding-corrupt record count.

### Phase 2 — Cleanup (write workflows, dry-run default, whmcs-prod gated)

5. **Client-group backfill**: auto-assign groups 1–6 from evidence (products held + legal-status
   field + For-Profit product lines); dry-run report first, operator approves, then live. Fixes G1
   so the native classification becomes trustworthy.
6. **Onboarding enforcement**: the charity-onboard workflow (204) sets client group at creation; a
   periodic audit flags unclassified clients (same pattern as the existing enforce-standard
   workflows) so G1 never regresses.
7. **Disposition the no-service clients** (G3) and **fix encoding-corrupt records** (G5, manual in
   admin, count verified by re-run).

### Phase 3 — Canonical, maintained metrics

8. **219 — Candid Metrics (canonical)**: one workflow emitting the full-duration per-year series
   from the cleaned data, scheduled (e.g. monthly) + dispatchable, delivering the JSON to the
   freeforcharity.org repo via PR (the `sites-list-generate` pattern) so `whmcs-members.json`, the
   site, and the Candid paste sheet refresh from one pipeline with zero manual reads.
9. **Definitions locked in the paste sheet**: each Candid metric carries its definition +
   methodology string; year values only ever come from the canonical workflow.

### Sequencing note

Phase 1 requires **no writes and no cleanup** — it can run immediately and will produce the first
honest full-duration numbers (activity-evidence-based) even before the group backfill. Phase 2 makes
the native classification agree with the evidence. Phase 3 is what makes this durable: after it,
"updating Candid" means reading the current sheet, never re-deriving anything by hand.

## 5. Interim guidance (until Phase 1 lands)

- Treat **all** WHMCS-derived member counts as provisional. The least-wrong current series is the
  gid-6 first-service cumulative (93 / 142 / 250 for 2023/2024/2025), but it undercounts activity (a
  member served in Y without a new service that year is invisible) and excludes the sites-list-only
  legacy population.
- Do **not** paste a member series into Candid until the Phase 1 activity matrix exists; the other
  census-backed metrics (text-derived series, domains, sites built) are unaffected and remain safe
  to publish.
