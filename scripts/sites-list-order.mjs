// Row ordering for the published sites list (sites-list/sites_list.{csv,json}).
//
// Split out of update-sites-data.mjs so the ordering can be tested without
// running the generator (#1016). Ported from #425's b1fcc0e with every column
// reference re-checked against the current export schema.
//
//   1. Primary group: stable, live GitHub Pages sites first; domains that have
//      left FFC, or that we cannot identify, sink to the bottom.
//   2. Within a group: Work Tier (most actionable first), then most-recent
//      activity, then .org/.com pairs kept together by lead domain.

const tierNum = (d) => parseInt(d['Work Tier'], 10) || 9;

const onGitHubPages = (d) =>
  /github pages/i.test(d['Host Category'] || '') || /github pages/i.test(d['Server In Use'] || '');

// Lower rank sorts higher in the list.
export function groupRank(d) {
  const health = d['Site Health'] || '';
  if (onGitHubPages(d) && health === 'Live') return 0; // stable + live on GitHub Pages
  if (d['Left FFC'] === 'Yes') return 3; // left FFC: very bottom
  const status = (d['Status'] || '').toLowerCase();
  const unidentified =
    health === 'Unknown' ||
    health === 'Unreachable' ||
    /unresolved|parked/i.test(d['Host Category'] || '') ||
    (status === 'unknown' && health !== 'Live' && health !== 'Redirect');
  if (unidentified) return 2; // cannot identify: bottom
  return 1; // everything else: middle
}

// Comparator for Array.prototype.sort. Rows carry the generator's private
// `_leadDomain` / `_isFollower` pairing fields while sorting.
export function compareRows(a, b) {
  const gA = groupRank(a);
  const gB = groupRank(b);
  if (gA !== gB) return gA - gB;
  const tA = tierNum(a);
  const tB = tierNum(b);
  if (tA !== tB) return tA - tB;
  const rA = a['Last PR Closed'] || '';
  const rB = b['Last PR Closed'] || '';
  if (rA !== rB) return rB.localeCompare(rA); // newer PR date first
  if (a._leadDomain < b._leadDomain) return -1;
  if (a._leadDomain > b._leadDomain) return 1;
  if (a._isFollower !== b._isFollower) return a._isFollower ? 1 : -1;
  return 0;
}
