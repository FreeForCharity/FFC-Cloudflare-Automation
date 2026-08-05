'use strict';

// Pure classification + report logic for workflow 321 (Azure - KV Credential
// Liveness + Expiry Monitor). The workflow's github-script step `require`s this
// module, so testing these functions
// (tests/workflow-logic/test_321_kv_credential_liveness.py) tests the exact
// logic that ships. I/O — listing the vault, reading the probed secrets,
// performing the HTTP probes, opening/closing the rolling issue — stays in the
// workflow; only the decisions and the issue body live here.
//
// Background (#848, finding 1): both `read-all-*` GitHub PATs in
// kv-ffc-admin-prod-cbm were dead (401 Bad credentials) and had been for an
// unknown period. Nothing consumed them, so nothing noticed — and #837 was
// about to put an ungated lane on one of them. Expiry checking alone would not
// have caught it: a REVOKED token has a future `expires`. Hence two independent
// signals here, and the rule that neither may ever read as green when it could
// not be evaluated.
//
// Two deliberate scope limits:
//
//   1. **Liveness probes cover `read-all-*` secrets only.** Expiry/enabled
//      state comes from `az keyvault secret list`, which returns attributes
//      WITHOUT the values, so write-scope credentials are monitored for expiry
//      without this workflow ever reading their material. `assertNoWriteScopeProbes`
//      makes that structural. It also keeps the monitor compatible with the
//      per-secret RBAC migration (#848 part A): the reader identity needs
//      `getSecret` on the four probed secrets and nothing else.
//   2. **A probe exists only where one secret can verify itself, read-only.**
//      GitHub PATs (`GET /user`) and Cloudflare tokens (`GET /zones`)
//      qualify. WHMCS needs three secrets and a POST; a Google SA key needs a
//      JWT exchange. Those stay expiry-monitored only — inventing a probe that
//      mutates or that needs a credential trio would trade the safety that
//      makes this workflow ungated.

const VAULT = 'kv-ffc-admin-prod-cbm';
const EXPIRY_WARN_DAYS = 30;
const MARKER = '<!-- kv-credential-liveness -->';
const ISSUE_TITLE = 'Key Vault credential health: dead, expiring, or unverifiable credentials';
const ISSUE_LABELS = ['security', 'priority: high', 'agentic-os'];

// Probe definitions. The URL is hardcoded PER KIND and never derived from vault
// data — a credential must only ever be sent to a host named in this file (and
// the workflow re-validates the host against its own allowlist before sending).
//
// `deadStatuses` are the statuses that authoritatively mean "this credential was
// rejected". Everything else that is not a success is UNVERIFIED, never live:
// a 429 or a 502 says nothing about the credential.
const PROBE_KINDS = {
  'github-pat': {
    url: 'https://api.github.com/user',
    deadStatuses: [401, 403],
    // Fine-grained PATs return their expiry on this header, which is the
    // provider's own answer rather than whatever `expires` the vault carries.
    expiryHeader: 'github-authentication-token-expiration',
    label: 'GitHub PAT',
  },
  'cloudflare-token': {
    // NOT `/user/tokens/verify`. That endpoint only answers for USER-owned
    // tokens; FFC's are account-scoped, and it rejects them with a 4xx even
    // when they are fully working — which is exactly what happened on 321's
    // first run (#877): both Cloudflare tokens were reported dead hours after
    // workflow 108 had used them to export every zone. `/zones` is the call
    // the read lane actually makes (`Export-CloudflareDns.ps1`), so a probe
    // failure here means the lane is broken, and a probe pass means it works.
    // `scripts/cloudflare-registrar-access-check.ps1` already avoided the
    // verify endpoint for this reason and said so in a comment.
    url: 'https://api.cloudflare.com/client/v4/zones?per_page=1',
    // Cloudflare answers an invalid token with 400 (code 1000), not 401.
    // 403 is deliberately NOT here: on a resource endpoint it means
    // authenticated-but-unauthorized (code 9109), which is a real problem but
    // not a rejected credential — it lands in `unverified`, which still raises
    // a finding without asserting something the probe did not observe.
    deadStatuses: [400, 401],
    // A 200 alone is not enough: Cloudflare wraps every response in an
    // envelope, so read `success` and never infer health from the status line.
    // (Replaces a `result.status == active` check that only `/user/tokens/verify`
    // returns.)
    requireSuccessBody: true,
    label: 'Cloudflare API token',
  },
};

