# WHMCS API (Read-only)

This repo can query WHMCS via the built-in WHMCS API to export managed domain names in **read-only**
mode.

## API endpoint

WHMCS API endpoint format:

- `https://<your-whmcs-host>/<whmcs-path>/includes/api.php`

For FFC (based on the admin URL `https://freeforcharity.org/hub/globaladmin`), the endpoint is
expected to be:

- `https://freeforcharity.org/hub/includes/api.php`

## Credentials

In WHMCS Admin:

- Configuration → System Settings → Manage API Credentials
- Create credentials to obtain an **API Identifier** and **API Secret**
- Assign at least one API Role that allows read access for domains (the workflow uses the
  `GetClientsDomains` action)

## GitHub Actions environment

The workflow
[.github/workflows/7-whmcs-export-domains.yml](../.github/workflows/7-whmcs-export-domains.yml) runs
in environment `whmcs-prod`.

Create an environment secret where:

- Secret **name** = the WHMCS **API Identifier** (as shown in WHMCS)
- Secret **value** = the WHMCS **API Secret**

This matches the workflow input `credential_set`, so humans can correlate runs with the WHMCS
credential set.

Optional:

- `WHMCS_API_URL` (if your endpoint differs)

## Local usage

Run the export script locally:

- Set env vars `WHMCS_API_IDENTIFIER` and `WHMCS_API_SECRET`
- Then run: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\whmcs-domain-export.ps1`

The script writes `whmcs_domains.csv` by default.
