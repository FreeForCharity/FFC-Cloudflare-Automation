'use strict';

// Thin CLI wrapper around scripts/zeffy-public-json-lib.js.
//
// Usage:
//   node scripts/zeffy-campaigns-to-public-json.js <input.csv> <output.json>
//
// Reads the CSV produced by scripts/zeffy-campaigns-export.ps1 and writes the
// public, PII-free campaign list (title/url/status only). Used by the `deliver`
// job of .github/workflows/401-zeffy-campaigns-export.yml; kept trivial so the
// transform logic lives (and is unit-tested) in the library.

const fs = require('fs');
const path = require('path');
const { buildPublicCampaigns, serialize } = require('./zeffy-public-json-lib');

function main(argv) {
  const input = argv[2];
  const output = argv[3];
  if (!input || !output) {
    console.error(
      'Usage: node scripts/zeffy-campaigns-to-public-json.js <input.csv> <output.json>',
    );
    return 2;
  }
  const csvText = fs.readFileSync(input, 'utf8');
  const campaigns = buildPublicCampaigns(csvText);
  fs.mkdirSync(path.dirname(output), { recursive: true });
  fs.writeFileSync(output, serialize(campaigns), 'utf8');
  console.log(`Wrote ${campaigns.length} public Zeffy campaign(s) -> ${output}`);
  return 0;
}

process.exit(main(process.argv));
