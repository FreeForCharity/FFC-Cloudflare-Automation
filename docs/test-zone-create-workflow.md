# Testing: 03. DNS - Add Domain (Create Zone) (Admin) Workflow

This document describes how to test the zone creation workflow end-to-end.

## Prerequisites

Before testing, ensure the following are configured in the `cloudflare-prod` environment:

1. **CLOUDFLARE_API_KEY_DNS_ONLY** - API token with zone creation permissions
2. **CLOUDFLARE_ACCOUNT_ID** - The Cloudflare account ID

## Test Steps

### 1. Navigate to GitHub Actions

1. Go to the repository: https://github.com/FreeForCharity/FFC-Cloudflare-Automation-
2. Click on the "Actions" tab
3. In the left sidebar, find and click "03. DNS - Add Domain (Create Zone) (Admin)"

### 2. Run the Workflow

1. Click the "Run workflow" dropdown button on the right
2. Fill in the inputs:
   - **domain**: Enter a test domain you control (e.g., `test-example.org`)
     - **IMPORTANT**: Use a domain you own or have permission to add to Cloudflare
     - Consider using a throwaway/test domain to avoid affecting production
   - **zone_type**: Select `full` (recommended for testing)
     - `full`: Full DNS management (Cloudflare becomes authoritative nameserver)
     - `partial`: CNAME setup (keeps existing nameservers)
3. Click "Run workflow"

### 3. Monitor the Workflow

1. The workflow run will appear at the top of the runs list
2. Click on the workflow run to see details
3. Click on the "create-zone" job to see step-by-step output

### 4. Verify Success Criteria

The workflow should:

#### ✅ Input Validation

- Validate domain name format
- Check that required secrets are present
- Display domain and zone type (without showing secret values)

#### ✅ Zone Creation or Detection

- Check if the zone already exists
- If it exists: Display existing zone ID, status, and nameservers
- If it doesn't exist: Create the zone and display new zone ID, status, and nameservers

#### ✅ Output Information

Look for the "Display Zone Information" step output showing:

- Domain name
- Zone ID (format: 32-character hexadecimal string)
- Status (typically "pending" or "active")
- Name servers (2 Cloudflare nameservers, e.g., `ns1.cloudflare.com, ns2.cloudflare.com`)

Example output:

```
========================================
ZONE INFORMATION
========================================
Domain: test-example.org
Zone ID: a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6
Status: pending
Name Servers: ns1.cloudflare.com, ns2.cloudflare.com
Action: Zone was created
========================================
```

#### ✅ Security Verification

- **No secrets should appear in logs**
- The API token should be masked in all outputs
- Check that the workflow output does NOT contain:
  - API token values
  - Account ID (it's okay to reference it, but the value should be masked)

### 5. Verify in Cloudflare Dashboard

1. Log in to the Cloudflare dashboard: https://dash.cloudflare.com/
2. Select the account associated with the `CLOUDFLARE_ACCOUNT_ID`
3. Look for the domain in the zones list
4. Verify:
   - The domain appears in the zones list
   - The Zone ID matches the workflow output
   - The nameservers match the workflow output
   - The zone type matches your selection (full/partial)

### 6. Test Edge Cases

#### Test 1: Create a New Zone

- Use a domain that doesn't exist in Cloudflare yet
- Verify the zone is created successfully

#### Test 2: Attempt to Create an Existing Zone

- Run the workflow again with the same domain
- Verify the workflow detects the existing zone
- Verify it returns the existing zone information without errors

#### Test 3: Invalid Domain

- Try with an invalid domain (e.g., `not-a-valid-domain`)
- Verify the workflow fails with a clear error message in the validation step

## Acceptance Criteria

The test is successful if:

- ✅ Workflow completes successfully
- ✅ Zone is created (or detected if already exists)
- ✅ Zone ID and nameservers are displayed in the workflow output
- ✅ Zone appears in the Cloudflare dashboard
- ✅ No secrets are printed in logs
- ✅ Workflow handles both new zones and existing zones gracefully
- ✅ Invalid domains are rejected with clear error messages

## Troubleshooting

### Error: "CLOUDFLARE_API_KEY_DNS_ONLY secret is not set"

- Verify the secret is configured in the `cloudflare-prod` environment
- Check that the environment name matches exactly (case-sensitive)

### Error: "Failed to create zone" with HTTP 403

- The API token lacks zone creation permissions
- Update the token in Cloudflare to include "Zone - Create" permission
- Ensure the token is associated with the correct account

### Error: "Failed to create zone" with HTTP 400

- Check the domain name format
- Verify the domain is a valid, registrable domain
- Some TLDs may not be supported by Cloudflare

### Zone creation succeeds but status is "pending"

- This is normal for newly created zones
- The zone needs DNS delegation (update nameservers at your registrar)
- Once nameservers are updated, the status will change to "active"

## Cleanup

After testing, you may want to:

1. Remove the test zone from Cloudflare (if using a throwaway domain)
   - In Cloudflare dashboard: Zones → [select zone] → Advanced → Remove Site
2. Document the successful test with screenshots
3. Close the test issue if one was created

## Notes

- This workflow uses `CLOUDFLARE_API_KEY_DNS_ONLY` which should have zone creation permissions
- The workflow is safe to run multiple times with the same domain
- Zone creation is idempotent - running it multiple times won't create duplicates
- The workflow validates inputs before making API calls to Cloudflare
