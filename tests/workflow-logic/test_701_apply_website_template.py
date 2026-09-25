"""Tests for scripts/Apply-WebsiteReactTemplate.ps1 on config-driven templates.

701's content job runs this script against the freshly created repo. Both
current FFC templates (FFC-IN-Footer_Only_Template, the default, and
FFC-IN-FFC_Single_Page_Template) render the footer from `siteConfig` in
src/lib/site.config.ts and type team members as { name, role, linkedinUrl }.
The script's older regex footer patch matched nothing there and wrote
{ title, imageUrl } team JSON that fails the TypeScript build, while the
content step still reported success. These tests pin the config-driven path
against a fixture repo shaped like the templates, so no network is needed.
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
SCRIPT = ROOT / "scripts" / "Apply-WebsiteReactTemplate.ps1"

SITE_CONFIG = """export type SiteConfig = {
  name: string
  parentOrg?: { name: string; url: string; hubUrl: string }
}

export const siteConfig: SiteConfig = {
  name: 'Free For Charity',
  tagline: 'Reduce Costs, Increase Impact',
  mission:
    'Free For Charity connects students, professionals, and businesses with nonprofits.',
  // Empty = the footer's Donate / Volunteer links email contactEmail instead.
  donationUrl: '',
  volunteerUrl: '',
  description:
    'Free For Charity connects students {with braces} and "quotes" to nonprofits.',
  shortDescription: 'Connecting students with nonprofits.',
  url: 'https://ffcworkingsite1.org',
  twitterHandle: '@freeforcharity',
  contactEmail: 'clarkemoyer@freeforcharity.org',
  keywords: ['nonprofit', 'charity'],
  social: [
    { label: 'Facebook', href: 'https://www.facebook.com/freeforcharity' },
    // Repo name uses underscores, the hyphenated variant 404s.
    { label: 'GitHub', href: 'https://github.com/FreeForCharity/FFC-IN-Footer_Only_Template' },
  ],
  ein: '46-2471893',
  phone: { display: '(520) 222-8104', tel: '5202228104' },
  addresses: [
    {
      label: 'Main Address',
      lines: ['4030 Wake Forrest Road', 'Raleigh NC 27609'],
      mapUrl: 'https://www.google.com/maps/search/?api=1&query=4030+Wake+Forrest',
    },
  ],
  guidestar: {
    profileUrl: 'https://www.guidestar.org/profile/46-2471893',
    directProfileUrl: 'https://www.guidestar.org/profile/shared/bbbe173a',
  },
  supportedBy: {
    name: 'Free For Charity',
    url: 'https://freeforcharity.org',
    hubUrl: 'https://freeforcharity.org/hub/',
  },
  parentOrg: {
    name: 'Free For Charity',
    url: 'https://freeforcharity.org',
    hubUrl: 'https://freeforcharity.org/hub/',
  },
}

export function sitePath(path = '/'): string {
  return path
}
"""

# Shaped like the Single Page template's team.ts, which exports more than
# `team`: those extra exports must survive the rewrite.
TEAM_TS = """// Team member data

import clarkeMoyer from './team/clarke-moyer.json'
import chrisRae from './team/chris-rae.json'

export type TeamMember = {
  name: string
  role: string
  linkedinUrl?: string
}

export const team: TeamMember[] = [
  clarkeMoyer,
  chrisRae,
]

