# Charity intake autonomy: the gate broker (workflow 753)

Approved by Clarke on 2026-11-12 in the project thread "Make charity intake run autonomously". The
full review (value workflow, scheduled-run inventory, ranked bottlenecks) is the project doc
"Charity intake autonomy review"; this page is the in-repo design of record.

## Why

The charity lane ([charity-lane-pipeline.md](charity-lane-pipeline.md)) was designed on 2026-10-11
to run without a person, and its gate policy (`config/charity-lane-gate-policy.json`) was approved
then. It still needed Clarke on every charity because:

1. **The only gate-clearing path ran on the retired local Conductor.** Cloud sessions get 403 on
   `pending_deployments`, the cloud safety check refuses an agent approving or dispatching
   production writes on its own, and cloud sessions cannot download run artifacts.
2. **Nothing drives the lane.** The Onboarding Conductor scheduled task has been off since
   2026-10-12; the daily intake check is read-only by instruction.
3. **703's weekly cron parked at its `github-prod` gate** until janitor 734 reaped it (7 of 11
   runs), so the sites list went stale.

## Design

`753. Repo - Charity-Lane Gate Broker` runs `scripts/charity_lane_gate.py` inside GitHub Actions.
The script is unchanged in what it may approve; 753 only removes the need for an operator's machine.

| Entry point                                             | What it does                                                                                                                                                                                           |
| ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `schedule '20 14 * * *'` → `advance`                    | Dispatches 232 (read-only verdicts), downloads its fraud-screened `charity-lane-verdicts` artifact, and accepts each `ACCEPT` row with `fraud_check=pass` through 226 (gate 1) or 211 (gate 2). Max 3. |
| `schedule '10 8 * * 1'` → `sequence weekly-sites-list`  | 601 (WPMUDEV export, approves `wpmudev-prod`), then 703 with `prime_wpmudev=false` (approves `github-prod`). Stops at the first step that does not succeed.                                            |
| `workflow_dispatch mode=run rule=<id> inputs_json={…}`  | One policy rule, e.g. `701-provision`, `707-content-apply`, `205-templated`. This is how a cloud thread or scheduled task asks for a gated step: dispatching 753 works from the cloud.                 |
| `workflow_dispatch mode=advance\|sequence dry_run=true` | Plan and validate only; approves nothing.                                                                                                                                                              |

Every path goes through the same steps as before: validate inputs against exactly one rule (closed
set, enum, pattern, max length) → dispatch → identify the single new run by this token's user →
approve only the rule's environments (Cloudflare writes need the live-site check) → post the outcome
to ledger #1608. A run the broker did not dispatch is never approved.

## Security model

- **Hard security stays on `main`.** 753 refuses any ref but `refs/heads/main` and checks out
  `main`. The environment `charity-lane-broker` has a deployment branch policy of `main` only, so no
  branch or PR can load its token. What may be approved is `config/charity-lane-gate-policy.json` on
  `main`, a policy file that only Clarke merges.
- **Narrow credential.** `wr-all-ffc-charity-lane-broker-github-pat` in `kv-ffc-admin-prod-cbm`: a
  fine-grained token for this repository only, with Actions (read/write), Deployments (read/write),
  Issues (read/write) and Contents (read). It cannot push, change settings, or reach another
  repository.
- **Dedicated Azure identity.** 753 signs in to Azure as **FFC Application Intake**
  (`vars.INTAKE_FFC_AZURE_KV_CLIENT_ID`), not the shared `ffc-admin-kv-writer`. Every gated write
  lane federates to the writer, so putting an ungated environment on it would let 753 read every
  `wr-all-*` secret in the vault. Intake holds **Key Vault Secrets User** scoped to the one secret
  above and nothing else, so an ungated run can reach exactly one credential.
- **No reviewer on the broker environment, by design.** The reviewer moved from each run to the code
  and policy that define the runs (the principle in
  [human-intervention-map.md](human-intervention-map.md)). The environment's lack of a reviewer is
  visible in settings and asserted by `scripts/check-environment-protection.py`.
- **Only 753 may use it.** `test_only_753_uses_the_broker_environment` fails any other workflow that
  names `charity-lane-broker`.
