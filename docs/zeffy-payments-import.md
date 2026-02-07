# Zeffy payments import (draft generator)

This repo includes scripts/workflows to generate a **first-draft** Zeffy payments import CSV from
WHMCS (read-only).

The primary entrypoint is GitHub Actions workflow **33. WHMCS -> Zeffy Payments Import (Draft)**.

## What it generates

The workflow produces these files and uploads them as artifacts:

- `artifacts/whmcs/whmcs_clients.csv` (from WHMCS `GetClients`)
- `artifacts/whmcs/whmcs_transactions.csv` (from WHMCS `GetTransactions`)
- `artifacts/whmcs/whmcs_invoices.csv` (from WHMCS `GetInvoices`, only when
  `include_zero_invoices=true`)
- `artifacts/whmcs/whmcs_invoices_deleted_clients.csv` (invoice lookups for `userid=0` transactions)
- `artifacts/zeffy/zeffy_payments_import_draft.csv` (Zeffy import draft)
- `artifacts/zeffy/zeffy_payments_import_draft.xlsx` (Zeffy import draft, preferred for Zeffy UI)
- `artifacts/zeffy/zeffy_payments_import_draft-part*.csv` (only when split due to
  `max_rows_per_file`)

Reports (uploaded as separate artifacts):

- `artifacts/zeffy/zeffy_payments_import_draft.validation_errors.csv` / `.md` (row-level validation
  failures)
- `artifacts/zeffy/zeffy_payments_import_draft.transforms_companyName.csv` / `.md` (companyName
  sanitization audit)
- `artifacts/zeffy/zeffy_payments_import_draft.transforms_personName.csv` / `.md` (first/last name
  sanitization audit)

When output is split, `*-part*.xlsx` files are also produced.

Artifact names (download from the Actions run page):

- `whmcs_clients`
- `whmcs_transactions`
- `whmcs_invoices`
- `whmcs_invoices_deleted_clients`
- `zeffy_payments_import_draft`

## Running via GitHub Actions

1. Go to Actions → **"33. WHMCS -> Zeffy Payments Import (Draft)"**
2. Run workflow (defaults should work)
3. Download the artifact `zeffy_payments_import_draft`

## Manual upload into Zeffy

1. Download the `zeffy_payments_import_draft` artifact from the Actions run.
2. Extract it and locate `zeffy_payments_import_draft.xlsx`.
3. In Zeffy: Payments import → upload the `.xlsx`.

Notes:

- Zeffy requires `.xlsx` (not just `.csv`). Use the `.xlsx` output from the artifact.
- The generator writes key fields as text in Excel (including `date (MM/DD/YYYY)`) so leading zeros
  are preserved.

### Workflow inputs

This workflow is `workflow_dispatch` only.

- `api_url` (default: `https://freeforcharity.org/hub/includes/api.php`)
- `clients_output` (default: `artifacts/whmcs/whmcs_clients.csv`)
- `transactions_output` (default: `artifacts/whmcs/whmcs_transactions.csv`)
- `invoices_output` (default: `artifacts/whmcs/whmcs_invoices.csv`)
- `zeffy_output` (default: `artifacts/zeffy/zeffy_payments_import_draft.csv`)
- `start_date` / `end_date` (optional, `YYYY-MM-DD`)
- `max_rows` (default: `200000`, safety cap for transactions export)
- `max_rows_per_file` (default: `10000`, Zeffy import cap)
- `include_zero_invoices` (default: `true`)

Secrets are stored in the GitHub Actions environment `whmcs-prod`.

## Mapping notes

- Canonical output is aligned to Zeffy’s **Payments Import Template** fields described at
  https://support.zeffy.com/importing-payments.
- `paymentMethod` is mapped from WHMCS `gateway` using Zeffy’s allowed values (card, cash, cheque,
  transfer, unknown, free, manual, pad, ach, applePayOrGooglePay).
- Traceability back to WHMCS is included in Zeffy’s `annotation` field (transaction/invoice IDs,
  gateway, etc.).

## Important behavior

### Invoice-only $0 invoices

