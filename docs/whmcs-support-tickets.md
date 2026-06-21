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
2. Workflow **"36. WHMCS - Issue to Ticket"** triggers on the label, opens a ticket from the form
   fields (charity email, domain, details), and comments the ticket **#** back to the issue.
3. New requests → priority Medium, subject `[New Request]`; break/fix → priority High, subject
   `[Break/Fix]`.

Manual/ad-hoc use: **"35. WHMCS - Open Ticket"** (dry-run by default) and **"37. WHMCS - Export
Tickets"**.

## Note on the environment approval gate

All WHMCS workflows use the `whmcs-prod` environment, which requires a deployment approval, so each
ticket-opening run waits for a reviewer. For higher-volume / hands-off ticket creation, FFC can
relax the protection on a dedicated environment for these low-risk ticket jobs (or approve runs as
they arrive). See `CLAUDE.md` for how to approve via `gh`.
