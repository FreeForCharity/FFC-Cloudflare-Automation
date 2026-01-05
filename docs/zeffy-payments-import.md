# Zeffy payments import (draft generator)

This repo includes scripts/workflows to generate a **first-draft** Zeffy payments import CSV from WHMCS (read-only).

## What it generates

- `artifacts/whmcs/whmcs_clients.csv` (from WHMCS `GetClients`)
- `artifacts/whmcs/whmcs_transactions.csv` (from WHMCS `GetTransactions`)
- `artifacts/zeffy/zeffy_payments_import_draft.csv` (joined + mapped)

## Running via GitHub Actions

1. Go to Actions → **"10. WHMCS -> Zeffy Payments Import (Draft)"**
2. Run workflow (defaults should work)
3. Download the artifact `zeffy_payments_import_draft`

## Mapping notes

- Canonical output is aligned to Zeffy’s **Payments Import Template** fields described at https://support.zeffy.com/importing-payments.
- `paymentMethod` is mapped from WHMCS `gateway` using Zeffy’s allowed values (card, cash, cheque, transfer, unknown, free, manual, pad, ach, applePayOrGooglePay).
- Traceability back to WHMCS is included in Zeffy’s `annotation` field (transaction/invoice IDs, gateway, etc.).

## Important: Zeffy template columns

Zeffy’s import template column names can differ depending on the template/version you download.

The generator supports two output modes:

- **canonical** (default): emits a Zeffy-template-shaped set of columns (including required address fields) using the exact Zeffy field names from the help article. Required fields that WHMCS may not have are defaulted to safe placeholders (for example `address/city/postalCode = "unknown"`).
- **template**: if you provide a local template CSV (header row only is enough), the script will emit exactly those headers and fill what it can.

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

If you paste/upload the exact Zeffy template header row you’re using, we can tune the alias mapping so the draft matches 1:1.
