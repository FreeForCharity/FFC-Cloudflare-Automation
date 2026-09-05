"""End-to-end smoke run of the capture, offline, against a synthetic WordPress.

`capture()` is roughly 750 lines that the script's own `--self-test` never
enters, because that suite exercises pure functions. Two defects have now
shipped through that gap, and the second is the reason this module exists: a
tally was declared beside the pass that fills it in and reported on in an
earlier pass, which is a temporal dead zone `node --check` cannot see. It
surfaced as `ReferenceError: Cannot access 'imageRecode' before
initialization` six minutes into a live crawl of a charity's site.

The fixture replaces `globalThis.fetch` via `node --import`, so the capture
keeps its real request path, redirect handling and timeouts and simply gets
synthetic answers. No listening socket, no DNS, no loopback address — which
matters, because the alternative (an `--origin` override pointed at
127.0.0.1) would mean weakening the private-host guard in a tool that fetches
operator-supplied URLs, to make it testable.

A static use-before-declaration scan was tried first and abandoned: without a
real parser it reported three false positives on the tree it was written
against (an object key and two names inside string literals), and a checker
that cries wolf is not a guard.

The fixture is deliberately a hostile WordPress, reproducing in miniature
every shape this migration has been bitten by: a stale `home` naming a domain
the site does not serve, a front page that redirects there, content hidden
behind JavaScript, a stylesheet only an inline script names, oversized PNGs,
and a reference the origin itself 404s.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "capture-wordpress-api.mjs"
STUB = REPO_ROOT / "tests" / "fixtures" / "wp-fetch-stub.mjs"
NODE = shutil.which("node") or "node"


def run_capture(out_dir: pathlib.Path) -> tuple[subprocess.CompletedProcess, dict | None]:
    """Run the capture against the fixture. Returns (proc, report-or-None)."""
    proc = subprocess.run(
        [
            NODE,
            "--import",
            # A path, not a bare specifier: `--import` resolves relative to the
            # cwd, and this module does not control the cwd of its own runner.
            STUB.as_uri(),
            str(SCRIPT),
            "--domain",
            "fixture.test",
            "--out",
            str(out_dir),
            "--max",
            "50",
            "--delay",
            "0",
            "--include-posts",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        # Never a scrubbed env: node cannot start without the inherited
        # environment on Windows, and a harness that cannot start is
        # indistinguishable from a system under test that failed.
        env=dict(os.environ),
        cwd=str(REPO_ROOT),
        timeout=300,
    )
    report_path = out_dir / "wp-capture-report.json"
    report = None
    if report_path.exists():
        with open(report_path, encoding="utf-8") as fh:
            report = json.load(fh)
    return proc, report


def test_the_capture_runs_to_completion_without_crashing() -> None:
    """The whole of capture() executes. This is the case the ReferenceError failed.

    Asserted on the OUTPUT, not just the exit code: the capture exits 1 for an
    unmet gate as well, and this fixture deliberately fails gates. A crash and
    a gate result must not be able to look the same.
    """
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "site"
        proc, report = run_capture(out)
        combined = proc.stdout + proc.stderr
        assert "ReferenceError" not in combined, combined[-2000:]
        assert "TypeError" not in combined, combined[-2000:]
        assert proc.returncode < 2, f"rc={proc.returncode}\n{combined[-2000:]}"
        assert report is not None, f"no report written\n{combined[-2000:]}"


def test_every_transform_reports_a_decision_rather_than_a_default() -> None:
    """Each pass must say what it did. A zero that means "never ran" is the bug.

    `encoderAvailable` is checked for `is not None` specifically: `None` is the
    initial value and means the image branch was never entered at all, which is
    exactly how a re-encoding pass that silently stopped working would read.
    """
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "site"
        _, report = run_capture(out)
        assert report is not None

        dewp = report["deWordPressed"]
        assert dewp["scriptTagsRemoved"] > 0, dewp
        assert dewp["inlineScriptsRemoved"] > 0, dewp
        assert dewp["headLinksRemoved"] > 0, dewp
        assert dewp["waypointHidingRulesRemoved"] > 0, dewp

        img = report["imageOptimization"]
        assert img["enabled"] is True, img
        assert img["encoderAvailable"] is not None, img
        # Whichever way it went, the oversized images must be accounted for.
        accounted = img["recoded"] + img["declined"] + img["skippedNoEncoder"]
        assert accounted >= 2, img


def test_the_written_pages_carry_no_cms_runtime() -> None:
    """The artifact, not the tally. Counts can be right while the page is wrong."""
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "site"
        _, report = run_capture(out)
        assert report is not None
        page = out / "about-us" / "index.html"
        assert page.exists(), sorted(p.name for p in out.iterdir())
        html = page.read_text(encoding="utf-8")

        # Exactly two scripts: the structured data, and the clone's own runtime.
        assert html.count("<script") == 2, html.count("<script")
        assert "application/ld+json" in html
        assert "clone-enhance.js" in html
        assert "jquery" not in html.lower()
        assert "monsterinsights" not in html.lower()

        # The blank-page trap: content must not depend on JavaScript.
        assert "et-waypoint:not(.et_pb_counters){opacity:0}" not in html

        # WordPress head plumbing, all of it PHP routes a static host cannot serve.
        assert "xmlrpc" not in html
        assert "wp-json" not in html
        assert 'name="generator"' not in html

        # The stylesheet only an inline script named is a real <link> now.
        assert 'rel="stylesheet"' in html
        assert "late.css" in html


def test_the_runtime_it_references_is_actually_written() -> None:
    """A page referencing a 404 runtime is a broken menu no markup check can see."""
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "site"
        run_capture(out)
        runtime = out / "_ffc-assets" / "clone-enhance.js"
        assert runtime.exists(), sorted(p.name for p in (out / "_ffc-assets").iterdir())
        assert runtime.stat().st_size > 0


def test_a_front_page_that_redirects_off_site_is_refused_not_stored() -> None:
    """The defect this migration started from: a 200 says nothing about whose page."""
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "site"
        _, report = run_capture(out)
        assert report is not None
        assert report["frontPageCaptured"] is False
        hops = report["offSiteRedirects"]
        assert any(h["finalUrl"].startswith("https://parked.example") for h in hops), hops
        # And the parked page is not sitting on disk pretending to be the site.
        assert not (out / "index.html").exists()


def test_a_reference_the_origin_404s_is_reproduced_rather_than_gating() -> None:
    """A live site's own broken image must not disqualify it from migrating."""
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "site"
        _, report = run_capture(out)
        assert report is not None
        failures = report["assets"]["failures"]
        assert failures["total"] >= 1, failures
        html = (out / "about-us" / "index.html").read_text(encoding="utf-8")
        assert "gone.png" in html, "the dead reference should survive, not be scrubbed"


