## Version description
This release finalizes the WHMCS → Zeffy Payments Import (Draft) pipeline for **successful manual upload into Zeffy**.

Key outcomes:
- Zeffy import succeeds using the generated `.xlsx` artifact.
- `companyName` is sanitized to remove double quotes (Zeffy rejects quotes).
- `firstName` / `lastName` are sanitized to remove digits and unsupported characters (e.g., `Post245` → `Post`).
- GitHub Actions run summary now highlights both `.csv` and `.xlsx` outputs and links to transforms/validation reports.
- Runbook updated with the manual Zeffy upload procedure and the current sanitization/diagnostics behavior.

Verified successful run example:
- https://github.com/FreeForCharity/FFC-Cloudflare-Automation/actions/runs/21782782643

## Contributors
- @clarkemoyer
- GitHub Copilot

## Included changes
- Fix Zeffy `companyName`/name-field validation failures by sanitizing quotes/digits (commits: `dfe5449`, `8297fa3`).
- Improve workflow summary + upload transforms report for person-name sanitization.
- Documentation updates for `.xlsx` artifact and manual Zeffy upload steps (commit: `8e036af`).