export const configuredTeam: TeamMember[] = team.filter((member) => member.name.trim())
"""

SECURITY_TXT = "Contact: mailto:clarkemoyer@freeforcharity.org\nExpires: 2027-01-01T00:00:00.000Z\n"

FULL_ARGS = {
    "Domain": "helpinghands.org",
    "CharityName": "St. Mary's Shelter",
    "FooterEmail": "info@helpinghands.org",
    "FooterPhone": "(555) 123-4567",
    "FooterAddress": "12 Main St\nSpringfield, IL 62701",
    "FooterEin": "12-3456789",
    "GuideStarProfileUrl": "",
    "GuideStarDirectProfileUrl": "",
    "FooterSocial": [
        "Facebook: https://www.facebook.com/helpinghands",
        "X: https://x.com/helpinghands",
    ],
    "LeadershipLines": [
        "President - Jane O'Doe",
        "Jim Roe | Treasurer | https://www.linkedin.com/in/jimroe",
    ],
    "Mission": "We shelter families.\nEvery night.",
    "DonationUrl": "https://www.zeffy.com/donate/helping-hands",
    "VolunteerUrl": "http://not-https.example.org",
}


def make_repo(td: pathlib.Path, site_config: str = SITE_CONFIG) -> pathlib.Path:
    repo = td / "repo"
    (repo / "src" / "lib").mkdir(parents=True)
    (repo / "src" / "data" / "team").mkdir(parents=True)
    (repo / "public" / ".well-known").mkdir(parents=True)
    (repo / "src" / "lib" / "site.config.ts").write_text(site_config, encoding="utf-8", newline="\n")
    (repo / "src" / "data" / "team.ts").write_text(TEAM_TS, encoding="utf-8", newline="\n")
    for slug in ("clarke-moyer", "chris-rae"):
        (repo / "src" / "data" / "team" / f"{slug}.json").write_text(
            '{"name": "X", "role": "Y"}\n', encoding="utf-8"
        )
    for rel in ("public/security.txt", "public/.well-known/security.txt"):
        (repo / rel).write_text(SECURITY_TXT, encoding="utf-8", newline="\n")
    return repo


def ps_literal(value) -> str:
    if isinstance(value, list):
        return "@(" + ", ".join(ps_literal(v) for v in value) + ")"
    return "'" + str(value).replace("'", "''") + "'"


def run_apply(repo: pathlib.Path, args: dict) -> subprocess.CompletedProcess:
    # Called the way 701 calls it: in-process, with real string arrays. `pwsh
    # -File` would flatten an array argument into one string.
    params = " ".join(f"-{k} {ps_literal(v)}" for k, v in args.items())
    wrapper = repo.parent / "invoke.ps1"
    wrapper.write_text(
        f"& {ps_literal(str(SCRIPT))} -RepoPath {ps_literal(str(repo))} {params}\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def applied(args: dict = FULL_ARGS, site_config: str = SITE_CONFIG):
    td = pathlib.Path(tempfile.mkdtemp())
    repo = make_repo(td, site_config)
    proc = run_apply(repo, args)
    return td, repo, proc


def read(repo: pathlib.Path, rel: str) -> str:
    return (repo / rel).read_bytes().decode("utf-8")


def test_writes_the_charity_identity_into_site_config():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Config-driven template detected" in proc.stdout, proc.stdout
        cfg = read(repo, "src/lib/site.config.ts")
        assert "name: 'St. Mary\\'s Shelter'," in cfg, cfg
        # Multi-line input collapses to the single footer sentence, and the
        # template's FFC description is replaced by it too.
        assert cfg.count("'We shelter families. Every night.'") == 3, cfg
        assert "contactEmail: 'info@helpinghands.org'," in cfg, cfg
        assert "ein: '12-3456789'," in cfg, cfg
        assert "phone: { display: '(555) 123-4567', tel: '15551234567' }," in cfg, cfg
        assert "lines: ['12 Main St', 'Springfield, IL 62701']," in cfg, cfg
        assert "twitterHandle: '@helpinghands'," in cfg, cfg
        assert "{ label: 'X (Twitter)', href: 'https://x.com/helpinghands' }," in cfg, cfg
        # Nothing of FFC's identity remains outside the permanent attribution.
        assert "46-2471893" not in cfg and "clarkemoyer@" not in cfg, cfg
        assert "freeforcharity1" not in cfg and "(520) 222-8104" not in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_donate_and_volunteer_urls_only_accept_https():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "donationUrl: 'https://www.zeffy.com/donate/helping-hands'," in cfg, cfg
        # Not https: left blank, so the template's footer link emails the charity.
        assert "volunteerUrl: ''," in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_blank_mission_becomes_a_sentence_naming_the_charity():
    td, repo, proc = applied({**FULL_ARGS, "Mission": ""})
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "mission: 'St. Mary\\'s Shelter is a nonprofit organization.'," in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_blank_candid_urls_derive_from_the_ein_not_ffc():
    # The shared SiteConfig schema requires both URLs, so blanks cannot be
    # written; FFC's own profile must not be left on the charity's footer.
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "profileUrl: 'https://www.guidestar.org/profile/12-3456789'," in cfg, cfg
        assert "directProfileUrl: 'https://www.guidestar.org/profile/12-3456789'," in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_keeps_ffc_attribution_and_drops_the_parent_org():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        cfg = read(repo, "src/lib/site.config.ts")
        assert "supportedBy: {\n    name: 'Free For Charity'," in cfg, cfg
        assert "parentOrg:" not in cfg.split("export const siteConfig")[1], cfg
        # Untouched keys, comments and code after the literal survive.
        assert "keywords: ['nonprofit', 'charity']," in cfg, cfg
        assert "// Empty = the footer's Donate" in cfg, cfg
        assert "export function sitePath(path = '/'): string {" in cfg, cfg
    finally:
        shutil.rmtree(td)


def test_team_data_uses_the_role_schema_and_keeps_other_exports():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        team_dir = repo / "src" / "data" / "team"
        files = sorted(p.name for p in team_dir.glob("*.json"))
        assert files == ["jane-o-doe.json", "jim-roe.json"], files
        jane = json.loads(read(repo, "src/data/team/jane-o-doe.json"))
        assert jane == {"name": "Jane O'Doe", "role": "President"}, jane
        jim = json.loads(read(repo, "src/data/team/jim-roe.json"))
        assert jim == {
            "name": "Jim Roe",
            "role": "Treasurer",
            "linkedinUrl": "https://www.linkedin.com/in/jimroe",
        }, jim
        team_ts = read(repo, "src/data/team.ts")
        assert "import member1 from './team/jane-o-doe.json'" in team_ts, team_ts
        assert "clarke-moyer" not in team_ts, team_ts
        assert "export const team: TeamMember[] = [\n  member1,\n  member2,\n]" in team_ts, team_ts
        assert "export type TeamMember = {" in team_ts, team_ts
        assert "export const configuredTeam" in team_ts, team_ts
    finally:
        shutil.rmtree(td)


def test_no_usable_leadership_fails_rather_than_keeping_ffcs_team():
    # 701 counts raw lines, so lines that parse to no name still reach the
    # script. Keeping the template's sample team would publish FFC's people;
    # an empty team breaks the templates' own tests. Fail loudly instead.
    td, repo, proc = applied({**FULL_ARGS, "LeadershipLines": ["| Treasurer", "|"]})
    try:
        assert proc.returncode != 0, proc.stdout
        assert "No usable leadership lines" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(td)


def test_a_blank_ein_fails_rather_than_keeping_ffcs():
    td, repo, proc = applied({**FULL_ARGS, "FooterEin": "  "})
    try:
        assert proc.returncode != 0, proc.stdout
        assert "No EIN supplied" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    finally:
        shutil.rmtree(td)


def test_security_txt_contact_follows_the_contact_email():
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        for rel in ("public/security.txt", "public/.well-known/security.txt"):
            body = read(repo, rel)
            assert body.startswith("Contact: mailto:info@helpinghands.org\n"), body
            assert "Expires: 2027-01-01" in body, body
    finally:
        shutil.rmtree(td)


def test_written_files_are_lf_only():
    # The content job runs on windows-latest; the templates enforce LF.
    td, repo, proc = applied()
    try:
        assert proc.returncode == 0, proc.stdout + proc.stderr
        for rel in ("src/lib/site.config.ts", "src/data/team.ts", "public/security.txt"):
            assert b"\r" not in (repo / rel).read_bytes(), rel
    finally:
        shutil.rmtree(td)


def test_a_template_shape_change_fails_loudly():
    # A missing required key must throw (content_status=failed) rather than
    # silently ship FFC's value on the charity's site.
    td, repo, proc = applied(site_config=SITE_CONFIG.replace("  ein: '46-2471893',\n", ""))
    try:
        assert proc.returncode != 0, proc.stdout
        assert "siteConfig has no 'ein' key" in proc.stdout + proc.stderr, proc.stdout + proc.stderr
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
