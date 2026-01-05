Place your Zeffy payments import template CSV here if you want the draft generator to emit the exact column headers Zeffy expects.

Example path:
- `examples/zeffy/payments-import-template.csv`

Then run:

```powershell
pwsh -File .\scripts\zeffy-payments-import-draft.ps1 -Mode template -TemplatePath .\examples\zeffy\payments-import-template.csv
```
