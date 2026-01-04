# GitHub Actions Environments and Secrets

This repo uses **GitHub Actions Environments** to:

- Store sensitive values as **Environment secrets** (recommended)
- Add optional **required reviewers** before a job can run

This is used for:

- `cloudflare-prod` (Cloudflare DNS workflows)
- `m365-prod` (Microsoft 365 / Graph / Exchange Online workflows)

## Where to configure Environments

1. In GitHub, open the repo.
2. Go to **Settings** → **Environments**.
3. Click **New environment**.
4. Name it exactly (case-sensitive):
   - `cloudflare-prod` or
   - `m365-prod`
5. (Recommended) Add **Required reviewers** to gate any workflow jobs that reference that
   environment.
6. Add **Environment secrets** for that environment.

## How secrets/vars resolve

- If a job specifies `environment: <name>`, GitHub makes available:
  - **Environment secrets** for `<name>`
  - **Repository secrets**
- If the same secret name exists in both places, the **Environment secret wins**.
- `${{ vars.NAME }}` reads a variable (non-secret) from the environment or repository.

## `cloudflare-prod`

Used by the numbered DNS workflows in `.github/workflows/`.

Environment secrets:

- `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` (required)
- `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS` (required)

Repository variable (non-secret):

- `ALLOWED_ACTORS` (optional guardrail; comma-separated GitHub usernames)

## `m365-prod`

Used by:

- `.github/workflows/5-m365-domain-and-dkim.yml`
- `.github/workflows/6-m365-list-domains.yml`

### Identifiers vs secrets

For Azure OIDC login, the values we store are **identifiers** (not passwords):

- `FFC_AZURE_CLIENT_ID` (App/Service Principal client id)
- `FFC_AZURE_TENANT_ID` (tenant id)
- `AZURE_SUBSCRIPTION_ID` (subscription id; only needed when you actually target Azure resources)

These are stored as **Environment secrets** in this repo so the names can remain stable.

Environment secrets (required for the M365 workflows in this repo):

- `FFC_AZURE_CLIENT_ID`
- `FFC_AZURE_TENANT_ID`

### Split-environment preflight

The preflight workflow `.github/workflows/7-m365-domain-preflight.yml` is intentionally split into
two jobs so secrets do not have to be duplicated across environments:

- **Graph job** runs in `m365-prod` and requires:
  - `FFC_AZURE_CLIENT_ID`
  - `FFC_AZURE_TENANT_ID`
- **Cloudflare job** runs in `cloudflare-prod` and requires:
  - `FFC_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`
  - `CM_CLOUDFLARE_API_TOKEN_ZONE_AND_DNS`

Environment secrets (optional):

- `AZURE_SUBSCRIPTION_ID` (only needed if you later add steps that require an Azure subscription
  context)

Environment secrets (optional, only needed for DKIM app-only mode):

- `EXO_TENANT` (example: `freeforcharity.onmicrosoft.com`)
- `EXO_ORGANIZATION` (often the same as `EXO_TENANT`)
- `EXO_CERT_PFX_BASE64` (base64-encoded PFX for certificate auth)
- `EXO_CERT_PFX_PASSWORD` (optional; empty if the PFX has no password)

## `m365-prod`: OIDC and app prerequisites (Azure/Entra)

The M365 workflows authenticate with GitHub OIDC via `azure/login@v2`, then use Azure CLI to fetch a
Microsoft Graph token.

This is what makes the workflow **headless**:

- No interactive browser/device-code prompts
- GitHub issues an OIDC token, and Entra validates it via the federated credential
- Azure CLI then obtains a Microsoft Graph access token for the app

You must configure the Entra application referenced by `FFC_AZURE_CLIENT_ID`:

- **Federated credential** (for GitHub OIDC)

  - Issuer: `https://token.actions.githubusercontent.com`
  - Audience: `api://AzureADTokenExchange`
  - Subject (recommended pattern when workflows use `environment: m365-prod`):
    - `repo:FreeForCharity/FFC-Cloudflare-Automation-:environment:m365-prod`
  - Note: if you scope the federated credential differently (branch/ref), the subject format
    changes.

- **Azure subscription access**

  - Not required for the current M365 workflows because they set `allow-no-subscriptions: true`.
  - Only required if you add steps that manage Azure resources.

- **Microsoft Graph permissions**

  - For the domain listing/status workflows, grant (and admin-consent) one of:
    - Application permission `Domain.Read.All` (recommended), or
    - Application permission `Directory.Read.All`

- **Exchange Online (DKIM) permissions (only if using app-only DKIM)**
  - Certificate-based auth is required.
  - The app must be permitted for Exchange Online app-only access and have the required EXO
    permissions.

## Troubleshooting quick hits

- `azure/login` fails:

  - Confirm the job has `permissions: id-token: write`.
  - Confirm `FFC_AZURE_CLIENT_ID` / `FFC_AZURE_TENANT_ID` are set on the `m365-prod` environment.
  - Confirm the Entra app federated credential subject matches the repo/environment.

- Graph calls return `403 Forbidden`:

  - Confirm the Entra app has Graph **application** permissions and **admin consent**.

- DKIM steps fail:
  - Confirm the PFX secrets exist and import works.
  - Confirm the Entra app is allowed for EXO app-only and has the right EXO permissions.
