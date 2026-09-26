"""scripts/analytics-wire.ps1 (workflow 704) against both template shapes.

The two FFC templates keep the GTM container id in different shapes:

- Single Page: `export const analyticsConfig = { gtmId: '...', gaMeasurementId: '...' }`
- Footer-Only: `export const GTM_ID: string = '...'` (GA4 fires inside GTM)

The script only knew the first. On a Footer-Only repo it matched nothing,
changed nothing, and 704 reported "Already wired" while the site kept FFC's
container. These cases run the real script against a fixture of each shape.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from wf_extract import child_env

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "analytics-wire.ps1"
OLD = "GTM-TQ5H8HPR"
NEW = "GTM-ABC1234"

SINGLE_PAGE_CONFIG = f"""export const analyticsConfig = {{
  gtmId: '{OLD}',

  gaMeasurementId: 'G-XXXXXXXXXX',

  metaPixelId: 'XXXXXXXXXXXXXXX',
}} as const
"""

FOOTER_ONLY_CONFIG = f"""/**
 * Google Tag Manager container ID. GA4 fires inside the container.
 */
export const GTM_ID: string = '{OLD}'
"""

FOOTER_ONLY_COMPONENT = """import Script from 'next/script'
import { GTM_ID } from '@/lib/analytics.config'

export default function GoogleTagManager() {
  return <Script id="gtm">{GTM_ID}</Script>
}
"""


def make_repo(td: pathlib.Path, config: str | None, component: str | None = None) -> pathlib.Path:
    repo = td / "repo"
    (repo / "src" / "lib").mkdir(parents=True)
    (repo / "src" / "components" / "google-tag-manager").mkdir(parents=True)
    (repo / "__tests__").mkdir()
    (repo / "package.json").write_text('{"name": "site"}\n', encoding="utf-8")
    if config is not None:
        (repo / "src" / "lib" / "analytics.config.ts").write_text(config, encoding="utf-8")
    if component is not None:
        (repo / "src" / "components" / "google-tag-manager" / "index.tsx").write_text(
            component, encoding="utf-8"
        )
    (repo / "__tests__" / "gtm.test.ts").write_text(
        f"expect(GTM_ID).toBe('{OLD}')\n", encoding="utf-8"
    )
    return repo


def wire(repo: pathlib.Path, measurement: str = "") -> subprocess.CompletedProcess:
    args = [
        "pwsh", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT),
        "-RepoDir", str(repo), "-Domain", "example.org", "-GtmId", NEW,
    ]
    if measurement:
        args += ["-MeasurementId", measurement]
    return subprocess.run(
        args, env=child_env(), capture_output=True, text=True, encoding="utf-8", timeout=120
    )


def summary(proc: subprocess.CompletedProcess) -> dict:
    start = proc.stdout.index("{")
    return json.loads(proc.stdout[start:])


def test_footer_only_constant_is_rewired_and_reported_as_a_change():
    td = pathlib.Path(tempfile.mkdtemp())
    try:
        repo = make_repo(td, FOOTER_ONLY_CONFIG, FOOTER_ONLY_COMPONENT)
        proc = wire(repo)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = (repo / "src" / "lib" / "analytics.config.ts").read_text(encoding="utf-8")
        assert f"export const GTM_ID: string = '{NEW}'" in cfg, cfg
        assert OLD not in cfg, cfg
        s = summary(proc)
        assert s["changed"] is True, s
        # The component already reads GTM_ID from config and is left alone.
        comp = (repo / "src" / "components" / "google-tag-manager" / "index.tsx").read_text(
            encoding="utf-8"
        )
        assert comp == FOOTER_ONLY_COMPONENT, comp
        # The site's own tests follow the new id, as they do for Single Page.
        assert NEW in (repo / "__tests__" / "gtm.test.ts").read_text(encoding="utf-8")
    finally:
        shutil.rmtree(td)


def test_single_page_object_is_still_rewired():
    td = pathlib.Path(tempfile.mkdtemp())
    try:
        repo = make_repo(td, SINGLE_PAGE_CONFIG)
        proc = wire(repo, "G-AB12CD34EF")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = (repo / "src" / "lib" / "analytics.config.ts").read_text(encoding="utf-8")
        assert f"gtmId: '{NEW}'" in cfg and "gaMeasurementId: 'G-AB12CD34EF'" in cfg, cfg
        assert summary(proc)["changed"] is True
    finally:
        shutil.rmtree(td)


def test_an_unrecognized_config_shape_fails_loudly_instead_of_reporting_wired():
    td = pathlib.Path(tempfile.mkdtemp())
    try:
        repo = make_repo(td, "export const somethingElse = 'GTM-TQ5H8HPR'\n")
        proc = wire(repo)
        assert proc.returncode != 0, proc.stdout
        assert "neither a gtmId: key nor an exported GTM_ID constant" in proc.stdout + proc.stderr, (
            proc.stdout + proc.stderr
        )
    finally:
        shutil.rmtree(td)


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    if shutil.which("pwsh") is None:
        print("  SKIP all (pwsh not installed; runs in CI)")
        sys.exit(0)
    failures = 0
    for t in TESTS:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL {t.__name__}: {str(e)[:600]}")
    sys.exit(1 if failures else 0)
