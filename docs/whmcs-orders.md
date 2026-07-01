# WHMCS Orders (export, triage, and gated state changes)

How FFC reads and acts on WHMCS orders through the hardened APIM automation path. Everything here
follows the repo's standard pattern: credentials come from Key Vault via OIDC at runtime
(`whmcs-secrets-from-kv`), calls route through APIM, and write paths default to **dry-run**.

## Why

WHMCS accumulates orders that need human attention — new charity orders waiting to be accepted,
orders the fraud module flagged, and active services. Surfacing them in GitHub (instead of only the
WHMCS admin portal) lets FFC triage the backlog from the same place as the rest of the admin
automation. A live read at the time of writing showed 714 orders total, with ~49 Pending and ~31
Fraud in the most recent 100 — a real, actionable backlog.

## Scripts

| Purpose                  | API action(s)                                | Script                            |
| ------------------------ | -------------------------------------------- | --------------------------------- |
| Export orders (report)   | `GetOrders`                                  | `scripts/whmcs-orders-export.ps1` |
| Change one order's state | `AcceptOrder` / `CancelOrder` / `FraudOrder` | `scripts/whmcs-order-update.ps1`  |

### `whmcs-orders-export.ps1` (read-only)

Pages `GetOrders` and writes a CSV. Filters: `-Status` (e.g. `Pending`, `Active`, `Fraud`,
`Cancelled`), `-ClientId` (maps to WHMCS `userid`). CSV columns follow the live `GetOrders` shape:

```
id, ordernum, userid, contactid, name, status, paymentstatus, amount,
paymentmethod, paymentmethodname, date, fraudmodule, invoiceid, ipaddress,
lineitemcount, products
```

`products` is a `; `-joined summary of the order's line items (product names). No writes occur.

### `whmcs-order-update.ps1` (write; dry-run by default at the workflow layer)

Wraps the three order state-change actions via `-Action accept|cancel|fraud`. Emits
`{ action, dryRun, orderid, requested, skipped? }`.

- `-DryRun` previews the request (secrets redacted) and skips the status pre-check.
- On a **live** run it first reads the order's current status (`GetOrders`) and **refuses no-op or
  illegal transitions**, returning `{ ..., skipped = 'already-<status>' }` instead of calling the
  API:
  - `accept` — only a `Pending` order can be accepted.
  - `cancel` — a `Cancelled` order is left alone.
  - `fraud` — a `Fraud` order is left alone.
- Emails are suppressed unless `-SendEmail`; acceptance runs product auto-setup only with
  `-AutoSetup`.

## Workflows

- **210. WHMCS - Orders Triage** (`210-whmcs-orders-triage.yml`) — **read-only**.
  `workflow_dispatch`
  - weekday `schedule`. Runs the export once per status (default `Pending,Fraud,Active`), writes a
    count summary + a Pending-orders table to the job summary, uploads CSV artifacts, and can upsert
    one rolling `whmcs:triage` tracking issue (`open_tracking_issue: true`).
- **211. WHMCS - Order Update** (`211-whmcs-order-update.yml`) — inputs `order_id`, `action`
  (accept/cancel/fraud), `reason`, `dry_run` (default **true**). One explicit order per dispatch.

## Safety policy (no bulk automation)

There is intentionally **no** automatic accept-or-cancel loop. The 49 Pending and 31 Fraud orders
are **not** acted on automatically — each live state change is a single, explicit, human-dispatched
run with `dry_run=false`. Whether to adopt any automatic policy (e.g. auto-cancel orders the fraud
module flagged) is a **user decision** tracked on the orders issue, deliberately left manual here.

All order-write workflows run under the `whmcs-prod` environment approval gate.

## Verification

- Dry-run: `pwsh -File scripts/whmcs-order-update.ps1 -OrderId 123 -Action accept -DryRun` with
  dummy `WHMCS_API_IDENTIFIER`/`WHMCS_API_SECRET` set — confirms the preview JSON and `***`
  redaction with no API call.
- Read path (sandbox, no writes): fetch the APIM key from Key Vault with `az`, then
  `curl -X POST <gateway> -H 'Ocp-Apim-Subscription-Key: …' --data 'identifier=…&secret=…&responsetype=json&action=GetOrders&status=Pending&limitnum=5'`.
  Never echo the key or secret.
