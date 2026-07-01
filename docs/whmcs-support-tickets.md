# WHMCS Support Tickets (new requests + break/fix)

Track charity support work in WHMCS's ticket system: **new requests** and **break/fix** actions on
charity sites. Integration is **one-way**: a GitHub issue opens a WHMCS ticket, and the ticket
number is posted back to the issue.

## Departments

FFC currently has a **single** support department — `deptid 1` ("Support") — and WHMCS departments
can only be created in the admin UI (not via API). So all tickets route to deptid 1 and are
distinguished by **subject prefix + priority + GitHub label**.
`config/whmcs-support-departments.json` holds the routing map; if dedicated **Onboarding** /
**Break-Fix** departments are added later, just update the ids there.

## Scripts

| Purpose                      | API action                         | Script                                 |
| ---------------------------- | ---------------------------------- | -------------------------------------- |
| List departments (discovery) | `GetSupportDepartments`            | `whmcs-support-departments-export.ps1` |
| Open a ticket                | `OpenTicket`                       | `whmcs-ticket-open.ps1`                |
| Reply / internal note        | `AddTicketReply` / `AddTicketNote` | `whmcs-ticket-reply.ps1`               |
| Export tickets (report)      | `GetTickets`                       | `whmcs-tickets-export.ps1`             |

All write scripts support `-DryRun` (preview, secrets/customfields redacted) and emit JSON on
stdout. Log **break/fix remediation steps** as internal notes:
`whmcs-ticket-reply.ps1 -TicketId N -InternalNote -Message "..."`.

## GitHub → WHMCS flow

1. Open an issue from **"Support Request"** (`whmcs:new-request`) or **"Break/Fix"**
   (`whmcs:break-fix`) — `.github/ISSUE_TEMPLATE/08-support-request.yml` / `09-break-fix.yml`.
2. Workflow **"206. WHMCS - Issue to Ticket"** triggers on the label, opens a ticket from the form
   fields (charity email, domain, details), and comments the ticket **#** back to the issue.
3. New requests → priority Medium, subject `[New Request]`; break/fix → priority High, subject
   `[Break/Fix]`.

Manual/ad-hoc use: **"205. WHMCS - Open Ticket"** (dry-run by default) and **"208. WHMCS - Export
Tickets"**.

## Triage and templated responses (workflows 38/39)

- **209. WHMCS - Tickets Triage** (`209-whmcs-tickets-triage.yml`) — **read-only**.
  `workflow_dispatch` + a weekday `schedule`. Runs `whmcs-tickets-export.ps1` once per status
  (default `Open,Customer-Reply`), writes a Markdown table of tickets needing attention to the job
  summary, uploads CSV artifacts, and can upsert **one** rolling tracking issue labeled
  `whmcs:triage` (`open_tracking_issue: true`). This only _surfaces_ tickets — it performs no ticket
  writes — so the integration remains **one-way** (GitHub→WHMCS); replies are not synced back.
- **207. WHMCS - Ticket Respond** (`207-whmcs-ticket-respond.yml`) — posts a templated reply or
  internal note to one ticket using `scripts/whmcs-ticket-reply.ps1`. Template bodies live in
  `config/whmcs-ticket-templates.json` (`ack_new_request`, `ack_break_fix`, `need_info`,
  `internal_note`); `internal: true` templates become staff-only `AddTicketNote`s. Dry-run by
  default. A live (`dry_run=false`) client-visible reply is **human-gated** and requires a real
  WHMCS admin username (`admin_username`).

## Eligibility policy: US nonprofits only (international → TechSoup)

Free For Charity supports **nonprofits registered in the United States only**. When a request or
order comes from an organization whose **country of record is outside the US**, the policy is to
**gently decline, refer the organization to [TechSoup](https://www.techsoup.org)** (which supports
nonprofits internationally through its global partner network), and **cancel any related order(s)**.

Determining "international" — be sure before acting:

- Use the **client's country of record** (`GetClientsDetails` → `countrycode`), not just the message
  text or the order IP. Someone may be a US org whose staff is temporarily travelling abroad (their
  client country is still `US`) — those are **eligible** and must not be declined.
- **US territories are US**: `PR`, `VI` (U.S. Virgin Islands), `GU`, `AS`, `MP` are eligible — do
  **not** treat them as international.
- A request with **no client account** can only be judged from its message; decline only when the
  international status is unambiguous (e.g. "registered in <country> outside the US").

Templates: use **`international_techsoup`** (client-visible reply with the TechSoup referral) plus
the staff-only **`international_note`** in `config/whmcs-ticket-templates.json`. Cancel the related
order via **211. WHMCS - Order Update** (`-Action cancel`). As with all live writes, the reply and
the cancellation are **human-gated** (explicit per-order authorization).

### Scope matters: scan Active, not just Pending/Fraud

A foreign org can sit in any order state. When auditing eligibility, scan **all** states — `Active`
(already provisioned and being served), `Pending` (awaiting acceptance), and `Fraud` (already
auto-blocked) — by mapping each order's `userid` to its client `countrycode`. Note the practical
differences:

- **`Fraud`** orders are already blocked; `CancelOrder` only acts on `Pending`, so there is nothing
  to cancel — they are effectively already dead.
- **`Pending`** orders are cancellable via `CancelOrder` (omit `cancelsub` on $0/no-gateway orders;
  passing it triggers a gateway subscription-cancel that errors).
- **`Active`** orders/services are **live** — see the wind-down rule below.

### Existing **active** foreign clients (wind-down, never an abrupt cutoff)

An active foreign client is a charity **whose site/services we are currently hosting**. Cancelling
or terminating these takes a **live nonprofit website offline immediately** — an irreversible,
outward-facing action. **Never** bulk-terminate active services as part of an eligibility sweep.

The required process is a **deliberate wind-down**, one client at a time, with human authorization
at each step:

1. **Notify** the charity (`international_techsoup` reply, plus your direct appeal contact in case
   of misclassification) with an explicit **grace period** (e.g. 30–60 days).
2. **Offer a data/site export** so the organization does not lose its work.
3. **Terminate only after** the notice window — _or_ **grandfather** long-standing active charities
   if leadership prefers not to disrupt them.

Record the decision and date per client (staff note via `AddTicketNote` / `international_note`). The
country determination caveats above (country of record, US territories, travelling staff) apply
equally here — be **100% sure** before touching a live charity.

## Note on the environment approval gate

All WHMCS workflows use the `whmcs-prod` environment, which requires a deployment approval, so each
ticket-opening run waits for a reviewer. For higher-volume / hands-off ticket creation, FFC can
relax the protection on a dedicated environment for these low-risk ticket jobs (or approve runs as
they arrive). See `CLAUDE.md` for how to approve via `gh`.