// The monitored probe targets. `read-all-*` only, by design (see header).
const PROBES = [
  { secret: 'read-all-cbm-github-pat', kind: 'github-pat' },
  { secret: 'read-all-cbm-ffc-copilot-mcp-github-pat', kind: 'github-pat' },
  { secret: 'read-all-ffc-cloudflare-api-token-zone-and-dns', kind: 'cloudflare-token' },
  { secret: 'read-all-cm-cloudflare-api-token-zone-and-dns', kind: 'cloudflare-token' },
];

/**
 * Structural guard: no probe target may name a write-scope secret.
 *
 * Called by the workflow before any secret is read, so an edit that puts
 * `wr-all-cbm-github-pat` (repo create/archive/collaborator scope, 100+ repos)
 * on this read lane fails the run instead of quietly widening what an ungated
 * scheduled workflow handles.
 *
 * @param {Array<{secret:string, kind:string}>} probes
 * @returns {Array<{secret:string, kind:string}>} the same list, when valid
 */
function assertNoWriteScopeProbes(probes) {
  const offenders = (probes || []).filter((p) => !/^read-all-/.test(String(p.secret)));
  if (offenders.length) {
    throw new Error(
      `321 probes must name read-all-* secrets only; refusing: ${offenders
        .map((p) => p.secret)
        .join(', ')}`,
    );
  }
  const unknown = (probes || []).filter((p) => !PROBE_KINDS[p.kind]);
  if (unknown.length) {
    throw new Error(`unknown probe kind(s): ${unknown.map((p) => p.kind).join(', ')}`);
  }
  return probes;
}

/**
 * Classify one probe result.
 *
 * @param {{secret:string, kind:string, httpStatus?:number|null,
 *          bodySuccess?:boolean|null, error?:string|null}} result
 * @returns {'LIVE'|'DEAD'|'UNVERIFIED'}
 *
 * DEAD       — the provider rejected the credential (see `deadStatuses`), or a
 *              Cloudflare 200 whose envelope says `success: false`.
 * LIVE       — 200 (plus `success: true` for Cloudflare).
 * UNVERIFIED — anything else: transport error, unreadable secret, 5xx, 429, or
 *              a status this kind does not classify. NEVER treated as live —
 *              "could not check" and "checked, fine" are different answers, and
 *              collapsing them is how a dead credential hides (#726/#855).
 */
function classifyProbe(result) {
  const kind = PROBE_KINDS[result && result.kind];
  if (!kind) return 'UNVERIFIED';
  if (result.error) return 'UNVERIFIED';
  const status = Number(result.httpStatus);
  if (!Number.isFinite(status) || status === 0) return 'UNVERIFIED';
  if (kind.deadStatuses.includes(status)) return 'DEAD';
  if (status !== 200) return 'UNVERIFIED';
  if (kind.requireSuccessBody) {
    const ok = result.bodySuccess;
    if (ok === true) return 'LIVE';
    // 200 with an unreadable envelope is unverifiable, not a rejection.
    return ok === false ? 'DEAD' : 'UNVERIFIED';
  }
  return 'LIVE';
}

function _days(fromIso, nowMs) {
  const t = Date.parse(fromIso);
  if (!Number.isFinite(t)) return null;
  return Math.floor((t - nowMs) / 86400000);
}

