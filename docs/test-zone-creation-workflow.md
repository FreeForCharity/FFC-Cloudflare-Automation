# Testing the Zone Creation Workflow

This document provides instructions for testing the "03. DNS - Add Domain (Create Zone) (Admin)"
workflow.

## Prerequisites

Before testing, ensure the following are configured in the GitHub repository:

### Required GitHub Environment

- **Environment Name**: `cloudflare-prod`
- **Location**: Settings → Environments → cloudflare-prod

### Required Secrets

The following secrets must be configured in the `cloudflare-prod` environment:

1. **CLOUDFLARE_API_KEY_DNS_ONLY**
   - Cloudflare API token with permissions to create zones
   - Scopes required:
     - Zone:Edit (for creating zones)
     - Zone:Read (for checking existing zones)
2. **CLOUDFLARE_ACCOUNT_ID**
   - Your Cloudflare account ID
   - Can be found in Cloudflare Dashboard → Overview → Account ID

## Test Domain

For testing purposes, use: **homesforchange.org**

## Test Steps

### Step 1: Navigate to the Workflow

1. Go to the GitHub repository: https://github.com/FreeForCharity/FFC-Cloudflare-Automation-
2. Click on the **Actions** tab
3. Find and click on **"03. DNS - Add Domain (Create Zone) (Admin)"** in the workflows list

### Step 2: Run the Workflow

1. Click the **"Run workflow"** button
2. Fill in the inputs:
   - **Domain Name**: `homesforchange.org`
   - **Zone Type**: `full` (default)
   - **Enable Jump Start**: `true` (default - enables auto-scan of DNS records)
3. Click **"Run workflow"** to start

### Step 3: Monitor the Workflow Run

1. Wait for the workflow to complete (should take 30-60 seconds)
2. Click on the running workflow to view details
3. Expand the job steps to see detailed output

### Step 4: Verify Success

The workflow should:

1. ✅ **Validate inputs** - Confirm domain name format is correct
2. ✅ **Check secrets** - Verify secrets are available (without displaying values)
3. ✅ **Create or detect zone** - Either:
   - Create a new zone and display:
     - Zone ID
     - Status (pending/active)
     - Assigned name servers
   - OR detect that the zone already exists and display the same information

### Expected Output

Look for output similar to:

```
=== Cloudflare Zone Creation ===
Domain: homesforchange.org
Zone Type: full
Account ID: 12345678...

Checking if zone already exists...

✓ Zone already exists!
Zone ID: abc123def456...
Status: active
Name Servers:
  - ns1.cloudflare.com
  - ns2.cloudflare.com

::notice title=Zone Already Exists::Zone 'homesforchange.org' already exists with ID: abc123def456...
```

OR (if zone doesn't exist):

```
Creating new zone...

✓ Zone created successfully!
Zone ID: abc123def456...
Status: pending
Name Servers:
  - ns1.cloudflare.com
  - ns2.cloudflare.com

::notice title=Zone Created::Zone 'homesforchange.org' created with ID: abc123def456...
```

### Step 5: Verify in Cloudflare Dashboard

1. Log in to [Cloudflare Dashboard](https://dash.cloudflare.com)
2. Navigate to the account specified by CLOUDFLARE_ACCOUNT_ID
3. Look for `homesforchange.org` in the list of zones
4. Verify the zone exists and note:
   - Zone ID matches the workflow output
   - Name servers match the workflow output
   - Status is shown (pending or active)

## Security Validation

### No Secrets in Logs

**IMPORTANT**: Verify that secrets are NOT displayed in the workflow logs:

1. Check the workflow run logs
2. Confirm that:
   - ❌ CLOUDFLARE_API_KEY_DNS_ONLY value is NOT shown
   - ❌ CLOUDFLARE_ACCOUNT_ID full value is NOT shown (only first 8 chars with "..." is OK)
   - ✅ Only generic messages like "✓ Secrets validated (values not displayed for security)" appear

## Acceptance Criteria

- [ ] Workflow completes successfully without errors
- [ ] Zone is created (or detected if already exists)
- [ ] Zone ID is displayed in the output
- [ ] Name servers are displayed in the output
- [ ] Zone appears in Cloudflare dashboard under the correct account
- [ ] No secrets are printed in the workflow logs
- [ ] Workflow uses only CLOUDFLARE_API_KEY_DNS_ONLY and CLOUDFLARE_ACCOUNT_ID from the
      cloudflare-prod environment

## Troubleshooting

### Error: "CLOUDFLARE_API_KEY_DNS_ONLY secret is not set"

**Cause**: The secret is not configured in the `cloudflare-prod` environment.

**Solution**:

1. Go to Settings → Environments → cloudflare-prod
2. Add the CLOUDFLARE_API_KEY_DNS_ONLY secret

### Error: "CLOUDFLARE_ACCOUNT_ID secret is not set"

**Cause**: The secret is not configured in the `cloudflare-prod` environment.

**Solution**:

1. Go to Settings → Environments → cloudflare-prod
2. Add the CLOUDFLARE_ACCOUNT_ID secret

### Error: "Zone creation failed: insufficient permissions"

**Cause**: The API token doesn't have permission to create zones.

**Solution**:

1. Go to Cloudflare Dashboard → My Profile → API Tokens
2. Edit or recreate the token with Zone:Edit permissions
3. Update the CLOUDFLARE_API_KEY_DNS_ONLY secret in GitHub

### Zone already exists

**Expected behavior**: If the zone already exists, the workflow will detect it and display the
existing zone information. This is normal and not an error.

## Next Steps After Successful Zone Creation

1. **Update Domain Registrar**: Point your domain's name servers to the Cloudflare name servers
   shown in the output
2. **Manage DNS Records**: Use other workflows (e.g., "03. DNS - Manage Record (Manual)") to
   add/update DNS records
3. **Apply Standards**: Use "02. Domain - Enforce Standard (Fix)" to apply standard DNS
   configuration

## Notes

- This workflow uses the `cloudflare-prod` environment which requires approval from authorized users
- Zone creation is an admin-only operation and should be used carefully
- The workflow is idempotent - running it multiple times for the same domain will detect the
  existing zone
- Jump start feature (when enabled) will automatically scan and import existing DNS records from the
  domain's current DNS provider
