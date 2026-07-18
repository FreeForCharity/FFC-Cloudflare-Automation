# Candid (GuideStar) — MCP connector + REST API workflows

Candid (candid.org — the merged GuideStar + Foundation Center) is FFC's source of truth for
nonprofit verification and transparency data: 501(c)(3) status by EIN, organization profiles, and
the Seals of Transparency shown on FFC-EX template sites. FFC itself maintains a **Platinum** seal
(EIN `46-2471893`, profile linked from the site footer), refreshed annually — see issues
[#490](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/490) /
[#493](https://github.com/FreeForCharity/FFC-Cloudflare-Automation/issues/493).

There are **two integration surfaces**, used for different things:

| Surface                               | For                                                        | Auth                                           |
| ------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------- |
| **Candid MCP server** (interactive)   | Claude sessions: research, profile lookups, sector context | OAuth (Candid account) — per-user, on connect  |
| **Candid REST APIs** (GitHub Actions) | Workflows 801/802: EIN validation, profile/seal lookups    | `Subscription-Key` header, key per API product |

Candid has **no public write API** — the annual Platinum profile update remains a manual web form
(hence the `candid-update.md` paste-sheet planned in #493).

## Candid MCP server (interactive sessions)

The repo-level [`.mcp.json`](../.mcp.json) registers Candid's official remote MCP server:

- **Endpoint:** `https://mcp.candid.org/mcp` (Streamable HTTP)
- **Auth:** standard MCP OAuth with dynamic client registration — on first use the client opens a
  browser login to a **Candid account** (free registration is enough for basic access). In Claude
  Code run `/mcp` to connect/authenticate. In Claude web/desktop, the same server is available as
  the "Candid" connector in the Connectors Directory.
- **Tools:** organization search (name, mission, location, seal level, leadership demographics),
  organization identification (link names to Candid profiles), knowledge search (Candid research,
  training, news), and Philanthropy Classification System taxonomy matching.
- **Coverage:** strongest for US-registered 501(c)(3)s and US foundations.

Use it for onboarding research ("does this applicant have a Candid profile, and what seal level?"),
verifying EIN/name matches conversationally, and sector research. For anything a workflow or audit
depends on, use the REST workflows below so the result is a logged artifact.

> Sandbox note: Claude Code on the web only exposes MCP servers configured as org connectors, so the
> `.mcp.json` entry mainly benefits local/desktop/VS Code sessions cloned from this repo.

## REST API workflows (Actions)

| Workflow                                                                 | API                                                                                                       | Purpose                                                                                                               |
| ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| **801. Candid - Charity Check (EIN)** (`801-candid-charity-check.yml`)   | [Charity Check v1](https://developer.candid.org/reference/charitycheck_v1) (`GET /charitycheck/v1/{ein}`) | Validate an applicant's 501(c)(3) / Pub78 / BMF / OFAC standing before provisioning; periodic partner re-verification |
| **802. Candid - Essentials Search** (`802-candid-essentials-search.yml`) | [Essentials v4](https://developer.candid.org/reference/essentials_v4) (`POST /essentials/v4`)             | Find an org's Candid profile, EIN, location, and transparency-seal level by name/EIN/keywords                         |

Both are **read-only**, run on `windows-latest` in environment **`candid-prod-read`** (no approval
gate needed — mirrors `google-prod-read` / `zeffy-prod`), print results to the job log + step
summary, and upload the raw JSON response as a 30-day artifact.

Dispatch (from a `gh`-authed environment):

```bash
gh workflow run 801-candid-charity-check.yml --ref main -f ein=46-2471893
gh workflow run 802-candid-essentials-search.yml --ref main -f search_terms='Free For Charity'
```

### Architecture (mirrors WHMCS / Google)

```
GitHub runner ──OIDC──► Azure (ffc-admin-kv-reader) ──► Key Vault (Candid subscription keys)
runner ──GET/POST + Subscription-Key──► https://api.candid.org (Charity Check / Essentials)
```

- Composite action **`.github/actions/candid-keys-from-kv`** fetches the key(s) via OIDC, masks them
  line-by-line, and exports `CANDID_CHARITY_CHECK_KEY` / `CANDID_ESSENTIALS_KEY` to `GITHUB_ENV`
  (heredoc-delimited). Key Vault is the single source of truth — **never** copy a Candid key into a
  GitHub secret.
- Scripts: `scripts/candid-api-common.ps1` (shared `Invoke-CandidApi` with an `api.candid.org` host
  allowlist and 429/5xx retry), `scripts/candid-charity-check.ps1`,
  `scripts/candid-essentials-search.ps1`. All accept `-ApiKey` for local use.
- Candid issues a **separate subscription key per API product** (each with primary/secondary
  copies); rotate by writing a new version of the KV secret — no GitHub change needed.

### One-time setup (not yet provisioned)

The scaffolding is complete but inert until these exist:

1. **Candid keys** — sign up at [developer.candid.org](https://developer.candid.org) (FFC accounts
   team) and subscribe to **Charity Check** and **Essentials**. Each product yields its own
   subscription key.
2. **Key Vault secrets** in `kv-ffc-admin-prod-cbm` (scoped-name convention; `read-all-*` and
   `wr-all-*` hold identical values, matching WHMCS):
   - `read-all-ffc-candid-charity-check-key` (+ `wr-all-…` copy)
   - `read-all-ffc-candid-essentials-key` (+ `wr-all-…` copy)
3. **GitHub environment `candid-prod-read`** with the two Azure OIDC identifier secrets already used
   by `google-prod-read`: `READ_ALL_FFC_AZURE_KV_CLIENT_ID`, `READ_ALL_FFC_AZURE_TENANT_ID`. No
   reviewer gate needed (read-only API, key has no write power).
4. **Federated credential** on the KV-reader identity (`ffc-admin-kv-reader`): subject
   `repo:FreeForCharity/FFC-Cloudflare-Automation:environment:candid-prod-read`.

Until then, a dispatch fails fast at the "Validate required Azure secrets" step (missing env
secrets) or in `candid-keys-from-kv` (missing KV secret / placeholder value) with an actionable
message.

## How this ties into existing FFC flows

- **Charity onboarding** (`204. WHMCS - Charity Onboard`, `701. Website - Provision`): the intake
  forms already collect EIN and GuideStar URLs (`whmcs-onboarding-products.json` custom fields
  12/103/104, website-request issue template). Run **801** on the applicant's EIN before
  provisioning, and **802** to find/confirm the profile links that `Apply-WebsiteReactTemplate.ps1`
  stamps into FFC-EX sites.
- **Annual Candid Platinum update** (#490/#493): the `candid-update.md` paste-sheet generator can
  use **802** to confirm FFC's current profile/seal state alongside the derived impact figures. (The
  update itself stays manual — no write API.)
