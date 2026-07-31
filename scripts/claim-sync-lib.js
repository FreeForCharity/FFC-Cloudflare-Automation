'use strict';

// Pure decision helpers for the 737 Claim Sync workflow.
//
// Kept as a standalone CommonJS module (not inline in the workflow YAML) so the
// link-extraction and claim-expiry logic can be unit-tested directly with node
// (tests/workflow-logic/test_737_claim_sync.py) — and so the workflow's
// github-script steps `require` the exact shipped code, which can never drift
// from what the tests exercise.

const CLAIM_LABEL = 'claimed';

// A hand-labeled claim with no open linked PR is released after this much
// inactivity. Mirrors the 48h expiry in the AGENTS.md work-claiming protocol.
const EXPIRY_MS = 48 * 60 * 60 * 1000;

// Marker stamped into the comment the org-wide sweep posts when it claims a hub
// issue on behalf of a PR in another repo. It is the claim's provenance: the
// sweep gets no `closed` event for a cross-repo PR, so without it the sweep
// cannot tell a finished cross-repo claim (release now) from a hand-written
// CLAIM comment (hold for the full 48h expiry).
const LINKED_CLAIM_MARKER = '<!-- claim-sync:linked-pr -->';

// Markers stamped into the notice posted on the PR *itself* when it claims an
// issue. `Refs #N` means two incompatible things — "this PR does part of that
// issue" (a claim) and "that issue is where the related work lives" (a
// citation) — and nothing here can tell them apart. 737 already comments on the
// issue; the PR author never sees that, so a citation written as a claim hides
// a backlog pickup silently, for as long as the PR stays open (#948: a docs
// draft hid the run's designated top pickup for six hours).
//
// The per-issue marker, not the generic one, is what makes the notice
// idempotent: a PR that later claims a SECOND issue must still get told about
// that one, so "have I already said this?" is a question about an issue number,
// not about the comment.
const PR_NOTICE_MARKER = '<!-- claim-sync:pr-notice -->';
const prNoticeIssueMarker = (n) => `<!-- claim-sync:pr-notice:${n} -->`;
const PR_NOTICE_ISSUE_RE = /<!--\s*claim-sync:pr-notice:(\d+)\s*-->/g;

// GitHub's closing keywords, plus bare `ref`/`refs` (which do NOT auto-close an
// issue but still signal a claim per the protocol). `[:\s]+` allows an optional
// colon and/or whitespace but requires a separator, so "prefix" / "closes#3"
// (no separator) never false-match.
//
// The `owner/repo` before `#N` is OPTIONAL and captured: both the bare
// same-repo form ("Closes #12") and the qualified cross-repo form
// ("Refs FreeForCharity/FFC-Cloudflare-Automation#934") are recognized, and
// extraction is keyed on which repo the reference resolves to. Matching the
// qualified form is not cosmetic — it is the only form that can name a hub
// issue from another repo, and it is what three finished PRs were using while
// their hub issues sat in the pickup query looking unclaimed.
const KEYWORDS = '(close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)';
const CLOSING_KEYWORDS = '(close[sd]?|fix(?:e[sd])?|resolve[sd]?)';
// A login cannot start with a separator char; repo names allow [A-Za-z0-9._-].
// Neither half admits a `/`, so a match always has exactly one.
const NWO = '[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+';
const linkPattern = (keywords) => new RegExp(`\\b${keywords}\\b[:\\s]+(${NWO})?#(\\d+)`, 'gi');

const LINK_RE = linkPattern(KEYWORDS);
const CLOSING_RE = linkPattern(CLOSING_KEYWORDS);

// `owner/repo` compared case-insensitively (GitHub names are), or null when the
// value is absent/unparseable.
function normalizeRepo(nwo) {
  if (typeof nwo !== 'string') return null;
  const t = nwo.trim();
  return /^[^/\s]+\/[^/\s]+$/.test(t) ? t.toLowerCase() : null;
}

