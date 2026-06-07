import fs from 'fs';
import path from 'path';
import { parse } from 'csv-parse/sync';
import { stringify } from 'csv-stringify/sync';

// Source of truth lives in this repo (the one that owns the WHMCS / Cloudflare /
// WPMUDEV environments). The export workflows produce per-source CSV artifacts;
// this script merges them with the human-curated base list, runs health checks,
// and writes both a CSV (human/spreadsheet friendly) and a JSON (structured)
// artifact. Downstream consumers (e.g. ffcadmin.org) pull the published files.

// Curated base list (preserves Section / Server In Use / Notes / Priority, etc.)
const SITES_LIST_PATH = 'sites-list/sites_list.csv';

// Per-source export artifacts are downloaded by the workflow into
// tmp_data/<artifact-name>/. The exact nesting depends on the upload path, so we
// resolve each file by name under its directory rather than assuming a fixed path.
const WHMCS_DIR = 'tmp_data/whmcs_domains';
const CF_DIR = 'tmp_data/domain_summary';
const WPMUDEV_DIR = 'tmp_data/wpmudev-domain-inventory';

const OUTPUT_CSV_PATH = 'sites-list/sites_list.csv';
const OUTPUT_JSON_PATH = 'sites-list/sites_list.json';

// Find a file by name anywhere under dir (the export artifact may be nested under
// the original upload path, e.g. artifacts/whmcs/whmcs_domains.csv). Returns the
// first match or null.
function resolveSource(dir, filename) {
  if (!fs.existsSync(dir)) return null;
  const stack = [dir];
  while (stack.length) {
    const cur = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(cur, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const e of entries) {
      const full = path.join(cur, e.name);
      if (e.isDirectory()) stack.push(full);
      else if (e.name === filename) return full;
    }
  }
  return null;
}

function readCSV(filePath) {
  if (!filePath || !fs.existsSync(filePath)) return [];
  const content = fs.readFileSync(filePath, 'utf-8');
  return parse(content, { columns: true, skip_empty_lines: true, trim: true });
}

// WHMCS exports a normalized `domain` column (always present) plus `domainname`.
const whmcsKey = (d) => (d.domain || d.domainname || '').toLowerCase();

// Health check: probe each domain over HTTPS, classifying the response.
async function checkSiteAvailability(domain) {
  if (!domain) return 'Unknown';
  const url = `https://${domain}`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 5000); // 5s timeout
  try {
    const response = await fetch(url, {
      method: 'GET',
      signal: controller.signal,
      redirect: 'manual', // detect redirects rather than following them
    });
    // We only need the status line; release the socket promptly instead of
    // leaving the streamed body open across hundreds of checks.
    response.body?.cancel?.().catch(() => {});
    if (response.status === 200) return 'Live';
    if (response.status >= 300 && response.status < 400) return 'Redirect';
    if (response.status >= 400) return 'Error';
    return 'Unknown';
  } catch {
    return 'Unreachable'; // timeout or connection error
  } finally {
    clearTimeout(timeoutId); // always clear, even on throw/abort
  }
}