- **Never allowed** (policy `never` list, enforced by the closed rule set and its tests): 113
  register, 120 with DNS or for a live site, 211 cancel/fraud, 226 reconcile or multi-order, a paid
  domain order (pid 39/41), any `m365-prod` write.
- **Bounded harm.** At most 3 accepts per day during the testing period; one broker at a time
  (concurrency group); inputs reach the script through `env`, never interpolated into shell.

**Residual risk.** Anyone who can dispatch workflows in this repo (Clarke, and agents acting as him)
can ask 753 for any action the policy allows, within its input limits. That is the set the Conductor
already held on 2026-10-11, now with a GitHub-side audit trail. The worst a prompt-injected agent
can do is send a templated ticket or provision a Pages repo; it cannot spend money, move live
traffic, or approve anything outside the list.

## One-time setup (before the PR can merge)

CI stays red until this is done: `check-environment-protection.py` requires every environment a
workflow names to exist.

1. **Environment.** Create `charity-lane-broker` with no required reviewers and a deployment branch
   policy of selected branches = `main`.

   ```bash
   gh api -X PUT repos/FreeForCharity/FFC-Cloudflare-Automation/environments/charity-lane-broker \
     --input - <<'JSON'
   {"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
   JSON
   gh api -X POST repos/FreeForCharity/FFC-Cloudflare-Automation/environments/charity-lane-broker/deployment-branch-policies \
     -f name=main -f type=branch
   ```

2. **Azure identity.** App registration **FFC Application Intake** (client id
   `c7d0a578-58a5-4a04-b490-82a4d203f752`, object id `67445b55-c007-4cf5-a246-c601ccfd3a9d`) with a
   service principal, and one federated credential
   `gha-FFC-Cloudflare-Automation-charity-lane-broker`, subject
   `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:charity-lane-broker` (issuer and
   audience as in [azure-oidc-federated-credentials.md](azure-oidc-federated-credentials.md)).
   Repository variable `INTAKE_FFC_AZURE_KV_CLIENT_ID` holds the client id; the tenant reuses
   `WR_ALL_FFC_AZURE_TENANT_ID`.

3. **Token.** Clarke mints a fine-grained PAT (resource owner FreeForCharity, repository
   FFC-Cloudflare-Automation only, permissions above, 1-year expiry) and stores it as
   `wr-all-ffc-charity-lane-broker-github-pat` in `kv-ffc-admin-prod-cbm`. Rotation = a new secret
   version; 733 reminds quarterly.

4. **Secret-scoped role.** After step 3 (a secret-scoped assignment needs the secret to exist),
   grant Intake read on that one secret and nothing wider:

   ```bash
   KV_ID=$(az keyvault show --name kv-ffc-admin-prod-cbm --query id -o tsv)
   az role assignment create --assignee-object-id 53e96c75-2071-4649-9a84-c1625c411883 \
     --assignee-principal-type ServicePrincipal --role "Key Vault Secrets User" \
     --scope "$KV_ID/secrets/wr-all-ffc-charity-lane-broker-github-pat"
   ```

5. **First run.** Dispatch 753 `mode=advance dry_run=true`, then
   `mode=sequence rule=weekly-sites-list dry_run=true`; read the plan on #1608. Then one live
   `mode=run` for a pending, ACCEPT-verdict order.

## Footer address rule (Clarke, 2026-11-12)

A charity's website footer always shows City and State. It shows a street address only when the
applicant typed one into the application field marked as the **public** street address. That field
is the applicant's consent to publish it, so no further confirmation step is needed.

| Application                                    | Footer address                          |
| ---------------------------------------------- | --------------------------------------- |
| Public street-address field filled and valid   | that street address, then City, State   |
| Public street-address field present but empty  | City, State                             |
| Older application with no public address field | City, State, until the charity confirms |

Never sourced from the IRS / ProPublica record, the WHMCS client mailing address, or any field the
applicant did not mark public. On 2026-11-12 btsscv.org and projectherinc.org were provisioned with
the IRS address and corrected the same day (FFC-EX-btsscv.org#7, FFC-EX-projectherinc.org#6).

## What still needs a person

- Paying for a domain (Gate 4 order accept, 113).
- Cutting over a domain that already serves a live site (120 with DNS).
- Escalations: fraud flags, duplicates, ineligible applicants, a repeated validation failure.
- Changing the policy file itself.