function _collect(body, re, sourceRepo, targetRepo) {
  const out = [];
  if (!body) return out;
  re.lastIndex = 0;
  let m;
  while ((m = re.exec(body)) !== null) {
    const n = Number(m[3]);
    if (!Number.isInteger(n) || n <= 0) continue;
    const qualified = normalizeRepo(m[2]);
    if (targetRepo) {
      // A bare `#N` means an issue in the repo the PR itself lives in — so it
      // counts for the target only when the PR IS in the target repo. With no
      // sourceRepo supplied, a bare reference resolves to nothing and is
      // dropped rather than guessed.
      if ((qualified || sourceRepo) !== targetRepo) continue;
    } else if (qualified) {
      // Unscoped (legacy) call: a qualified reference names a repo the caller
      // did not ask about, so it is never this repo's claim.
      continue;
    }
    if (!out.includes(n)) out.push(n);
  }
  return out;
}

// Returns { closing, refs, all } — de-duplicated issue numbers referenced by a
// PR body. `closing` are auto-closing keywords; `refs` are the ref/refs-only
// references; `all` is the union (any reference counts as a claim).
//
// Repo scoping via opts:
//   {}                              -> bare `#N` only (same-repo, legacy shape)
//   { sourceRepo, targetRepo }      -> references RESOLVING to targetRepo, where
//                                      a bare `#N` resolves to sourceRepo
// An unparseable targetRepo matches nothing: a scoped call that cannot say which
// repo it means must not fall back to labeling on bare references.
function extractLinkedIssues(body, opts = {}) {
  const scoped = opts.targetRepo !== undefined && opts.targetRepo !== null;
  const targetRepo = scoped ? normalizeRepo(opts.targetRepo) : null;
  if (scoped && !targetRepo) return { closing: [], refs: [], all: [] };
  const sourceRepo = normalizeRepo(opts.sourceRepo);
  const all = _collect(body, LINK_RE, sourceRepo, targetRepo);
  const closing = _collect(body, CLOSING_RE, sourceRepo, targetRepo);
  const refs = all.filter((n) => !closing.includes(n));
  return { closing, refs, all };
}

// The comment the sweep posts when it claims a hub issue for a cross-repo PR.
// Carries LINKED_CLAIM_MARKER so a later sweep can recognize the claim as
// PR-derived (see decideRelease's claimedByLinkedPR).
function linkedClaimComment(prRef) {
  return (
    `🔒 Claimed — open PR ${prRef} references this issue. ` +
    'Released automatically once no open PR in any FreeForCharity repo references it ' +
    `(\`is:open -label:${CLAIM_LABEL}\`).\n\n${LINKED_CLAIM_MARKER}`
  );
}

function hasLinkedClaimMarker(comments) {
  return (comments || []).some(
    (c) => c && typeof c.body === 'string' && c.body.includes(LINKED_CLAIM_MARKER),
  );
}

// The notice posted ON THE PR naming what it just removed from the pickup
// query, and the escape hatch for an author who meant a citation. Numbers are
// sorted and de-duplicated so the body is a function of the SET, not of the
// order the claim loop happened to visit it in.
function prNoticeComment(issueNumbers) {
  const ns = [
    ...new Set((issueNumbers || []).map(Number).filter((n) => Number.isInteger(n) && n > 0)),
  ].sort((a, b) => a - b);
  const many = ns.length > 1;
  return (
    `🔒 This PR claimed ${ns.map((n) => `#${n}`).join(', ')} — ` +
    `${many ? 'those issues are' : 'that issue is'} now hidden from the agent pickup query ` +
    `(\`is:open -label:${CLAIM_LABEL}\`) until this PR merges or closes.\n\n` +
    `**If you only meant to _cite_ ${many ? 'them' : 'it'}, not to take the work**, ` +
    `edit the body to reference ${many ? 'them' : 'it'} with a full link ` +
    '(`https://github.com/OWNER/REPO/issues/N`) instead of `Refs`/`Closes`, and remove the ' +
    `\`${CLAIM_LABEL}\` label. \`Refs #N\` is the claiming form — see AGENTS.md ` +
    '§ "Work claiming".\n\n' +
    PR_NOTICE_MARKER +
    ns.map(prNoticeIssueMarker).join('')
  );
}

