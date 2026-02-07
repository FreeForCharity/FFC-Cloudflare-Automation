## Version description

This release improves the WHMCS → Zeffy import-draft workflow so the output is directly usable in
Zeffy and more robust against bad source data.

Key outcomes:

- Zeffy import now produces a native `.xlsx` draft artifact (required by Zeffy).
- Dates are strictly formatted as `MM/DD/YYYY` and written as text in Excel output to preserve
  leading zeros.
- `companyName` is validated and sanitized (e.g., `@Home, Inc.` → `At Home, Inc.`) and a transforms
  report is emitted.
- The workflow can fail fast on validation errors while still uploading artifacts for debugging.

## Contributors

- @clarkemoyer
- GitHub Copilot

## Included changes (PRs)

- #136 Zeffy import: validate dates + sanitize companyName
- #137 Hotfix: companyName transform/validation
- #138 Zeffy draft: produce .xlsx for import