async function main() {
  console.log('Reading data...');
  const whmcsPath = resolveSource(WHMCS_DIR, 'whmcs_domains.csv');
  const cfPath = resolveSource(CF_DIR, 'domain_summary.csv');
  const wpmudevPath = resolveSource(WPMUDEV_DIR, 'wpmudev_domains.csv');

  const currentSites = readCSV(SITES_LIST_PATH);
  const whmcsData = readCSV(whmcsPath);
  const cfData = readCSV(cfPath);
  const wpmudevData = readCSV(wpmudevPath);

  // Track which upstream exports are actually present. When a source is missing
  // (an export workflow failed, or a local run without artifacts) we PRESERVE the
  // existing membership flags rather than overwrite every domain with "No" and
  // silently wipe the WHMCS / Cloudflare / WPMUDEV columns.
  const hasWhmcs = !!whmcsPath;
  const hasCf = !!cfPath;
  const hasWpmudev = !!wpmudevPath;
  console.log(
    `Sources detected -> WHMCS: ${hasWhmcs}, Cloudflare: ${hasCf}, WPMUDEV: ${hasWpmudev}`,
  );
  if (!hasWhmcs && !hasCf && !hasWpmudev) {
    console.log('No upstream exports found: preserving membership flags, refreshing health only.');
  }

  // Indexing for O(1) lookup (keyed by normalized domain).
  const manualMap = new Map(
    currentSites.filter((s) => s['Domain']).map((s) => [s['Domain'].toLowerCase(), s]),
  );
  const whmcsMap = new Map(whmcsData.filter(whmcsKey).map((d) => [whmcsKey(d), d]));
  const cfMap = new Map(cfData.filter((d) => d.zone).map((d) => [d.zone.toLowerCase(), d]));
  const wpmudevMap = new Map(
    wpmudevData.filter((d) => d.domain).map((d) => [d.domain.toLowerCase(), d]),
  );

  const allDomains = new Set(
    [
      ...currentSites.map((s) => s['Domain']),
      ...whmcsData.map(whmcsKey),
      ...cfData.map((d) => d.zone),
      ...wpmudevData.map((d) => d.domain),
    ]
      .filter(Boolean)
      .map((d) => d.toLowerCase()),
  );

  let mergedData = [];
  console.log(`Processing ${allDomains.size} domains including health checks...`);

  const domainsArray = Array.from(allDomains);
  const chunkSize = 10;
  for (let i = 0; i < domainsArray.length; i += chunkSize) {
    const chunk = domainsArray.slice(i, i + chunkSize);
    await Promise.all(
      chunk.map(async (domain) => {
        const manualEntry = manualMap.get(domain) || {};
        const whmcsEntry = whmcsMap.get(domain);
        const cfEntry = cfMap.get(domain);
        const wpmudevEntry = wpmudevMap.get(domain);

        const healthStatus = await checkSiteAvailability(domain);

        const newItem = {
          Section: manualEntry['Section'] || 'Unknown',
          Domain: domain,
          Status: whmcsEntry?.status || manualEntry['Status'] || 'Unknown',
          // Only derive a flag from a source when that source export is present;
          // otherwise keep the existing value so a missing export never wipes it.
          'In WHMCS': hasWhmcs ? (whmcsEntry ? 'Yes' : 'No') : manualEntry['In WHMCS'] || 'No',
          'In Cloudflare': hasCf ? (cfEntry ? 'Yes' : 'No') : manualEntry['In Cloudflare'] || 'No',
          'In WPMUDEV': hasWpmudev
            ? wpmudevEntry
              ? 'Yes'
              : 'No'
            : manualEntry['In WPMUDEV'] || 'No',
          // Preserved curated columns
          'Server In Use': manualEntry['Server In Use'] || '',
          'Old Server Abandoned?': manualEntry['Old Server Abandoned?'] || '',
          Notes: manualEntry['Notes'] || '',
          'Cloudflare IP': cfEntry
            ? cfEntry.apex_a_ips || '(no A record)'
            : manualEntry['Cloudflare IP'] || '(no A record)',
          'Is In Cloudflare': hasCf
            ? cfEntry
              ? 'Yes'
              : 'No'
            : manualEntry['Is In Cloudflare'] || 'No',
          'Repo URL': manualEntry['Repo URL'] || manualEntry['URL'] || '',
          'Site Health': healthStatus,
        };
        newItem['Priority'] = manualEntry['Priority'] || 'Standard';

        mergedData.push(newItem);
      }),
    );
    if ((i + chunkSize) % 50 === 0) {
      console.log(
        `Processed ${Math.min(i + chunkSize, domainsArray.length)} / ${domainsArray.length} sites...`,
      );
    }
  }

  // Pairing logic: .org leads, matching .com follows and inherits priority.
  const orgDomains = new Set(
    mergedData.filter((d) => d.Domain.endsWith('.org')).map((d) => d.Domain.replace('.org', '')),
  );
  mergedData.forEach((item) => {
    const baseName = item.Domain.substring(0, item.Domain.lastIndexOf('.'));
    const tld = item.Domain.split('.').pop();
    if (tld === 'com' && orgDomains.has(baseName)) {
      item._isFollower = true;
      item._leadDomain = `${baseName}.org`;
    } else {
      item._isFollower = false;
      item._leadDomain = item.Domain;
    }
  });

  const domainMap = new Map(mergedData.map((d) => [d.Domain, d]));
  mergedData.forEach((item) => {
    if (item._isFollower) {
      const lead = domainMap.get(item._leadDomain);
      if (lead) item['Priority'] = lead['Priority'];
    }
  });

  const priorityOrder = { Highest: 1, High: 2, Standard: 3, Low: 4 };
  mergedData.sort((a, b) => {
    const pA = priorityOrder[a['Priority']] || 99;
    const pB = priorityOrder[b['Priority']] || 99;
    if (pA !== pB) return pA - pB;
    if (a._leadDomain < b._leadDomain) return -1;
    if (a._leadDomain > b._leadDomain) return 1;
    if (a._isFollower !== b._isFollower) return a._isFollower ? 1 : -1;
    return 0;
  });

  mergedData.forEach((d) => {
    delete d._isFollower;
    delete d._leadDomain;
  });

  console.log(
    `Writing ${mergedData.length} records to ${OUTPUT_CSV_PATH} and ${OUTPUT_JSON_PATH}...`,
  );
  fs.writeFileSync(OUTPUT_CSV_PATH, stringify(mergedData, { header: true }));
  fs.writeFileSync(OUTPUT_JSON_PATH, JSON.stringify(mergedData, null, 2) + '\n');
  console.log('Done.');
}

main();