// Issue numbers this PR has already been told about, read back from its own
// comments. Anything already in here is not re-announced.
function noticedIssues(comments) {
  const out = [];
  for (const c of comments || []) {
    if (!c || typeof c.body !== 'string') continue;
    PR_NOTICE_ISSUE_RE.lastIndex = 0;
    let m;
    while ((m = PR_NOTICE_ISSUE_RE.exec(c.body)) !== null) {
      const n = Number(m[1]);
      if (Number.isInteger(n) && n > 0 && !out.includes(n)) out.push(n);
    }
  }
  return out;
}

const HOUR_MS = 60 * 60 * 1000;

// Whole hours, then days — a claim age is read to answer "is anyone actually
// working on this?", and minutes never change that answer.
function formatAge(ms) {
  if (!Number.isFinite(ms) || ms < 0) return 'unknown age';
  const hours = Math.floor(ms / HOUR_MS);
  const days = Math.floor(hours / 24);
  return days >= 1 ? `${days}d ${hours - days * 24}h` : `${hours}h`;
}

// A claim whose ONLY claimants are drafts older than thresholdMs — reportable,
// never releasable. A draft is not active work, but it suppresses a pickup
// exactly as hard as a ready PR, and several of the drafts measured in #948 had
// been sitting four to six days. Returns null (nothing to report) unless EVERY
// claimant is a draft AND every one of them is past the threshold:
//   - any ready PR  -> real work in review, not a stuck claim;
//   - any young draft -> plausibly still being written;
//   - an unknown draft flag or unparseable age -> unknown, and this reports
//     rather than releases, so a false alarm is the only failure it can cause.
// `rows`: [{ ref, draft, createdAtMs }].
function draftOnlyClaim(rows, { nowMs, thresholdMs = EXPIRY_MS } = {}) {
  const refs = Array.isArray(rows) ? rows : [];
  if (!refs.length || !Number.isFinite(nowMs)) return null;
  let oldestAgeMs = -Infinity;
  for (const r of refs) {
    if (!r || r.draft !== true) return null;
    const age = nowMs - Number(r.createdAtMs);
    if (!Number.isFinite(age) || age < thresholdMs) return null;
    if (age > oldestAgeMs) oldestAgeMs = age;
  }
  return { oldestAgeMs, refs: refs.map((r) => r.ref) };
}

// A claim is released when there is no open linked PR AND either
//   - the claim came from a linked PR that is no longer open (claimedByLinkedPR:
//     the work is finished, so holding the label just hides available work), or
//   - the issue has been idle for >= thresholdMs (the hand-claim backstop).
// An open linked PR always keeps the claim. claimedByLinkedPR defaults to false
// so a hand-written CLAIM comment still gets its full 48h — releasing those on
// sight would let two agents collide on the same issue within the hour.
function decideRelease({
  hasOpenLinkedPR,
  claimedByLinkedPR = false,
  lastActivityMs,
  nowMs,
  thresholdMs = EXPIRY_MS,
}) {
  if (hasOpenLinkedPR) return false;
  if (claimedByLinkedPR) return true;
  if (!Number.isFinite(lastActivityMs) || !Number.isFinite(nowMs)) return false;
  return nowMs - lastActivityMs >= thresholdMs;
}

module.exports = {
  CLAIM_LABEL,
  EXPIRY_MS,
  LINKED_CLAIM_MARKER,
  PR_NOTICE_MARKER,
  LINK_RE,
  CLOSING_RE,
  normalizeRepo,
  extractLinkedIssues,
  linkedClaimComment,
  hasLinkedClaimMarker,
  prNoticeIssueMarker,
  prNoticeComment,
  noticedIssues,
  formatAge,
  draftOnlyClaim,
  decideRelease,
};