def test_the_cms_accessibility_defects_are_corrected() -> None:
    """Zoom and a main landmark. Both are the CMS's output, not the site's words.

    Measured with Lighthouse on the real capture: these two carry weights 10 and
    3, the largest accessibility failures on the page, and fixing them moved the
    score 79 -> 95. What stays failing there is `link-name` and `heading-order`,
    which are the charity's own markup — an `alt=""` on their logo and headings
    that skip a level. A migration that silently rewrote those would no longer
    be a mirror, so it does not.
    """
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "site"
        run_capture(out)
        html = (out / "about-us" / "index.html").read_text(encoding="utf-8")
        assert "user-scalable" not in html, "pinch-zoom must not stay disabled"
        assert "maximum-scale" not in html, "a zoom cap under 5x is a restriction"
        assert "width=device-width" in html, "the useful half of the viewport must survive"
        assert 'role="main"' in html, "a screen reader needs a skip-to-content target"


def test_a_page_link_to_itself_is_brought_home_too() -> None:
    """The header logo points at the site root, so on the FRONT page it is a self-link.

    Skipping self-links left that one absolute, and only on the front page —
    everywhere else the root is a different entry and was rewritten normally.
    588 of 589 pages looked right, which is why it survived the stranded-nav fix.
    """
    with tempfile.TemporaryDirectory() as td:
        out = pathlib.Path(td) / "site"
        run_capture(out)
        html = (out / "about-us" / "index.html").read_text(encoding="utf-8")
        assert 'href="https://fixture.test/about-us/"' not in html, html[:400]
        assert 'href="../about-us/"' in html


def _tests() -> list:
    return [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]


def main() -> int:
    failures = 0
    for fn in _tests():
        try:
            fn()
            print(f"  PASS {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a crash is a failure, and must say so
            failures += 1
            print(f"  FAIL {fn.__name__}: harness error: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
