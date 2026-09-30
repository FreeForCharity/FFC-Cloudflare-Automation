"""Guards for the published sites list's row ordering (#1016).

`scripts/sites-list-order.mjs` owns the ordering 703's generator applies to
sites-list/sites_list.{csv,json}: live GitHub Pages sites first, departed or
unidentifiable domains last, and within a group Work Tier, then recency, then
.org/.com pairs kept together.

The fixtures use the column values the current export actually emits
(`Host Category` = `GitHub Pages` / `Unresolved/Parked`, `Site Health` =
`Live` / `Unreachable` / `Redirect`, `Left FFC` = `Yes`), not the 2026-06 schema
the comparator was first written against.

Run: python3 tests/workflow-logic/test_sites_list_order.py
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIB = REPO_ROOT / "scripts" / "sites-list-order.mjs"
GENERATOR = REPO_ROOT / "scripts" / "update-sites-data.mjs"
NODE = shutil.which("node") or "node"


def _node(expr_body: str, payload: object) -> object:
    # ESM module, so load it with a dynamic import. encoding="utf-8" for the
    # same cp1252 reason test_737_claim_sync.py documents.
    code = (
        f"import({json.dumps(LIB.as_uri())}).then((l)=>{{"
        "const input=JSON.parse(process.argv[1]);"
        f"{expr_body}"
        "}).catch((e)=>{console.error(e);process.exit(2);});"
    )
    proc = subprocess.run(
        [NODE, "-e", code, json.dumps(payload)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(f"node failed (rc={proc.returncode}): {proc.stderr}")
    return json.loads(proc.stdout)


def rank(row: dict) -> int:
    return _node("process.stdout.write(JSON.stringify(l.groupRank(input)));", row)


def order(rows: list[dict]) -> list[str]:
    return _node(
        "process.stdout.write(JSON.stringify(input.slice().sort(l.compareRows).map(r=>r.Domain)));",
        rows,
    )


def row(domain: str, **cols: str) -> dict:
    base = {
        "Domain": domain,
        "Host Category": "Hostinger",
        "Server In Use": "",
        "Site Health": "Live",
        "Left FFC": "",
        "Status": "Active",
        "Work Tier": "3",
        "Last PR Closed": "",
        "_leadDomain": domain,
        "_isFollower": False,
    }
    base.update(cols)
    return base


LIVE_PAGES = row("pages.org", **{"Host Category": "GitHub Pages"})
MIDDLE = row("hostinger.org")
PARKED = row("parked.org", **{"Host Category": "Unresolved/Parked", "Site Health": "Unreachable"})
LEFT = row("left.org", **{"Left FFC": "Yes", "Site Health": "Live"})


def test_group_ranks_match_current_export_values():
    assert rank(LIVE_PAGES) == 0, "live site on GitHub Pages (Host Category) must rank 0"
    assert rank(row("p2.org", **{"Server In Use": "GitHub Pages"})) == 0, (
        "Server In Use = GitHub Pages must count as on Pages too"
    )
    assert rank(MIDDLE) == 1
    assert rank(PARKED) == 2
    assert rank(LEFT) == 3


def test_pages_site_that_is_not_live_is_not_promoted():
    down = row("down.org", **{"Host Category": "GitHub Pages", "Site Health": "Unreachable"})
    assert rank(down) == 2, "an unreachable Pages site is not 'stable + live' and must not float up"


def test_left_ffc_outranks_unidentified_for_the_bottom():
    both = row("gone.org", **{"Left FFC": "Yes", "Host Category": "Unresolved/Parked"})
    assert rank(both) == 3


def test_primary_group_split_beats_input_order_and_work_tier():
    # Input deliberately reversed, and the departed row given the most urgent
    # Work Tier: the group split must still win.
    rows = [
        dict(LEFT, **{"Work Tier": "1"}),
        PARKED,
        MIDDLE,
        dict(LIVE_PAGES, **{"Work Tier": "5"}),
    ]
    assert order(rows) == ["pages.org", "hostinger.org", "parked.org", "left.org"]


def test_within_group_tier_then_recency_then_pairing():
    rows = [
        row("b.org", **{"Work Tier": "2", "Last PR Closed": "2026-01-01"}),
        row("a.org", **{"Work Tier": "2", "Last PR Closed": "2026-09-01"}),
        row("z.org", **{"Work Tier": "1"}),
        row("pair.com", _leadDomain="pair.org", _isFollower=True, **{"Work Tier": "4"}),
        row("pair.org", **{"Work Tier": "4"}),
    ]
    assert order(rows) == ["z.org", "a.org", "b.org", "pair.org", "pair.com"]


def test_sorting_is_idempotent():
    rows = [LEFT, MIDDLE, PARKED, LIVE_PAGES, row("x.org", **{"Work Tier": "1"})]
    once = order(rows)
    by_domain = {r["Domain"]: r for r in rows}
    twice = order([by_domain[d] for d in once])
    assert once == twice


def test_generator_uses_the_shared_comparator():
    src = GENERATOR.read_text(encoding="utf-8")
    assert "from './sites-list-order.mjs'" in src, (
        "update-sites-data.mjs no longer imports sites-list-order.mjs -- the tested ordering "
        "would not be the one 703 publishes"
    )
    assert "mergedData.sort(compareRows)" in src


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:400]}")
    sys.exit(1 if failures else 0)