/**
 * Fold the vault listing and the probe results into one verdict.
 *
 * @param {{secrets?:Array<{name:string, enabled?:boolean, expires?:string|null,
 *          updated?:string|null}>,
 *          probes?:Array<object>, listError?:string|null, now?:string}} input
 *
 * Buckets. Findings: dead, expired, expiring, disabled, missing, unverified.
 * NOT findings, reported for context only: `live` (probe verified), `healthy`
 * (expiry beyond the warning window), `noExpiry` (no expiry date set — common
 * and not a defect). Keep this list in step with `hasFinding` below; a bucket
 * described as a finding that does not raise one is how a responder learns to
 * distrust the report.
 *   - dead:      a probe the provider rejected. The #848 finding-1 class.
 *   - expired:   `expires` already past. Runtime failure, guaranteed.
 *   - expiring:  `expires` inside EXPIRY_WARN_DAYS. Both working GitHub PATs
 *                share 2027-02-21, so they will land here on the same day —
 *                which is the point of reporting days, not just a boolean.
 *   - disabled:  `enabled: false`. Reads fail at runtime with a 403-shaped
 *                error that looks like an RBAC problem, so name it explicitly.
 *   - missing:   a probe target absent from the vault listing — a typo in this
 *                file or a secret retired without updating it. Reported rather
 *                than skipped, because a silently skipped probe is a monitor
 *                that reports green while checking nothing.
 *   - unverified: a probe that could not be evaluated.
 *
 * A failure to list the vault at all (`listError`) is itself a finding: with no
 * listing there is no evidence of health, and the workflow must not exit 0
 * looking clean.
 */
function analyze(input) {
  const now = input && input.now ? Date.parse(input.now) : Date.now();
  const secrets = (input && input.secrets) || [];
  const probeResults = (input && input.probes) || [];
  const listError = (input && input.listError) || null;

  const byName = new Map();
  for (const s of secrets) {
    if (s && s.name) byName.set(s.name, s);
  }

  const expired = [];
  const expiring = [];
  const disabled = [];
  const noExpiry = [];
  const healthy = [];
  for (const s of secrets) {
    if (!s || !s.name) continue;
    const days = s.expires ? _days(s.expires, now) : null;
    const row = { name: s.name, expires: s.expires || null, daysToExpiry: days };
    if (s.enabled === false) {
      disabled.push(row);
      continue;
    }
    if (days === null) {
      noExpiry.push(row);
    } else if (days < 0) {
      expired.push(row);
    } else if (days <= EXPIRY_WARN_DAYS) {
      expiring.push(row);
    } else {
      healthy.push(row);
    }
  }

  const dead = [];
  const unverified = [];
  const missing = [];
  const live = [];
  // Absence is only knowable from a listing that SUCCEEDED. With no listing,
  // every probe target would read as `missing` — a fabricated finding that
  // would hide the real one (the listing failure) behind four false ones.
  //
  // A successful listing that comes back EMPTY is the opposite case and turns
  // on the same switch: nothing is there, so every target is genuinely absent
  // and `missing` is the accurate report. That is also what the workflow does —
  // it skips the read for any target not in the listing — so gating this on a
  // non-empty list would make the library contradict the step feeding it, and
  // downgrade a real "the vault has nothing in it" (wrong vault name, an RBAC
  // state that lists nothing) to a vague UNVERIFIED.
  const canCheckPresence = !listError;
  for (const p of probeResults) {
    const target = { secret: p.secret, kind: p.kind };
    if (canCheckPresence && !byName.has(p.secret)) {
      missing.push(target);
      continue;
    }
    const verdict = classifyProbe(p);
    const row = {
      ...target,
      httpStatus: Number.isFinite(Number(p.httpStatus)) ? Number(p.httpStatus) : null,
      detail: p.error ? String(p.error) : p.bodySuccess === false ? 'success=false' : '',
      providerExpiry: p.providerExpiry || null,
    };
    if (verdict === 'DEAD') dead.push(row);
    else if (verdict === 'LIVE') live.push(row);
    else unverified.push(row);
  }

  const sortByName = (a, b) => String(a.name || a.secret).localeCompare(String(b.name || b.secret));
  [expired, expiring, disabled, noExpiry, healthy, dead, unverified, missing, live].forEach((a) =>
    a.sort(sortByName),
  );

  return {
    vault: VAULT,
    listError,
    totalSecrets: secrets.length,
    expired,
    expiring,
    disabled,
    noExpiry,
    healthy,
    dead,
    unverified,
    missing,
    live,
    hasFinding: Boolean(
      listError ||
      expired.length ||
      expiring.length ||
      disabled.length ||
      dead.length ||
      unverified.length ||
      missing.length,
    ),
  };
}

