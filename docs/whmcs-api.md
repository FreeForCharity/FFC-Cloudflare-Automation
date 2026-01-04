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

### API access control (IP restriction)

WHMCS restricts API access by IP by default. If the WHMCS API rejects requests, you may see an
error like `Invalid IP x.x.x.x`.

GitHub-hosted runners do not have a single fixed outbound IP, so a strict IP allowlist is usually
not compatible with GitHub-hosted runners.

Options:

- Self-hosted runner (fixed outbound IP): allowlist that IP in WHMCS.
- WHMCS API Access Key (bypass IP restriction): configure an access key in WHMCS and pass it with
  API calls (recommended for GitHub-hosted runners).
- Run locally from an allowlisted IP.

### WHMCS API Access Key (bypass IP restriction)

WHMCS supports an API Access Key that can bypass the IP restriction.

In your WHMCS server `configuration.php`, add:

- `$api_access_key = 'your_secret_passphrase_here';`

Then configure a GitHub environment secret in `whmcs-prod`:

- Secret name: `WHMCS_API_ACCESS_KEY`
- Secret value: the exact passphrase you set in `configuration.php`

## GitHub Actions environment

The workflow
[.github/workflows/7-whmcs-export-domains.yml](../.github/workflows/7-whmcs-export-domains.yml) runs
in environment `whmcs-prod`.

Create an environment secret where:

- Secret **name** = the WHMCS **API Identifier**, but uppercased (GitHub secret names are stored as uppercase)
- Secret **value** = the WHMCS **API Secret**

This repo currently assumes a single credential set and hard-codes the identifier/secret name in the
workflow, so the workflow does not require selecting a credential at runtime.

Optional:

- `WHMCS_API_URL` (if your endpoint differs)

## Output and downloads

The GitHub Actions workflow writes the export CSV into a safe workspace folder by default:

- `artifacts/whmcs/whmcs_domains.csv`

It then uploads an Actions artifact named:

- `whmcs_domains`

The workflow also writes a short job summary that includes a direct link to the workflow run page
(where the artifact download UI lives).

## Local usage

Run the export script locally:

- Set env vars `WHMCS_API_IDENTIFIER` and `WHMCS_API_SECRET`
- Then run: `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\whmcs-domain-export.ps1`

The script writes `whmcs_domains.csv` by default.

To write to a specific path (for example, matching the workflow default), pass:

- `-OutputFile artifacts/whmcs/whmcs_domains.csv`
