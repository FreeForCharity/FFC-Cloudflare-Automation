# Google Workspace mobile management — allow personal Android phones without a work profile

> **Status: DRAFTED 2026-10-02, not yet applied.** The policy change is Admin-console only (Google
> exposes no write API for the mobile-management level), so a Workspace super admin
> (`clarkemoyer@freeforcharity.org`) makes it. Part B (delegation scopes) is also console-only. Part
> C can be run by an AI session afterwards to verify.

## Background

FFC policy: members use their own phones with **no work profile**. The Microsoft side was fixed on
2026-10-02 (app-protection policies no longer require Intune enrollment). The remaining blocker is
Google: `freeforcharity.org` is also a Google Workspace organization (customer `C00vzt6sw`, used for
Analytics, Tag Manager and Cloud Identity even though mail is on Microsoft), and its mobile
management is set to **Advanced**. Adding the FFC Google account to an Android phone therefore
installs _Android Device Policy_ and demands a work profile; declining aborts the account add.

Evidence (read via the delegated service account, 2026-10-02): all 14 Android registrations ever
recorded for FFC (2017–2024, users clarkemoyer / GlobalAdmin / chrisrae) carry the
`Google Apps Device Policy` / `Android Device Policy` agent; no registrations and no mobile audit
events in the last 60 days; the Pixel 9's work profile is **Intune's**, not Google's (Google has no
Pixel 9 record).

## Part A — change the management level (super admin, Admin console)

1. <https://admin.google.com> → **Devices** → **Mobile & endpoints** → **Settings** → **Universal
   settings**.
2. **General** → **Mobile management** → select the top-level organizational unit
   (`freeforcharity.org`) → set **Basic**. (Basic keeps a screen-lock requirement and remote
   _account_ wipe, but no Device Policy app and no work profile. **Unmanaged** drops management
   entirely; Basic is the recommended setting.)
3. Still under **Universal settings** → **Security**: if **Device approvals** → _Require admin
   approval_ is on, turn it off (a pending approval also blocks the account add).
4. **Android settings** → **Work profile**: make sure nothing requires or auto-creates a work
   profile for personal devices (**Available** or off, not **Required**).
5. Save; settings propagate within minutes.

Then on the new phone: **Settings → Passwords & accounts → Add account → Google** → sign in as
`<user>@freeforcharity.org`. No Device Policy prompt should appear. Outlook/Teams are signed in
directly (no Company Portal); they set an app PIN under the Intune app-protection policy.

Existing Google-managed phones are unaffected until re-added; a user removes an old Google work
profile from **Settings → Accounts → Work** on that phone.

## Part B — extend the service account's delegation so sessions can read device policy

The DWD client `ffc-workspace-admin@ffc-api-prod` (client id `110347116631668841237`) can list
mobile devices and audit logs today, but cannot read Cloud Identity device state or Workspace policy
values. Scopes are **exact-string** matched, so the read-only variants must be listed separately:

1. Admin console → **Security** → **Access and data control** → **API controls** → **Manage Domain
   Wide Delegation** → client id `110347116631668841237` → **Edit**.
2. Append these scopes (one per line) and save:

   ```text
   https://www.googleapis.com/auth/cloud-identity.devices
   https://www.googleapis.com/auth/cloud-identity.devices.readonly
   https://www.googleapis.com/auth/cloud-identity.policies.readonly
   https://www.googleapis.com/auth/admin.directory.device.mobile.readonly
   ```

   All four are read scopes. `admin.directory.device.mobile.action` (wipe / approve / block) is
   deliberately **not** included: add it only if an approved workflow needs to act on devices.

   Granted today (keep):
   `admin.directory.{user,group,orgunit,domain,customer,device.mobile, device.chromeos}`,
   `admin.reports.audit.readonly`, `cloud-platform`, plus the Analytics / Tag Manager / Search
   Console set listed in `docs/google-*.md`.

## Part C — verify from a session (read-only)

Key from Key Vault `read-all-cbm-google-workspace-service-account-key`, impersonating
`clarkemoyer@freeforcharity.org`, JWT flow as in `scripts/google-api-common.ps1`
(`Get-GoogleDwdAccessToken`; keep the key in memory — the helper sets strict mode, so dump raw JSON
and summarise outside PowerShell).

| Check                                      | Scope                              | Endpoint                                                                                               |
| ------------------------------------------ | ---------------------------------- | ------------------------------------------------------------------------------------------------------ |
| New phone registered without Device Policy | `admin.directory.device.mobile`    | `GET admin.googleapis.com/admin/directory/v1/customer/C00vzt6sw/devices/mobile?projection=BASIC`       |
| Mobile audit shows the registration        | `admin.reports.audit.readonly`     | `GET admin.googleapis.com/admin/reports/v1/activity/users/all/applications/mobile?startTime=<RFC3339>` |
| Management level now Basic (after Part B)  | `cloud-identity.policies.readonly` | `GET cloudidentity.googleapis.com/v1beta1/policies` → settings of type `settings/...mobile...`         |

Expected after Part A: the new device row's `userAgent` is **not** `Android Device Policy …`, and
`status` is `APPROVED` (Basic) with no approval pending.

## Why not `gcloud`

`gcloud` user credentials on the admin PC are reauth-broken (interactive browser only) and `gcloud`
has no command for Workspace mobile-management settings anyway; the Admin SDK can list devices and
take actions on them (wipe, approve, block) but cannot set the management level.
