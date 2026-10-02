# Intune / Entra policy automation identity (`ffc-intune-admin`) — setup runbook

> **Status: DRAFTED 2026-10-02, not yet applied.** Every command below is an Entra identity write,
> which the AI harness blocks; a Global Administrator (`clarkemoyer@freeforcharity.org`) runs them.
> Reads to verify the result can be run by anyone with `az`.

## Why

The 2026-10-02 Intune fix (turning off "require device compliance" on the default app-protection
policies, see `docs/mobile-device-management.md` if present, else the PR that added this file) had
to be done through an interactive device-code sign-in. That is the Microsoft analogue of the Google
problem this repo already solved with domain-wide delegation: an unattended identity that workflows
and AI sessions can use **without a human sign-in and without a stored user token**.

FFC already runs this pattern twice:

- `ffc-admin-kv-writer` / `ffc-admin-kv-reader` — GitHub OIDC federated credentials, no secret
  stored, for Key Vault (`docs/azure-oidc-federated-credentials.md`).
- `ffc-onedrive-backup` — a dedicated app holding **one** Graph application permission
  (`Files.ReadWrite.All`), used app-only from `FFC-IN-freeforcharity.org`
  (`docs/onedrive-backup-app-only-auth.md` in that repo).

This runbook adds a third: a dedicated app with the Intune Graph **application** permissions, used
app-only. Microsoft has no "act as a named user" delegation like Google's; application permissions
with admin consent are the equivalent, and audit entries name the app.

## What it can and cannot do

| Area                                                                | App-only? | Permission (application role)                                |
| ------------------------------------------------------------------- | --------- | ------------------------------------------------------------ |
| App protection (MAM) policies — read/write                          | yes       | `DeviceManagementApps.ReadWrite.All`                         |
| Device configuration / compliance policies — read/write             | yes       | `DeviceManagementConfiguration.ReadWrite.All`                |
| Enrollment restrictions, Android Enterprise binding — read/write    | yes       | `DeviceManagementServiceConfig.ReadWrite.All`                |
| Managed device inventory — read                                     | yes       | `DeviceManagementManagedDevices.Read.All`                    |
| Conditional Access and Entra policies — read                        | yes       | `Policy.Read.All`                                            |
| Group names for assignments — read                                  | yes       | `Group.Read.All`                                             |
| **Entra automatic MDM/MAM enrollment scope** (`mobilityManagement`) | **no**    | `Policy.ReadWrite.MobilityManagement` is **delegated-only**. |

So the Entra "MDM user scope" stays a human task (it is currently `none`, which is what we want).

## Identities

Follow the repo convention of separate read and write identities (like kv-reader / kv-writer).

| App (display name)  | Purpose                                           | Environment        | Gate    |
| ------------------- | ------------------------------------------------- | ------------------ | ------- |
| `ffc-intune-admin`  | ReadWrite roles above — policy changes            | `intune-prod`      | gated   |
| `ffc-intune-reader` | `*.Read.All` versions only — inventory and audits | `intune-prod-read` | ungated |

`ffc-` prefix = non-named service identity (KV naming rule). Client ids are non-secret GUIDs and go
in repository **variables**, like the existing `WR_ALL_FFC_AZURE_KV_CLIENT_ID`.

## Step 1 — create the apps and grant permissions (Global Admin)

```bash
TENANT=80c64bf2-fa5b-425c-9a5a-1fcf282d3274
GRAPH=00000003-0000-0000-c000-000000000000

# --- ffc-intune-admin (write) ---
az ad app create --display-name ffc-intune-admin --sign-in-audience AzureADMyOrg \
  --query "{appId:appId,id:id}" -o tsv
# note both ids: APP_ID (client id) and OBJ_ID (object id)
az ad sp create --id "$APP_ID"

# Graph application roles (ids are Microsoft Graph's fixed app-role ids, read 2026-10-02)
# application roles, in order: DeviceManagementApps.ReadWrite.All, DeviceManagementConfiguration.ReadWrite.All, DeviceManagementServiceConfig.ReadWrite.All, DeviceManagementManagedDevices.Read.All, Policy.Read.All, Group.Read.All
az ad app permission add --id "$APP_ID" --api $GRAPH --api-permissions \
  78145de6-330d-4800-a6ce-494ff2d33d07=Role \
  9241abd9-d0e6-425a-bd4f-47ba86e767a4=Role \
  5ac13192-7ace-4fcf-b828-1a26f28068ee=Role \
  2f51be20-0bb4-4fed-bf7b-db946066c75e=Role \
  246dd0d5-5bd0-4def-940b-0421030a5b68=Role \
  5b567255-7703-4780-807c-7be8301ae99b=Role
az ad app permission admin-consent --id "$APP_ID"

# --- ffc-intune-reader (read) — optional but recommended for ungated reporting lanes ---
az ad app create --display-name ffc-intune-reader --sign-in-audience AzureADMyOrg \
  --query "{appId:appId,id:id}" -o tsv
az ad sp create --id "$READER_APP_ID"
# application roles, in order: DeviceManagementApps.Read.All, DeviceManagementConfiguration.Read.All, DeviceManagementServiceConfig.Read.All, DeviceManagementManagedDevices.Read.All, Policy.Read.All, Group.Read.All
az ad app permission add --id "$READER_APP_ID" --api $GRAPH --api-permissions \
  7a6ee1e7-141e-4cec-ae74-d9db155731ff=Role \
  dc377aa6-52d8-4e23-b271-2a7ae04cedf3=Role \
  06a5fe6d-c49d-46a7-b082-56b1b14103c7=Role \
  2f51be20-0bb4-4fed-bf7b-db946066c75e=Role \
  246dd0d5-5bd0-4def-940b-0421030a5b68=Role \
  5b567255-7703-4780-807c-7be8301ae99b=Role
az ad app permission admin-consent --id "$READER_APP_ID"
```