/**
 * One-line summary for `core.notice` and the recovery comment.
 * @param {object} a analysis from analyze()
 */
function summary(a) {
  return (
    `secrets=${a.totalSecrets} probed=${a.dead.length + a.live.length + a.unverified.length} ` +
    `live=${a.live.length} dead=${a.dead.length} unverified=${a.unverified.length} ` +
    `missing=${a.missing.length} expired=${a.expired.length} expiring=${a.expiring.length} ` +
    `disabled=${a.disabled.length}`
  );
}

function _table(header, rows) {
  return [`| ${header.join(' | ')} |`, `| ${header.map(() => '---').join(' | ')} |`, ...rows];
}

/**
 * Render the rolling issue body. Values never appear — only names, statuses,
 * dates and day counts, all derived from data this module received without ever
 * seeing a secret value.
 *
 * @param {object} a analysis from analyze()
 * @param {string} iso generation timestamp
 */
function renderBody(a, iso) {
  const lines = [MARKER, ''];
  lines.push(
    `Health of the credentials in \`${a.vault}\` that back the automation lanes. ` +
      'Two independent signals: the vault’s own expiry/enabled attributes for every secret, ' +
      'and a read-only liveness probe for the credentials that can verify themselves.',
    '',
    `- Generated: ${iso}`,
    `- Secrets in vault: ${a.totalSecrets} · probed: ${a.dead.length + a.live.length + a.unverified.length} of ${PROBES.length} registered`,
    `- Expiry warning threshold: ${EXPIRY_WARN_DAYS} days`,
    '',
    'This rolling issue auto-closes on the next run where nothing is dead, expired, expiring, ' +
      'disabled, missing, or unverifiable, and the vault could be listed. Anything less than all ' +
      'of that keeps it open — the close condition is exactly the finding set below.',
    '',
  );

  if (a.listError) {
    lines.push(
      '## ⛔ The vault could not be listed',
      '',
      `\`${a.listError}\``,
      '',
      'No health can be asserted from this run — treat the sections below as incomplete, not clean.',
      '',
    );
  }

  if (a.dead.length) {
    lines.push(
      `## 💀 Dead — the provider rejected the credential (${a.dead.length})`,
      '',
      'A dead credential fails every consumer at run time. Reminting is a human action (#848) — no agent handles token material.',
      '',
      ..._table(
        ['Secret', 'Kind', 'HTTP', 'Detail'],
        a.dead.map(
          (d) =>
            `| \`${d.secret}\` | ${(PROBE_KINDS[d.kind] || {}).label || d.kind} | ${d.httpStatus === null ? '—' : d.httpStatus} | ${d.detail || 'rejected'} |`,
        ),
      ),
      '',
    );
  }

  if (a.expired.length) {
    lines.push(
      `## ⛔ Expired (${a.expired.length})`,
      '',
      ..._table(
        ['Secret', 'Expired', 'Days ago'],
        a.expired.map((s) => `| \`${s.name}\` | ${s.expires} | ${Math.abs(s.daysToExpiry)} |`),
      ),
      '',
    );
  }

  if (a.expiring.length) {
    lines.push(
      `## ⚠️ Expiring within ${EXPIRY_WARN_DAYS} days (${a.expiring.length})`,
      '',
      'Rotation = a **new KV secret version**, no GitHub change. Runbooks are linked from the quarterly reminders opened by 733.',
      '',
      ..._table(
        ['Secret', 'Expires', 'Days left'],
        a.expiring.map((s) => `| \`${s.name}\` | ${s.expires} | ${s.daysToExpiry} |`),
      ),
      '',
    );
  }

  if (a.disabled.length) {
    lines.push(
      `## 🚫 Disabled in the vault (${a.disabled.length})`,
      '',
      'A disabled secret fails the fetch with a permissions-shaped error, which reads like an RBAC problem rather than a disabled secret.',
      '',
      ..._table(
        ['Secret'],
        a.disabled.map((s) => `| \`${s.name}\` |`),
      ),
      '',
    );
  }

  if (a.unverified.length) {
    lines.push(
      `## ❓ Unverifiable — the probe could not be evaluated (${a.unverified.length})`,
      '',
      'Not evidence of a dead credential, and **not** evidence of a healthy one. Common causes: the reader identity cannot read the secret (per-secret RBAC, #848 part A), a transport failure, or provider 5xx/429.',
      '',
      ..._table(
        ['Secret', 'Kind', 'HTTP', 'Detail'],
        a.unverified.map(
          (d) =>
            `| \`${d.secret}\` | ${(PROBE_KINDS[d.kind] || {}).label || d.kind} | ${d.httpStatus === null ? '—' : d.httpStatus} | ${d.detail || 'no response'} |`,
        ),
      ),
      '',
    );
  }

  if (a.missing.length) {
    lines.push(
      `## 🔍 Registered for probing but absent from the vault (${a.missing.length})`,
      '',
      'Either the name in `scripts/kv-credential-liveness-lib.js` is wrong, or the secret was retired without updating the registry. Until it is resolved, this credential is not being checked at all.',
      '',
      ..._table(
        ['Secret', 'Kind'],
        a.missing.map(
          (d) => `| \`${d.secret}\` | ${(PROBE_KINDS[d.kind] || {}).label || d.kind} |`,
        ),
      ),
      '',
    );
  }

  if (a.live.length) {
    lines.push(
      `## ✅ Verified live (${a.live.length})`,
      '',
      ..._table(
        ['Secret', 'Kind', 'Provider expiry'],
        a.live.map(
          (d) =>
            `| \`${d.secret}\` | ${(PROBE_KINDS[d.kind] || {}).label || d.kind} | ${d.providerExpiry || '—'} |`,
        ),
      ),
      '',
    );
  }

  lines.push(
    '---',
    '',
    `_Managed by 321. Azure - KV Credential Liveness + Expiry Monitor. Refs #848. Probes read \`read-all-*\` secrets only — write-scope credentials are monitored for expiry without ever being read._`,
  );
  return lines.join('\n');
}