WHMCS `$0` invoices do not always show up in `GetTransactions`. When `include_zero_invoices=true`
and an invoices export is present, the generator will:

- Identify invoices where `total == 0`.
- Skip any `$0` invoice that already has a matching transaction (by `invoiceid`).
- Append remaining invoice-only `$0` invoices as pseudo-payments with Zeffy `paymentMethod=free`.

This is primarily used to ensure Zeffy contact creation has a corresponding “payment” row.

### Deleted clients (`userid=0`)

Some WHMCS transactions can have `userid=0` (deleted clients). These rows are excluded from the
Zeffy output so imports do not break on missing/unknown contact data.

The generator also skips rows where the resolved client is missing a first/last name (Zeffy requires
a contact name).

To satisfy Zeffy “Invalid format” validation:

- `companyName` has double quotes removed (e.g., `FRC Team 1726 "Nifty..."` becomes
  `FRC Team 1726 Nifty...`).
- `firstName` / `lastName` are sanitized to remove digits and unsupported characters (e.g.,
  `Post245` becomes `Post`).

If you want to review what changed, see the transforms reports in the `zeffy_transforms_report`
artifact.

The workflow also emits a separate export (`whmcs_invoices_deleted_clients`) that looks up invoice
details (including contact fields) for any `userid=0` transactions that still have an `invoiceid`.
This file is for investigation/auditing, not automatic inclusion.

### Output splitting + header validation

- If `max_rows_per_file` is set (default `10000`), the generator will split the output into
  `*-part*.csv` files when needed.
- The workflow validates the CSV header matches Zeffy’s expected header string before uploading the
  output.

## Verified example run

Verified successful import run (2026-02-07):

- Actions run: https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/21782782643
- Key outcomes:
  - Zeffy import succeeded using `zeffy_payments_import_draft.xlsx`
  - `companyName` contains no double quotes
  - `firstName`/`lastName` contain no digits

These counts are expected to vary over time.

## Important: Zeffy template columns

Zeffy’s import template column names can differ depending on the template/version you download.

The generator supports two output modes:

- **canonical** (default): emits a Zeffy-template-shaped set of columns (including required address
  fields) using the exact Zeffy field names from the help article. Required fields that WHMCS may
  not have are defaulted to safe placeholders (for example `address/city/postalCode = "unknown"`).
- **template**: if you provide a local template CSV (header row only is enough), the script will
  emit exactly those headers and fill what it can.

Current canonical header set matches the template header names:

`firstName,lastName,amount,address,city,postalCode,country,type,formTitle,rateTitle,email,language,date (MM/DD/YYYY),state/province,paymentMethod,receiptUrl,ticketUrl,receiptNumber,companyName,note,annotation`

Example (local):

```powershell
pwsh -File .\scripts\zeffy-payments-import-draft.ps1 \
  -ClientsCsv artifacts/whmcs/whmcs_clients.csv \
  -TransactionsCsv artifacts/whmcs/whmcs_transactions.csv \
  -OutputFile artifacts/zeffy/zeffy_payments_import_draft.csv \
  -Mode template \
  -TemplatePath .\examples\zeffy\payments-import-template.csv
```

If you paste/upload the exact Zeffy template header row you’re using, we can tune the alias mapping
so the draft matches 1:1.

## Troubleshooting

- The workflow fails during invoices export with errors like
  `A parameter cannot be found that matches parameter name 'ApiUrl'` or
  `A parameter cannot be found that matches parameter name 'OutputFile'`.
  - Ensure the invoices exporter accepts these workflow parameters. See
    `scripts/whmcs-invoices-export.ps1`.
- CI fails with `Invoke-Formatter` / formatting errors
  - Run the repo formatter (see `scripts/format-powershell.ps1`) and re-push.
- Workflow fails with a Zeffy header mismatch
  - Zeffy template header strings can change. Use the generator `template` mode with an exported
    Zeffy template header to force exact header output.
- Large exports
  - Use `start_date`/`end_date` to narrow the window and keep Zeffy CSV(s) under the import limit.
