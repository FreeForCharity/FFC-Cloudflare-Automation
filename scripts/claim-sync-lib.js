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
  LINK_RE,
  CLOSING_RE,
  normalizeRepo,
  extractLinkedIssues,
  linkedClaimComment,
  hasLinkedClaimMarker,
  decideRelease,
};