/**
 * Find this monitor's rolling issue in an open-issue listing.
 *
 * `issues.listForRepo` returns **pull requests as well as issues** — the same
 * object in the REST model, told apart only by a `pull_request` key — and the
 * clean-run path CLOSES whatever this returns. Without the filter a scheduled
 * monitor can close somebody's open pull request with a comment claiming a
 * recovery.
 *
 * The marker is an HTML comment, i.e. invisible in a rendered PR body, so a PR
 * can carry it without its author ever seeing it (a PR quoting this constant, a
 * lessons entry describing the pattern, a pasted issue body used as evidence).
 *
 * 739 already carried the rule — "Issues API returns PRs too; drop them
 * everywhere" — and it reached the workflows that enumerate issues to *report*,
 * never the ones that enumerate them to *close one*. Refs #980.
 *
 * @param {Array<{body?:string, pull_request?:object}>} items an open-issue listing
 * @returns {object|null} the rolling issue, or null when there is none
 */
function findRollingIssue(items) {
  return (
    (items || []).find(
      (i) => i && !i.pull_request && typeof i.body === 'string' && i.body.includes(MARKER),
    ) || null
  );
}

module.exports = {
  VAULT,
  EXPIRY_WARN_DAYS,
  MARKER,
  findRollingIssue,
  ISSUE_TITLE,
  ISSUE_LABELS,
  PROBE_KINDS,
  PROBES,
  assertNoWriteScopeProbes,
  classifyProbe,
  analyze,
  summary,
  renderBody,
};
