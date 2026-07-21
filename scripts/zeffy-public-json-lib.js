'use strict';

// Pure transform helpers for the 401 Zeffy campaigns "public JSON" delivery.
//
// Kept as a standalone, dependency-free CommonJS module (not inline in the
// workflow YAML, and no csv-parse dependency) so the CSV -> public-JSON logic
// can be unit-tested directly with plain `node`
// (tests/workflow-logic/test_401_zeffy_public_json.py) and so the workflow
// step runs the exact shipped code, which can never drift from what the tests
// exercise.
//
// Input:  the CSV produced by scripts/zeffy-campaigns-export.ps1 (columns
//         include id/type/category/status/title/url plus financial + date
//         fields such as target_cents, volume, created, ...).
// Output: an array of { title, url, status } — a strict allowlist. The
//         financial and date columns are intentionally dropped: the published
//         file is a public list of Zeffy campaign *surfaces* (used by the smoke
//         engine to classify a detected donation URL as ffc-interim vs
//         charity-specific), never a fundraising-figures export.

// The ONLY fields copied into the published file. Enforced as an allowlist so a
// future column added to the export (e.g. a new financial or contact field)
// can never silently leak into the public JSON.
const PUBLIC_FIELDS = ['title', 'url', 'status'];

// Parse RFC 4180 CSV text into an array of row objects keyed by the header row.
// Handles quoted fields with embedded commas / newlines, doubled "" escapes,
// and both LF and CRLF line endings (PowerShell Export-Csv emits CRLF). A
// trailing newline produces no empty row.
function parseCsv(text) {
  const rows = [];
  let field = '';
  let record = [];
  let inQuotes = false;
  let sawAny = false; // did the current record have any content/field yet?
  const s = String(text == null ? '' : text);

  const endField = () => {
    record.push(field);
    field = '';
  };
  const endRecord = () => {
    endField();
    rows.push(record);
    record = [];
    sawAny = false;
  };

  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (inQuotes) {
      if (ch === '"') {
        if (s[i + 1] === '"') {
          field += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        field += ch;
      }
      sawAny = true;
      continue;
    }
    if (ch === '"') {
      inQuotes = true;
      sawAny = true;
    } else if (ch === ',') {
      endField();
      sawAny = true;
    } else if (ch === '\r') {
      // swallow; the paired \n (or a lone \r) closes the record
      if (s[i + 1] === '\n') i++;
      endRecord();
    } else if (ch === '\n') {
      endRecord();
    } else {
      field += ch;
      sawAny = true;
    }
  }
  // Flush a final record that was not newline-terminated.
  if (sawAny || field.length > 0 || record.length > 0) {
    endRecord();
  }

  if (rows.length === 0) return [];
  const header = rows[0];
  return rows.slice(1).map((cells) => {
    const obj = {};
    header.forEach((key, idx) => {
      obj[key] = idx < cells.length ? cells[idx] : '';
    });
    return obj;
  });
}

function isPublicUrl(value) {
  const v = String(value == null ? '' : value).trim();
  return /^https?:\/\//i.test(v);
}

// Map parsed CSV rows -> deduped, sorted array of { title, url, status }.
// Rows without a usable public http(s) URL are dropped (nothing to publish).
// De-duplicated by URL (first occurrence wins); sorted by URL for stable,
// churn-free diffs across runs.
function toPublicCampaigns(rows) {
  const seen = new Set();
  const out = [];
  for (const row of rows || []) {
    const url = String((row && row.url) == null ? '' : row.url).trim();
    if (!isPublicUrl(url) || seen.has(url)) continue;
    seen.add(url);
    const entry = {};
    for (const f of PUBLIC_FIELDS) {
      entry[f] = String((row && row[f]) == null ? '' : row[f]).trim();
    }
    entry.url = url; // normalized (trimmed) form
    out.push(entry);
  }
  out.sort((a, b) => (a.url < b.url ? -1 : a.url > b.url ? 1 : 0));
  return out;
}

// Full CSV-text -> public campaigns array.
function buildPublicCampaigns(csvText) {
  return toPublicCampaigns(parseCsv(csvText));
}

// Deterministic serialization: 2-space pretty JSON with a trailing newline, so
// the committed file matches what a formatter/diff expects run-to-run.
function serialize(campaigns) {
  return JSON.stringify(campaigns, null, 2) + '\n';
}

module.exports = {
  PUBLIC_FIELDS,
  parseCsv,
  isPublicUrl,
  toPublicCampaigns,
  buildPublicCampaigns,
  serialize,
};