Verify the grant took (the `admin-consent` call can return 200 and still need a minute):

```bash
az ad app permission list-grants --id "$APP_ID" --show-resource-name -o table
```

## Step 2 — GitHub Actions: federated credentials (no secret stored)

Exact-string subjects, same shape as every other credential in this repo:

```bash
az ad app federated-credential create --id "$OBJ_ID" --parameters '{
  "name": "github-oidc-intune-prod",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:FreeForCharity/FFC-Cloudflare-Automation:environment:intune-prod",
  "audiences": ["api://AzureADTokenExchange"] }'

az ad app federated-credential create --id "$READER_OBJ_ID" --parameters '{
  "name": "github-oidc-intune-prod-read",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:FreeForCharity/FFC-Cloudflare-Automation:environment:intune-prod-read",
  "audiences": ["api://AzureADTokenExchange"] }'
```

GitHub side:

```bash
R=FreeForCharity/FFC-Cloudflare-Automation
gh api -X PUT repos/$R/environments/intune-prod \
  -f 'reviewers[][type]=User' -F 'reviewers[][id]=2991935'      # clarkemoyer gates writes
gh api -X PUT repos/$R/environments/intune-prod-read            # ungated
gh variable set FFC_INTUNE_ADMIN_CLIENT_ID  -R $R --body "$APP_ID"
gh variable set FFC_INTUNE_READER_CLIENT_ID -R $R --body "$READER_APP_ID"
```

**Repo bookkeeping (same PR as the first workflow that uses a lane):** add both apps and their
environments to `config/federated-credentials.json`, and the rows to the tables in
`docs/azure-oidc-federated-credentials.md`. CI asserts the map matches the environments the
workflows actually use, so adding the lane without the map entry fails `Validate Repository`.

Workflow usage (mirrors the OneDrive job):

```yaml
jobs:
  intune:
    runs-on: ubuntu-latest
    environment: intune-prod
    permissions: { id-token: write, contents: read }
    steps:
      - uses: azure/login@v3
        with:
          client-id: ${{ vars.FFC_INTUNE_ADMIN_CLIENT_ID }}
          tenant-id: ${{ vars.WR_ALL_FFC_AZURE_TENANT_ID }}
          allow-no-subscriptions: true
      - name: Read Android app-protection policies
        run: >
          az rest --resource https://graph.microsoft.com --method get --url
          "https://graph.microsoft.com/beta/deviceAppManagement/androidManagedAppProtections"
          --query "value[].{name:displayName,requireCompliance:deviceComplianceRequired}" -o table
```

## Step 3 — AI sessions and operators outside Actions: certificate in Key Vault

Sessions on Clarke's PC or the cloud sandbox cannot present a GitHub OIDC token, so each app also
gets a **certificate**, generated and stored in Key Vault (never on disk in a session):

```bash
KV=kv-ffc-admin-prod-cbm
az keyvault certificate create --vault-name $KV --name ffc-intune-admin \
  --policy "$(az keyvault certificate get-default-policy | sed 's/CN=CLIGetDefaultPolicy/CN=ffc-intune-admin/')"
# export the PUBLIC part and attach it to the app (append — keeps any future creds)
az keyvault certificate download --vault-name $KV --name ffc-intune-admin --file /tmp/ffc-intune-admin.pem --encoding PEM
az ad app credential reset --id "$APP_ID" --cert @/tmp/ffc-intune-admin.pem --append
rm /tmp/ffc-intune-admin.pem
# repeat for ffc-intune-reader
```

The default policy is a 12-month self-signed cert with auto-renew; renewal only needs the
`credential reset --append` step again. A session then mints an app-only Graph token entirely in
memory: read the private key with `az keyvault secret show --name ffc-intune-admin` (Key Vault
exposes a certificate's private key as a secret of the same name), build a client-assertion JWT and
POST it to `/oauth2/v2.0/token` with `scope=https://graph.microsoft.com/.default` — the same shape
as `Get-GoogleDwdAccessToken` in `scripts/google-api-common.ps1`. A `Get-GraphAppToken` helper in a
new `scripts/graph-api-common.ps1` is the natural follow-up once the identity exists.

## Step 4 — verify end to end

1. Dispatch any workflow that uses `intune-prod-read` (or run the YAML above as a throwaway
   workflow): the `az rest` call must return the two Android "Default Mobile App Policy" rows with
   `requireCompliance = false`; swap in `iosManagedAppProtections` for the two iOS rows.
2. `python3 scripts/check-federated-credential-subjects.py --live` must pass after the map update.

## Rollback

`az ad app delete --id "$APP_ID"` removes the app, its service principal, consent, federated
credentials and certificate binding in one step; the Key Vault certificate can then be deleted.
