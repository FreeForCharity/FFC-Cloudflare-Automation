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

// GitHub's closing keywords, plus bare `ref`/`refs` (which do NOT auto-close an
// issue but still signal a claim per the protocol). Matches same-repo bare
// references — "Closes #12", "Fixes: #3", "refs #45" — case-insensitively.
// `[:\s]+` allows an optional colon and/or whitespace but requires a separator,
// so "prefix" / "closes#3" (no separator) never false-match.
const LINK_RE = /\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?)\b[:\s]+#(\d+)/gi;
const CLOSING_RE = /\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b[:\s]+#(\d+)/gi;

function _collect(body, re) {
  const out = [];
  if (!body) return out;
  re.lastIndex = 0;
  let m;
  while ((m = re.exec(body)) !== null) {
    const n = Number(m[2]);
    if (Number.isInteger(n) && n > 0 && !out.includes(n)) out.push(n);
  }
  return out;
}

// Returns { closing, refs, all } — de-duplicated issue numbers referenced by a
// PR body. `closing` are auto-closing keywords; `refs` are the ref/refs-only
// references; `all` is the union (any reference counts as a claim).
function extractLinkedIssues(body) {
  const all = _collect(body, LINK_RE);
  const closing = _collect(body, CLOSING_RE);
  const refs = all.filter((n) => !closing.includes(n));
  return { closing, refs, all };
}

// A claim is released only when there is NO open linked PR AND the issue has had
// no activity for >= thresholdMs. An open linked PR always keeps the claim.
function decideRelease({ hasOpenLinkedPR, lastActivityMs, nowMs, thresholdMs = EXPIRY_MS }) {
  if (hasOpenLinkedPR) return false;
  if (!Number.isFinite(lastActivityMs) || !Number.isFinite(nowMs)) return false;
  return nowMs - lastActivityMs >= thresholdMs;
}

module.exports = {
  CLAIM_LABEL,
  EXPIRY_MS,
  LINK_RE,
  CLOSING_RE,
  extractLinkedIssues,
  decideRelease,
};
